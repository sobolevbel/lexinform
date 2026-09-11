"""The daily run end to end on fakes: discovery, prefilter, analysis, publishing, reporting."""

import datetime as dt

import pytest

from lexinform.models import BillStatus, Category, DocumentType, PublicationStatus
from tests.fakes import FakeTextExtractor, make_analysis
from tests.harness import COMMITTEE_STAGES, World, summary

# --------------------------------------------------------------------------- happy path


def test_relevant_bill_is_analysed_and_published_once() -> None:
    w = World()
    w.add_bill("3039", "Poselski projekt ustawy o zmianie ustawy o cudzoziemcach")
    w.add_bill("4000", "Rządowy projekt ustawy o podatku VAT")  # neither title nor text match

    report = w.run()

    assert (report.discovered, report.prefilter_hits, report.analyzed, report.published) == (
        2,
        1,
        1,
        1,
    )
    assert [b.number for b, _ in w.publisher.new_bills] == ["3039"]
    assert w.bill("4000").status is BillStatus.SKIPPED_TEXT_PREFILTER
    assert (report.text_prefilter_checked, report.text_prefilter_hits) == (1, 0)
    assert [c.text_source for c in w.llm.contexts] == ["pdf"]


def test_second_run_publishes_nothing_new() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()

    report = w.run()

    assert (report.published, report.analyzed) == (0, 0)
    assert len(w.publisher.new_bills) == 1


@pytest.mark.parametrize("bad_type", [DocumentType.DRAFT_RESOLUTION, DocumentType.OTHER])
def test_non_bills_are_ignored(bad_type: DocumentType) -> None:
    w = World()
    resolution = summary("5", "Projekt uchwały o cudzoziemcach").model_copy(
        update={"document_type_enum": bad_type}
    )
    w.gateway.processes.append(resolution)

    report = w.run()

    assert report.discovered == 0


# --------------------------------------------------------------------------- publish rules


def test_irrelevant_analysis_is_not_published_and_listed_as_rejected() -> None:
    w = World(llm_script={"3039": make_analysis(relevant=False, score=1, category=Category.NONE)})
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    report = w.run()

    assert (report.analyzed, report.published) == (1, 0)
    assert [(v.number, v.reason) for v in report.rejected] == [("3039", "not relevant")]


def test_bills_below_min_score_are_not_published() -> None:
    w = World(llm_script={"3039": make_analysis(score=2, category=Category.INDIRECT)})
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.add_bill("3040", "Projekt ustawy o obywatelstwie polskim")  # default analysis: score 5

    report = w.run(min_score=3)

    assert report.published == 1
    assert [(v.number, v.reason) for v in report.rejected] == [("3039", "score 2")]


def test_no_publish_marks_the_card_skipped_forever() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    w.run(publish=False)
    later = w.run()

    assert w.publisher.new_bills == []
    card = w.publication("3039")
    assert card is not None and card.status is PublicationStatus.SKIPPED
    assert later.published == 0


def test_dry_run_leaves_the_database_empty() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    report = w.run(dry_run=True)

    assert (report.published, report.mode) == (1, "dry_run")
    assert w.repo.get(10, "3039") is None
    assert w.repo.last_discovery_started_at() is None


def test_max_analyze_zero_skips_the_model() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    report = w.run(max_analyze=0)

    assert (report.analyzed, report.analysis_failures) == (0, 0)
    assert w.llm.contexts == []


# --------------------------------------------------------------------------- failures and retries


def test_llm_failure_is_isolated_and_retried_on_the_next_run() -> None:
    w = World(llm_script={"3039": RuntimeError("llm down")})
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.add_bill("3040", "Projekt ustawy o obywatelstwie polskim")

    first = w.run()
    w.llm.script = {}
    second = w.run()

    assert (first.analyzed, first.analysis_failures, first.published) == (1, 1, 1)
    assert (second.analyzed, second.published) == (1, 1)
    assert {b.number for b, _ in w.publisher.new_bills} == {"3039", "3040"}


def test_failed_analysis_costs_one_attempt() -> None:
    w = World(llm_script={"3039": RuntimeError("llm down")})
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    w.run()

    failed = w.bill("3039")
    assert (failed.status, failed.analysis_attempts) == (BillStatus.ANALYSIS_FAILED, 1)


