"""Committee and Sejm sittings whose agenda names a followed bill."""

import datetime as dt

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import (
    Committee,
    CommitteeSitting,
    PublicationKind,
    SejmSitting,
    Stage,
)
from tests.harness import COMMITTEE_STAGES, REFERRED, World

ASW = Committee(term=10, code="ASW", name="Komisja Administracji i Spraw Wewnętrznych")
FIRST_READING_AGENDA = (
    '<div class="agenda-indent-0">Pierwsze czytanie projektu (druk nr 3039)</div>\n'
    '<div class="agenda-indent-0">– uzasadnia poseł X.</div>'
)
SITTING_REF = "ASW/136/2026-09-17+09:00+sala 412"  # day, hour and room: a move of any is a new post


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


def test_committees_sitting_together_are_announced_once() -> None:
    """One meeting is listed under every committee that sits in it, with a `num` of its own —
    938 of the 4,387 sittings of term 10. A bill referred to two of them got two identical
    posts: druk 2699 («o nabywaniu nieruchomości przez cudzoziemców») sat before ASW and SPC on
    2026-07-02, and would have said so twice."""
    w = _referred_bill()
    spc = Committee(term=10, code="SPC", name="Komisja Sprawiedliwości i Praw Człowieka")
    w.gateway.committees["SPC"] = spc
    referral = Stage(
        stage_name="Skierowanie",
        stage_type="Referral",
        date=dt.date(2026, 9, 3),
        committee_code="SPC",
    )
    referrals = COMMITTEE_STAGES[-1]
    w.set_stages(
        "3039",
        COMMITTEE_STAGES[:-1]
        + (referrals.model_copy(update={"children": (*referrals.children, referral)}),),
    )
    together = _sitting()
    w.gateway.committee_sittings["ASW"] = (together.model_copy(update={"joint_with": ("SPC",)}),)
    w.gateway.committee_sittings["SPC"] = (
        together.model_copy(update={"code": "SPC", "num": 92, "joint_with": ("ASW",)}),
    )

    report = w.run()

    assert report.agenda_posted == 1
    assert _refs(w) == [SITTING_REF]


def test_the_sitting_on_the_senates_resolution_is_found_by_its_own_print() -> None:
    """Past the third reading the agenda stops naming the bill: "Rozpatrzenie uchwały Senatu w
    sprawie ustawy o zmianie ustawy o cudzoziemcach (druk nr 1935)" is druk 1630 of term 10, and
    its committee sitting (ASW/79, 18.11.2025) and the plenary one that followed were both
    missed — the whole Senate and veto stretch, which is the reader's last window."""
    w = _referred_bill()
    senate = Stage(
        stage_name="Stanowisko Senatu",
        stage_type="SenatePosition",
        date=dt.date(2026, 9, 4),
        position="wniósł poprawki",
        print_number="3105",
    )
    w.set_stages("3039", COMMITTEE_STAGES + (senate,))
    w.gateway.committee_sittings["ASW"] = (
        _sitting(
            agenda=(
                '<div class="agenda-indent-0">Rozpatrzenie uchwały Senatu w sprawie ustawy '
                "o cudzoziemcach (druk nr 3105).</div>"
            ),
        ),
    )

    report = w.run()

    assert report.agenda_posted == 1
    _, item, _ = w.publisher.agendas[0]
    assert item.ref == SITTING_REF
    assert "uchwały Senatu" in item.text


def test_a_sitting_announced_under_the_old_ref_is_not_announced_again() -> None:
    """The `ref` used to be the day alone. Without keeping it, the first run after the hour and
    the room joined it would announce every sitting already on a bill's agenda a second time.

    A sitting listed with no hour and given one later looks exactly like a stored day-only ref,
    so it is held back too; the API gives an hour for every sitting of term 10 but one.
    """
    w = _referred_bill()
    bare = _sitting().model_copy(update={"start_time": None, "room": None})
    w.gateway.committee_sittings["ASW"] = (bare,)
    first = w.run()
    w.gateway.committee_sittings["ASW"] = (_sitting(),)  # the same sitting, hour and room now known
    w.clock.advance(days=1)

    report = w.run()

    assert (first.agenda_posted, report.agenda_posted) == (1, 0)
    assert _refs(w) == ["ASW/136/2026-09-17"]


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
    assert _refs(w) == ["ASW/136/2026-09-24+09:00+sala 412"]
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


def test_a_sitting_the_api_no_longer_calls_planned_is_not_announced() -> None:
    """`CommitteeSitting.status` was parsed and never read: a called-off sitting still carries a
    future date and an agenda."""
    w = _referred_bill()
    w.gateway.committee_sittings["ASW"] = (_sitting(status="CANCELLED"),)

    report = w.run()

    assert report.agenda_posted == 0
    assert _refs(w) == []


