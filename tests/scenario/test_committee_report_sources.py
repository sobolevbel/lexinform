import datetime as dt
from pathlib import Path

import pytest

from lexinform.adapters.sejm_api import parse_print
from lexinform.models import AmendmentsContext, Stage
from tests.fakes import FakeTextExtractor
from tests.harness import REFERRED, World, print_url
from tests.scenario.test_senate_document_selection import STAGE, SUBJECT

FIXTURES = Path(__file__).parents[1] / "fixtures/sejm"
REPORT_URL = print_url("3255")
CATALOGUE_URL = REPORT_URL.replace("3255.pdf", "3255%20.pdf")
REPORT = Stage(
    stage_type="CommitteeReport",
    stage_name="Sprawozdanie komisji",
    date=dt.date(2026, 10, 21),
    print_number="3255",
    report_file=REPORT_URL,
    proposal="przyjąć poprawki",
)


def tracked_report(*, senate: bool, workers: int = 1) -> World:
    extractor = FakeTextExtractor(
        by_content={
            b"resolution": (FIXTURES / "senate_3213_resolution.txt").read_text(),
            b"report": (FIXTURES / "committee_3255_senate_report.txt").read_text(),
        }
    )
    world = World(
        extractor=extractor,
        batch=True,
        batch_kinds=frozenset({"amendments"}),
        text_budget_chars=60_000,
        workers=workers,
    )
    world.add_bill("1929", "Projekt ustawy o cudzoziemcach", stages=REFERRED)
    world.run()
    world.gateway.prints["3213"] = parse_print(
        {
            "term": 10,
            "number": "3213",
            "title": "Uchwała Senatu w sprawie ustawy " + SUBJECT,
            "attachments": ["3213.pdf"],
            "processPrint": ["1929"],
        },
        term=10,
        base_url="https://api.test",
    )
    world.gateway.files[print_url("3213")] = b"resolution"
    work = Stage(stage_type="CommitteeWork", stage_name="Praca w komisjach", children=(REPORT,))
    world.set_stages("1929", REFERRED + ((STAGE,) if senate else ()) + (work,))
    world.clock.advance(days=1)
    return world


def publish_catalogue(world: World) -> None:
    world.gateway.prints["3255"] = parse_print(
        {
            "term": 10,
            "number": "3255",
            "title": "Sprawozdanie Komisji o uchwale Senatu",
            "processPrint": ["1929"],
            "attachments": ["3255 .pdf"],
        },
        term=10,
        base_url="https://api.test",
    )
    world.gateway.files[CATALOGUE_URL] = b"report"


@pytest.mark.parametrize("senate", [False, True])
@pytest.mark.parametrize("workers", [1, 4])
def test_waiting_report_recovers_through_batch_and_publishes_once(
    senate: bool, workers: int
) -> None:
    world = tracked_report(senate=senate, workers=workers)
    before = world.bill("1929").observed_process
    missing = world.run()
    assert missing.batch_waiting == 1 and not world.batch.submitted
    assert not world.publisher.updates
    publish_catalogue(world)

    waiting = world.run(since=world.clock.now())

    assert waiting.batch_waiting == 1 and not waiting.errors
    assert world.bill("1929").observed_process == before
    assert len(world.batch.submitted) == 1
    context = world.batch.submitted[0].ctx
    assert isinstance(context, AmendmentsContext), "only the amendments batch is enabled"
    report = (FIXTURES / "committee_3255_senate_report.txt").read_text()
    assert (context.recommendation if senate else context.text) == report
    if senate:
        assert context.text == (FIXTURES / "senate_3213_resolution.txt").read_text()
    world.repo.restore(world.repo.dump())
    world.batch.resolve()
    posted = world.run(since=world.clock.now())
    repeated = world.run(since=world.clock.now())

    assert (posted.updates, repeated.updates) == (1, 0)
    assert world.bill("1929").awaiting_batch_since is None
    assert len(world.batch.submitted) == 1 and not world.llm.amendment_contexts
    change = world.publisher.updates[0][1]
    assert change.amendments is not None, "the update waits for its substantive summary"
    assert change.amendments.source_url == (print_url("3213") if senate else CATALOGUE_URL)


def test_missing_catalogue_keeps_a_working_official_stage_link() -> None:
    world = tracked_report(senate=False)
    world.gateway.files[REPORT_URL] = b"report"

    result = world.run()

    assert result.batch_waiting == 1 and not result.errors
    assert len(world.batch.submitted) == 1


@pytest.mark.parametrize("senate", [False, True])
def test_foreign_catalogue_does_not_unblock_a_missing_report(senate: bool) -> None:
    world = tracked_report(senate=senate)
    publish_catalogue(world)
    world.gateway.prints["3255"] = world.gateway.prints["3255"].model_copy(
        update={"process_prints": ("1930",)}
    )

    result = world.run()

    assert result.batch_waiting == 1 and not result.errors
    assert not world.batch.submitted and not world.publisher.updates


def test_catalogue_outage_does_not_burn_attempts_or_commit_the_observation() -> None:
    world = tracked_report(senate=False)
    before = world.bill("1929")
    world.gateway.outages.add("get_print:3255")

    result = world.run()

    assert result.errors and not world.batch.submitted
    assert world.bill("1929").observed_process == before.observed_process
    assert world.bill("1929").analysis_attempts == before.analysis_attempts
