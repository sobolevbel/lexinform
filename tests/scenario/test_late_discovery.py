import datetime as dt
from pathlib import Path

import pytest

from lexinform.models import ProcessDetail, Stage, next_phase
from tests.fakes import FakeTextExtractor
from tests.harness import World

CURRENT_URL = "https://api.test/sejm/term10/processes/2111/attachment/2111_u3.pdf"
CURRENT_TEXT = "Art. 1. Cudzoziemiec otrzymuje zezwolenie na pobyt. " * 50
VETO_STAGES = (
    Stage(stage_type="Start", stage_name="Projekt wpłynął do Sejmu"),
    Stage(
        stage_type="SejmReading",
        stage_name="III czytanie na posiedzeniu Sejmu",
        date=dt.date(2026, 5, 29),
        decision="uchwalono",
        text_after3=CURRENT_URL,
    ),
    Stage(stage_type="Veto", stage_name="Wniosek Prezydenta (weto)", date=dt.date(2026, 7, 17)),
    Stage(
        stage_type="CommitteeWork",
        stage_name="Praca w komisjach nad wnioskiem Prezydenta",
        date=dt.date(2026, 9, 16),
        children=(
            Stage(
                stage_type="CommitteeReport",
                stage_name="Sprawozdanie komisji",
                print_number="3111",
                proposal="uchwalić ponownie",
                report_file="https://api.test/sejm/term10/prints/3111/3111.pdf",
            ),
        ),
    ),
    Stage(stage_type="End", stage_name="Uchwalono"),
)


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize(
    "count, expected",
    [
        (1, "first_reading"),
        (2, "president_after_senate_silence"),
        (3, "veto"),
        (4, "veto"),
        (5, "veto"),
    ],
)
def test_cold_discovery_at_each_incident_stage_is_an_introduction(
    workers: int,
    count: int,
    expected: str,
) -> None:
    w = World(workers=workers)
    w.add_bill("2111", "Projekt ustawy o cudzoziemcach", stages=VETO_STAGES[:count])
    w.gateway.files[CURRENT_URL] = b"%PDF-current"
    if count > 1:
        w.touch("2111", w.clock.now(), closure_date=dt.date(2026, 5, 29), passed=True)

    first = w.run()
    again = w.run(full_track=True)

    assert (first.analyzed, first.published, first.reanalyzed, first.updates) == (1, 1, 0, 0)
    assert (again.analyzed, again.reanalyzed, again.updates) == (0, 0, 0)
    assert len(w.llm.contexts) == 1
    phase = next_phase(w.bill("2111"), today=w.clock.now().date())
    assert phase is not None and phase.key == expected
    record = w.bill("2111").analysis
    assert record is not None, "the first run analysed and published the bill"
    assert record.source_kind == ("print" if count == 1 else "text_after3")


@pytest.mark.parametrize("workers", [1, 4])
def test_late_discovery_analyses_current_text_once_and_does_not_invent_a_vote(workers: int) -> None:
    fixture = Path(__file__).parents[1] / "fixtures/sejm/process_2111_incident.json"
    process = ProcessDetail.model_validate_json(
        fixture.read_text().replace("https://api.sejm.gov.pl", "https://api.test")
    )
    w = World(
        workers=workers, extractor=FakeTextExtractor(by_content={b"%PDF-current": CURRENT_TEXT})
    )
    w.clock.current = dt.datetime(2026, 9, 16, 19, 35, 36, tzinfo=dt.UTC)
    w.add_bill("2111", process.title, stages=process.stages)
    w.gateway.processes = [process]
    w.gateway.details["2111"] = process
    w.gateway.files[CURRENT_URL] = b"%PDF-current"

    first = w.run()
    w.clock.advance(days=1)
    again = w.run(full_track=True)

    assert (first.analyzed, first.published, first.reanalyzed, first.updates) == (1, 1, 0, 0)
    assert (again.analyzed, again.reanalyzed, again.updates) == (0, 0, 0)
    assert len(w.llm.contexts) == 1
    assert w.llm.contexts[0].text == CURRENT_TEXT.strip()
    assert w.llm.contexts[0].source_kind == "text_after3"
    assert w.publisher.updates == []
    record = w.bill("2111").analysis
    assert record is not None and record.source_url == CURRENT_URL and record.revision == 1


@pytest.mark.parametrize("same_day", [False, True])
def test_closure_present_at_first_analysis_is_a_baseline_even_when_tracking_runs_later(
    same_day: bool,
) -> None:
    w = World()
    w.add_bill("2111", "Projekt ustawy о cudzoziemcach", stages=VETO_STAGES)
    w.gateway.files[CURRENT_URL] = b"%PDF"
    closed = w.clock.now().date() if same_day else dt.date(2026, 5, 29)
    w.touch("2111", w.clock.now(), closure_date=closed, passed=True)
    w.run(track=False)
    w.clock.advance(days=1)

    report = w.run(full_track=True)

    assert report.updates == 0
    assert w.publisher.updates == []


def test_a_real_vote_after_late_discovery_is_still_announced_once() -> None:
    w = World()
    w.add_bill("2111", "Projekt ustawy o cudzoziemcach", stages=VETO_STAGES)
    w.gateway.files[CURRENT_URL] = b"%PDF"
    w.touch("2111", w.clock.now(), closure_date=dt.date(2026, 5, 29), passed=True)
    w.run()
    w.clock.advance(days=1)
    motion = Stage(
        stage_type="PresidentMotionConsideration",
        stage_name="Rozpatrywanie na forum Sejmu wniosku Prezydenta",
        decision="uchwalono ponownie",
        date=w.clock.now().date(),
    )
    w.set_stages("2111", VETO_STAGES[:-1] + (motion, VETO_STAGES[-1]))
    w.touch("2111", w.clock.now())

    report = w.run()
    again = w.run(full_track=True)

    assert report.updates == 1 and again.updates == 0
    assert len(w.publisher.updates) == 1
    bill, change, _ = w.publisher.updates[0]
    assert not change.closure_detected
    assert "отклонил вето" in w.formatter.status_update(bill, change).text.lower()