def test_analysis_attempts_are_capped_at_three() -> None:
    w = World(llm_script={"3039": RuntimeError("llm down")})
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    for _ in range(5):
        w.run()

    assert w.bill("3039").analysis_attempts == 3


def test_failed_post_is_recorded_and_retried() -> None:
    w = World(fail_publish={"3039"})
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    failed = w.run()
    w.publisher.fail_on = set()
    retried = w.run()

    assert failed.published == 0 and failed.errors
    assert retried.published == 1
    card = w.publication("3039")
    assert card is not None and card.status is PublicationStatus.SENT


def test_migration_failure_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    w = World()

    def boom() -> None:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(w.repo, "migrate", boom)

    report = w.run()

    assert report.errors and "database is locked" in report.errors[0]
    assert w.notifier.calls  # the log channel still gets the report


# --------------------------------------------------------------------------- watermark


def test_since_defaults_to_the_last_run_minus_one_day_of_overlap() -> None:
    w = World()
    assert w.pipeline.resolve_since(None) == w.clock.now() - dt.timedelta(days=1)

    w.run()
    w.clock.advance(days=3)

    assert w.pipeline.resolve_since(None) == w.clock.now() - dt.timedelta(days=4)


def test_watermark_advances_after_a_run_with_publishing_errors() -> None:
    w = World(fail_publish={"3039"})
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    first_start = w.clock.now()

    report = w.run()
    w.clock.advance(days=5)

    assert report.errors
    assert w.pipeline.resolve_since(None) == first_start - dt.timedelta(days=1)


# --------------------------------------------------------------------------- report


def test_notifier_receives_the_report_and_the_captured_warnings() -> None:
    w = World(llm_script={"3039": RuntimeError("llm down")})
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    w.run()

    report, lines = w.notifier.calls[-1]
    assert report.analysis_failures == 1
    assert any("analysis failed for 3039" in line for line in lines)


def test_report_carries_phase_timings() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    report = w.run()

    assert set(report.phase_seconds) == {
        "commands",
        "discovery",
        "rcl discovery",
        "text prefilter",
        "analysis",
        "publishing",
        "tracking",
    }


# --------------------------------------------------------------------------- parallelism

COUNTERS = (
    "discovered",
    "prefilter_hits",
    "text_prefilter_checked",
    "text_prefilter_hits",
    "analyzed",
    "analysis_failures",
    "published",
    "tracked",
    "updates",
)
FOREIGNER_TEXT = (
    "Art. 1. W ustawie o cudzoziemcach wprowadza się zmiany. "
    "Art. 2. Zezwolenie na pobyt czasowy wydaje wojewoda. "
    "Art. 3. Cudzoziemiec składa wniosek osobiście. " * 3
)


def _two_runs(workers: int) -> tuple[dict[str, int], dict[str, int], list[str]]:
    w = World(extractor=FakeTextExtractor(FOREIGNER_TEXT), workers=workers)
    numbers = ("3039", "3040", "4000", "4001")
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.add_bill("3040", "Projekt ustawy o obywatelstwie polskim")
    w.add_bill("4000", "Rządowy projekt ustawy o podatku VAT")  # caught by the PDF text
    w.add_bill("4001", "Rządowy projekt ustawy o zmianie niektórych ustaw")
    first = w.run()
    for number in numbers:
        w.set_stages(number, COMMITTEE_STAGES)
    second = w.run()
    counters = [{k: getattr(r, k) for k in COUNTERS} for r in (first, second)]
    return counters[0], counters[1], sorted(b.number for b, _ in w.publisher.new_bills)


def test_parallel_workers_produce_the_same_result_as_a_plain_loop() -> None:
    sequential = _two_runs(workers=1)
    parallel = _two_runs(workers=4)

    assert sequential == parallel
    first, second, published = parallel
    assert (first["analyzed"], first["published"], first["text_prefilter_hits"]) == (4, 4, 2)
    assert (second["tracked"], second["updates"]) == (4, 4)
    assert published == ["3039", "3040", "4000", "4001"]
