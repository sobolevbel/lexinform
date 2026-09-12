"""Committee and Sejm sittings whose agenda names a followed bill."""

import datetime as dt

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import Committee, CommitteeSitting, SejmSitting, Stage
from tests.harness import COMMITTEE_STAGES, REFERRED, World

ASW = Committee(term=10, code="ASW", name="Komisja Administracji i Spraw Wewnętrznych")
FIRST_READING_AGENDA = (
    '<div class="agenda-indent-0">Pierwsze czytanie projektu (druk nr 3039)</div>\n'
    '<div class="agenda-indent-0">– uzasadnia poseł X.</div>'
)
SITTING_REF = "ASW/136/2026-09-17"


def _sitting(
    num: int = 136,
    date: dt.date = dt.date(2026, 9, 17),
    *,
    agenda: str = FIRST_READING_AGENDA,
    status: str = "PLANNED",
) -> CommitteeSitting:
    return CommitteeSitting(
        code="ASW",
        num=num,
        date=date,
        start_time=dt.time(9, 0),
        room="sala 412",
        status=status,
        agenda=agenda,
        video_url="https://sejm.gov.pl/Sejm10.nsf/transmisje_arch.xsp?unid=1",
    )


def _referred_bill() -> World:
    w = World()  # clock: Monday 2026-09-07
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=COMMITTEE_STAGES)
    w.gateway.committees["ASW"] = ASW
    return w


def _refs(w: World, number: str = "3039") -> list[str]:
    return [item.ref for item in w.bill(number).agenda]


def test_committee_sitting_naming_the_bill_is_posted_once() -> None:
    w = _referred_bill()
    w.gateway.committee_sittings["ASW"] = (
        _sitting(130, dt.date(2026, 9, 2), status="FINISHED"),  # over: ignored
        _sitting(135, dt.date(2026, 9, 15), agenda="<div>Inne sprawy (druk nr 1)</div>"),
        _sitting(),
    )

    report = w.run()
    w.clock.advance(days=1)
    again = w.run()

    assert (report.published, report.agenda_posted) == (1, 1)
    bill, item, reply_to = w.publisher.agendas[0]
    assert (bill.number, reply_to) == ("3039", w.card_id("3039"))
    assert (item.ref, item.kind) == (SITTING_REF, "committee")
    assert item.committee_name == ASW.name
    assert item.text == "Pierwsze czytanie projektu (druk nr 3039) – uzasadnia poseł X."
    assert (item.start_time, item.room) == (dt.time(9, 0), "sala 412")
    assert _refs(w) == [SITTING_REF]
    assert again.agenda_posted == 0 and len(w.publisher.agendas) == 1


def test_stage_update_carries_the_scheduled_sitting() -> None:
    w = _referred_bill()
    w.gateway.committee_sittings["ASW"] = (_sitting(),)
    w.run()
    reading = Stage(
        stage_name="I czytanie w komisjach", stage_type="Reading", date=dt.date(2026, 9, 8)
    )
    hearing = Stage(
        stage_name="Wysłuchanie publiczne", stage_type="PublicHearing", date=dt.date(2026, 9, 30)
    )
    w.set_stages("3039", COMMITTEE_STAGES + (reading,))
    w.clock.advance(days=1)
    held = w.run()  # the first reading alone is a service stage: kept for the next post
    w.set_stages("3039", COMMITTEE_STAGES + (reading, hearing))
    w.clock.advance(days=1)

    report = w.run()

    assert (held.updates, held.held, report.updates) == (0, 1, 1)
    bill, change, _ = w.publisher.updates[0]
    assert [st.stage_type for st in change.new_stages] == ["Reading", "PublicHearing"]
    text = MessageFormatter("ru").status_update(bill, change, today=dt.date(2026, 9, 9)).text
    assert "• 08.09.2026: I чтение в комиссиях" in text  # the held stage, told now
    assert "Komisja Administracji i Spraw Wewnętrznych (ASW) (sprawozdanie)" in text
    assert "· 17.09.2026, 09:00" in text and "до заседания 17.09.2026" in text