def test_a_sitting_that_moved_corrects_the_post_instead_of_contradicting_it() -> None:
    w = _referred_bill()
    w.gateway.committee_sittings["ASW"] = (_sitting(),)
    w.run()
    w.gateway.committee_sittings["ASW"] = (_sitting(date=dt.date(2026, 9, 22)),)
    w.clock.advance(days=1)

    report = w.run()

    assert report.agenda_posted == 1
    bill, item, _ = w.publisher.agendas[-1]
    assert item.date == dt.date(2026, 9, 22)
    was = w.publisher.agendas[0][1]
    text = MessageFormatter("ru").agenda(bill, item, moved_from=was).text
    assert "Заседание перенесено с 17.09.2026" in text


def test_a_sitting_that_keeps_the_day_but_moves_the_hour_is_told_again() -> None:
    """The hour and the room move on their own, and often: of the 886 committee sittings of
    term 10 whose `comments` record a change, 204 say "Nastąpiła zmiana godziny posiedzenia" and
    97 "zmiana sali". The day was the whole of the `ref`, so the run wrote the new hour to the
    bill and said nothing, and the post the reader planned a day around named the old one."""
    w = _referred_bill()
    w.gateway.committee_sittings["ASW"] = (_sitting(),)
    w.run()
    w.gateway.committee_sittings["ASW"] = (
        _sitting().model_copy(update={"start_time": dt.time(13, 30), "room": "sala 118"}),
    )
    w.clock.advance(days=1)

    report = w.run()

    assert report.agenda_posted == 1
    bill, item, _ = w.publisher.agendas[-1]
    assert (item.start_time, item.room) == (dt.time(13, 30), "sala 118")
    was = w.publisher.agendas[0][1]
    text = MessageFormatter("ru").agenda(bill, item, moved_from=was).text
    assert "13:30" in text and "sala 118" in text
    assert "Изменились время или зал" in text and "09:00 · sala 412" in text
    # The sitting is the same one, so it is corrected and not taken back as called off.
    assert report.agenda_cancelled == 0


def test_a_committee_sitting_that_has_already_met_today_is_not_announced() -> None:
    """A committee agenda often appears on the morning of the sitting, and a run lands at ~11:00
    and ~22:00 Warsaw. Announcing an 09:00 sitting at 22:00 tells the reader about a room they
    cannot walk into any more."""
    w = _referred_bill()
    w.gateway.committee_sittings["ASW"] = (_sitting(date=dt.date(2026, 9, 7)),)  # today, 09:00
    w.clock.current = dt.datetime(2026, 9, 7, 20, 0, tzinfo=dt.UTC)  # 22:00 in Warsaw

    report = w.run()

    assert report.agenda_posted == 0
    assert _refs(w) == [
        "ASW/136/2026-09-07+09:00+sala 412"
    ]  # still stored: the card dates its next step by it


def test_a_committee_sitting_later_today_is_announced() -> None:
    w = _referred_bill()
    w.gateway.committee_sittings["ASW"] = (_sitting(date=dt.date(2026, 9, 7)),)  # today, 09:00
    w.clock.current = dt.datetime(2026, 9, 7, 5, 0, tzinfo=dt.UTC)  # 07:00 in Warsaw

    report = w.run()

    assert report.agenda_posted == 1


def test_a_sitting_that_is_called_off_is_taken_back() -> None:
    """The agenda post is the message a reader plans a day around. A sitting that left `PLANNED`
    used to vanish from `bill.agenda` in silence: the card quietly went back to «обычно 2–6
    недель» while the post naming the room and the hour stood unchanged, and the reader turned
    up."""
    w = _referred_bill()
    w.gateway.committee_sittings["ASW"] = (_sitting(),)
    w.run()
    w.gateway.committee_sittings["ASW"] = (_sitting(status="CANCELLED"),)
    w.clock.advance(days=1)

    report = w.run()

    assert report.agenda_cancelled == 1
    assert _refs(w) == []
    bill, item, still_meets = w.publisher.agenda_cancellations[0]
    assert (bill.number, item.ref, still_meets) == ("3039", SITTING_REF, False)
    text = (
        MessageFormatter("ru")
        .agenda_cancelled(bill, item, still_meets=still_meets, today=dt.date(2026, 9, 8))
        .text
    )
    assert "🗓 <b>Заседание отменено — druk nr 3039</b>" in text
    assert "было запланировано на 17.09.2026, 09:00" in text
    assert "Новая дата пока не назначена" in text
    assert "#заседаниекомиссии" in text  # one search finds the sitting and its retraction


def test_the_sitting_goes_ahead_without_the_bill_and_says_so() -> None:
    """Two different facts for someone who booked the morning: the committee is not meeting, or
    it is meeting about something else."""
    w = _referred_bill()
    w.gateway.committee_sittings["ASW"] = (_sitting(),)
    w.run()
    w.gateway.committee_sittings["ASW"] = (_sitting(agenda="Inne sprawy (druk nr 1111)"),)
    w.clock.advance(days=1)

    report = w.run()

    assert report.agenda_cancelled == 1
    _, item, still_meets = w.publisher.agenda_cancellations[0]
    assert still_meets
    text = (
        MessageFormatter("ru")
        .agenda_cancelled(w.bill("3039"), item, still_meets=True, today=dt.date(2026, 9, 8))
        .text
    )
    assert "🗓 <b>Проект снят с повестки заседания — druk nr 3039</b>" in text
    assert "Заседание состоится, но этого проекта в его повестке больше нет" in text


