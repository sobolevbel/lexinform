"""Batch digests defer the observation and deliver all accumulated news together."""

import pytest

from lexinform.errors import LlmUnavailableError
from lexinform.models import (
    Attachment,
    Bill,
    PrintInfo,
    PublicationKind,
    StatusSnapshot,
    SupplementRecord,
    TextDocument,
)
from lexinform.services.analysis import Waiting
from tests.fakes import FakeTextExtractor
from tests.harness import COMMITTEE_STAGES, REFERRED, World
from tests.scenario.test_tracking import (
    A_REPORT_URL,
    REPORT_URL,
    SENATE_AMENDED,
    SENATE_PRINT_URL,
    WITH_A_REPORT,
    WITH_REPORT,
)

TITLE = "Projekt ustawy o cudzoziemcach"
OSR = "Do druku nr 3039 - ocena skutków regulacji"


def tracked_world(*, workers: int = 1, scanned: bool = False) -> World:
    w = World(
        batch=True,
        batch_kinds=frozenset({"supplement"}),
        workers=workers,
        extractor=FakeTextExtractor(by_content={b"%PDF-filed": ""}, page_count=4)
        if scanned
        else None,
    )
    w.add_bill("3039", TITLE)
    w.run()
    w.file_to_print("3039", OSR)
    w.clock.advance(hours=1)
    return w


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("scanned", [False, True])
def test_digest_wait_preserves_baseline_and_survives_watermark_and_restore(
    workers: int, scanned: bool
) -> None:
    w = tracked_world(workers=workers, scanned=scanned)
    before = w.bill("3039").observed_process
    waiting = w.run()

    assert waiting.batch_waiting == 1 and not waiting.errors
    assert w.bill("3039").observed_process == before
    assert w.bill("3039").seen_supplements == ()
    assert w.bill("3039").awaiting_batch_since is not None
    assert not w.publisher.updates
    assert w.publication("3039", PublicationKind.STATUS_UPDATE) is None
    w.repo.restore(w.repo.dump())
    w.set_stages("3039", COMMITTEE_STAGES)
    w.clock.advance(hours=1)
    w.batch.resolve()

    posted = w.run(since=w.clock.now())
    repeated = w.run(since=w.clock.now())

    assert (posted.updates, repeated.updates) == (1, 0)
    assert w.bill("3039").awaiting_batch_since is None
    assert not w.llm.supplement_contexts
    change = w.publisher.updates[0][1]
    assert len(change.new_stages) == 2
    assert change.supplements[0].digest is not None
    assert len([call for call in posted.llm_calls if call.kind == "supplement"]) == 1
    assert not [call for call in repeated.llm_calls if call.kind == "supplement"]
    text = w.formatter.status_update(*w.publisher.updates[0][:2]).text
    assert "Оценка последствий (OSR)" in text


@pytest.mark.parametrize("failure", ["timeout", "error", "manual"])
def test_digest_falls_back_and_late_answer_never_repeats_the_update(failure: str) -> None:
    w = tracked_world()
    w.run()
    if failure == "timeout":
        w.clock.advance(hours=7)
    elif failure == "error":
        w.batch.secondary_script[("3039", "supplement")] = RuntimeError("invalid")
        w.batch.resolve()
    else:
        w.command("/refresh 3039")

    w.run()
    w.batch.resolve()
    w.run()

    assert len(w.publisher.updates) == 1
    assert len(w.llm.supplement_contexts) == 1
    assert w.bill("3039").awaiting_batch_since is None


def test_dry_run_digest_is_synchronous() -> None:
    w = tracked_world()

    report = w.run(dry_run=True)

    assert report.updates == 1 and report.batch_waiting == 0
    assert len(w.llm.supplement_contexts) == 1
    assert not w.batch.submitted
    assert w.bill("3039").awaiting_batch_since is None


def test_status_names_deferred_observation_and_its_age() -> None:
    w = tracked_world()
    w.run()
    w.clock.advance(hours=2)
    w.command("/status")
    w.run()
    outcome = w.replier.replies[-1][1]
    assert isinstance(outcome.snapshot, StatusSnapshot)
    assert [bill.number for bill in outcome.snapshot.awaiting_batch] == ["3039"]
    assert outcome.snapshot.observed_at == w.clock.now()
    text = w.formatter.command_reply(*w.replier.replies[-1]).text
    assert "Ждут сводки батча" in text and "2.0 ч" in text


def test_urgent_bill_digest_never_waits() -> None:
    w = tracked_world()
    w.touch("3039", w.clock.now(), urgency_status="URGENT")

    report = w.run()

    assert report.updates == 1 and report.batch_waiting == 0
    assert len(w.llm.supplement_contexts) == 1
    assert not w.batch.submitted


def test_wait_marker_survives_an_outage_reading_the_next_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = tracked_world()
    w.file_to_print("3039", "Stanowisko Rządu do druku nr 3039.", suffix="002")
    original = w.analysis.digest_supplement

    def digest(
        bill: Bill, document: TextDocument, *, number: str, title: str, may_wait: bool = False
    ) -> SupplementRecord | Waiting:
        if number == "3039-002":
            raise LlmUnavailableError("injected outage")
        return original(bill, document, number=number, title=title, may_wait=may_wait)

    with monkeypatch.context() as patch:
        patch.setattr(w.analysis, "digest_supplement", digest)
        report = w.run()

    assert report.errors
    assert w.bill("3039").awaiting_batch_since is not None
    assert not w.bill("3039").seen_supplements
    assert not w.publisher.updates


@pytest.mark.parametrize("senate", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("workers", [1, 4])
def test_amendment_batches_wait_only_when_enabled(
    senate: bool, enabled: bool, workers: int
) -> None:
    w = World(
        batch=True,
        batch_kinds=frozenset({"amendments"}) if enabled else frozenset(),
        workers=workers,
    )
    w.add_bill("3039", TITLE, stages=REFERRED if senate else WITH_REPORT)
    w.gateway.files[REPORT_URL] = b"%PDF-report"
    w.run()
    before = w.bill("3039").observed_process
    if senate:
        w.gateway.prints["2994"] = PrintInfo(
            term=10,
            number="2994",
            title="Uchwała Senatu",
            attachments=(Attachment(print_number="2994", name="2994.pdf", url=SENATE_PRINT_URL),),
        )
        w.gateway.files[SENATE_PRINT_URL] = b"%PDF-senat"
        w.set_stages("3039", SENATE_AMENDED)
    else:
        w.gateway.files[A_REPORT_URL] = b"%PDF-A"
        w.set_stages("3039", WITH_A_REPORT)
    w.clock.advance(hours=1)

    first = w.run()
    if enabled:
        assert first.batch_waiting == 1 and first.updates == 0
        assert w.bill("3039").observed_process == before
        assert not w.llm.amendment_contexts
        w.batch.resolve()
        posted = w.run()
        assert posted.updates == 1
    else:
        assert first.updates == 1 and first.batch_waiting == 0
        assert len(w.llm.amendment_contexts) == 1
    repeated = w.run()

    assert repeated.updates == 0
    assert w.bill("3039").awaiting_batch_since is None
    change = w.publisher.updates[0][1]
    assert change.amendments is not None
    assert change.amendments.source_url == (SENATE_PRINT_URL if senate else A_REPORT_URL)
    text = w.formatter.status_update(*w.publisher.updates[0][:2]).text
    assert "Что меняют поправки" in text