def test_rescheduled_sitting_is_posted_again_and_replaces_the_old_item() -> None:
    w = _referred_bill()
    w.gateway.committee_sittings["ASW"] = (_sitting(),)
    w.run()
    w.gateway.committee_sittings["ASW"] = (_sitting(136, dt.date(2026, 9, 24)),)
    w.clock.advance(days=1)

    report = w.run()

    assert report.agenda_posted == 1
    assert _refs(w) == ["ASW/136/2026-09-24"]
    assert len(w.publisher.agendas) == 2


def test_past_sitting_is_dropped_from_the_bill_without_a_post() -> None:
    w = _referred_bill()
    w.gateway.committee_sittings["ASW"] = (_sitting(),)
    w.run()
    w.clock.advance(days=20)

    report = w.run()

    assert report.agenda_posted == 0
    assert _refs(w) == []


def test_failed_committee_listing_keeps_the_known_items() -> None:
    w = _referred_bill()
    w.gateway.committee_sittings["ASW"] = (_sitting(),)
    w.run()
    del w.gateway.committee_sittings["ASW"]  # a 4xx for this committee: a per-item problem
    w.clock.advance(days=1)

    report = w.run()

    assert report.agenda_posted == 0 and report.errors == []
    assert _refs(w) == [SITTING_REF]


def test_sejm_api_outage_stops_only_the_agenda_watch_and_loses_nothing() -> None:
    """The sittings being unreachable says nothing about the Dziennik Ustaw notices, the
    reminders and the stage updates that the rest of the phase still owes its readers."""
    w = _referred_bill()
    w.gateway.committee_sittings["ASW"] = (_sitting(),)
    w.run()
    w.gateway.outages.add("list_committee_sittings")
    w.clock.advance(days=1)

    report = w.run()

    assert any("sittings: Sejm API unavailable" in e for e in report.errors)
    assert _refs(w) == [SITTING_REF]
    assert len(w.publisher.agendas) == 1


def test_items_are_stored_but_not_posted_without_publishing() -> None:
    w = _referred_bill()
    w.run()
    w.gateway.committee_sittings["ASW"] = (_sitting(),)
    w.clock.advance(days=1)

    silent = w.run(publish=False)
    stored_silently = _refs(w)
    posted_silently = list(w.publisher.agendas)
    w.clock.advance(days=1)
    loud = w.run()

    assert silent.agenda_posted == 0 and posted_silently == []
    assert stored_silently == [SITTING_REF]
    assert loud.agenda_posted == 1


def test_sejm_sitting_agenda_naming_the_bill_is_posted() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=REFERRED)
    w.gateway.sittings = [
        SejmSitting(
            number=64,
            dates=(dt.date(2026, 9, 2), dt.date(2026, 9, 4)),
            agenda="<li>Stare (druk nr 3039)</li>",  # over: not fetched
        ),
        SejmSitting(
            number=65,
            dates=tuple(dt.date(2026, 9, d) for d in (15, 16, 17, 18)),
            agenda=(
                "<ol><li>Sprawozdanie Komisji (druki nr 3039 i 3055) - sprawozdawca poseł Y.</li>"
                "<li>Inne (druk nr 1)</li></ol>"
            ),
        ),
        SejmSitting(number=0, dates=(dt.date(2026, 10, 7),)),  # planned, no agenda yet
    ]

    report = w.run()

    assert report.agenda_posted == 1
    bill, item, _ = w.publisher.agendas[0]
    assert (item.kind, item.ref, item.sitting_number) == ("sejm", "sejm/65/2026-09-15", 65)
    assert (item.date, item.end_date) == (dt.date(2026, 9, 15), dt.date(2026, 9, 18))
    assert item.text == "Sprawozdanie Komisji (druki nr 3039 i 3055) - sprawozdawca poseł Y."
    assert [c for c in w.gateway.calls if c.startswith("get_sitting:")] == ["get_sitting:65"]
    card = MessageFormatter("ru").new_bill(bill, None, today=dt.date(2026, 9, 7)).text
    assert "Что дальше:</b> I чтение на заседании Сейма · заседание Сейма № 65" in card