def test_a_sitting_is_taken_back_only_once() -> None:
    w = _referred_bill()
    w.gateway.committee_sittings["ASW"] = (_sitting(),)
    w.run()
    w.gateway.committee_sittings["ASW"] = (_sitting(status="CANCELLED"),)
    w.clock.advance(days=1)
    w.run()

    w.clock.advance(days=1)
    again = w.run()

    assert again.agenda_cancelled == 0
    assert len(w.publisher.agenda_cancellations) == 1


def test_a_rescheduled_sitting_is_not_taken_back() -> None:
    """It keeps its `sitting_key`, and the new post says where it moved from — a retraction next
    to it would contradict the correction instead of being one."""
    w = _referred_bill()
    w.gateway.committee_sittings["ASW"] = (_sitting(),)
    w.run()
    w.gateway.committee_sittings["ASW"] = (_sitting(136, dt.date(2026, 9, 24)),)
    w.clock.advance(days=1)

    report = w.run()

    assert (report.agenda_posted, report.agenda_cancelled) == (1, 0)
    assert w.publisher.agenda_cancellations == []


def test_a_committee_listing_that_failed_is_not_a_cancellation() -> None:
    """The item is missing because the request was refused, not because the sitting is off."""
    w = _referred_bill()
    w.gateway.committee_sittings["ASW"] = (_sitting(),)
    w.run()
    del w.gateway.committee_sittings["ASW"]  # the fake raises for an unknown committee
    w.clock.advance(days=1)

    report = w.run()

    assert report.agenda_cancelled == 0
    assert _refs(w) == [SITTING_REF]


def test_a_sitting_the_reader_was_never_told_about_is_not_taken_back() -> None:
    """Publishing was off when it was announced, so there is nothing standing to correct."""
    w = _referred_bill()
    w.gateway.committee_sittings["ASW"] = (_sitting(),)
    w.run(publish=False)
    w.gateway.committee_sittings["ASW"] = (_sitting(status="CANCELLED"),)
    w.clock.advance(days=1)

    report = w.run()

    assert report.agenda_cancelled == 0
    assert w.publisher.agenda_cancellations == []


def test_a_sitting_the_committee_called_conditionally_is_not_announced_as_a_fact() -> None:
    """Twenty-one sittings of term 10 happen only if the Sejm refers something to the committee
    first — four of them were still ahead on 2026-09-15 — and the channel announced every one of
    them the way it announces a settled date. The same note is the only place the API publishes
    an address for applying to a przesłuchanie, and `closed` says the room named in the post is
    one the public may not enter."""
    w = _referred_bill()
    w.gateway.committee_sittings["ASW"] = (
        _sitting().model_copy(
            update={
                "closed": True,
                "notes": (
                    "Posiedzenie aktualne w przypadku zakończenia pierwszego czytania na"
                    " posiedzeniu plenarnym Sejmu i skierowania projektu do Komisji."
                    " Zgłoszenia udziału w przesłuchaniu należy przesyłać na adres e-mail:"
                    " zgloszenie.RF@sejm.gov.pl w terminie do 16 września 2026 r."
                ),
            }
        ),
    )

    report = w.run()

    assert report.agenda_posted == 1
    _, item, _ = w.publisher.agendas[0]
    assert item.condition == "first_reading_referral"
    assert (item.closed, item.apply_email) == (True, "zgloszenie.RF@sejm.gov.pl")
    assert item.apply_by == dt.date(2026, 9, 16)
    [text] = w.publisher.texts(PublicationKind.AGENDA)
    assert "Заседание объявлено условно" in text and "первое чтение" in text
    assert "Заседание закрытое" in text
    assert "zgloszenie.RF@sejm.gov.pl" in text and "16.09.2026" in text


def test_a_condition_on_another_point_of_the_agenda_leaves_our_sitting_a_fact() -> None:
    """Eleven of the 21 name the points they cover, and on two the point carries none of our
    prints (FPB/86, SPC/52): a hedge there would be as wrong as the fact the others were."""
    w = _referred_bill()
    w.gateway.committee_sittings["ASW"] = (
        _sitting(
            agenda=(
                '<div class="agenda-indent-0">I. Pierwsze czytanie projektu (druk nr 3039)</div>'
                '<div class="agenda-indent-0">II. Rozpatrzenie planu pracy Komisji.</div>'
            )
        ).model_copy(
            update={
                "notes": (
                    "Pkt II aktualny w przypadku zakończenia pierwszego czytania na posiedzeniu"
                    " Sejmu i skierowania projektu do Komisji"
                )
            }
        ),
    )

    report = w.run()

    assert report.agenda_posted == 1
    _, item, _ = w.publisher.agendas[0]
    assert item.condition is None
    assert "условно" not in w.publisher.texts(PublicationKind.AGENDA)[0]
