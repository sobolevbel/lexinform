"""Following a government project on RCL: stages, consultation, new text, the way to the Sejm."""

import datetime as dt

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import BillStatus, PublicationKind, RclProject
from tests.harness import (
    CONSULTATION_FOLDERS,
    RCL,
    START,
    World,
    detail,
    rcl_document,
    rcl_folder,
    rcl_project,
    rcl_stage,
    summary,
)

UZGODNIENIA_ONLY = (
    rcl_stage(2, "Uzgodnienia", "active"),
    rcl_stage(3, "Konsultacje publiczne", "not_started", modified=None),
    rcl_stage(14, "Skierowanie projektu ustawy do Sejmu", "not_started", modified=None),
)


def _followed(w: World, project: RclProject | None = None) -> RclProject:
    """A project discovered and published on the first run; the clock moved on by a day."""
    project = w.add_rcl_project(project)
    w.run()
    w.clock.advance(days=1)
    return project


def _moved(project: RclProject, *stages: object, **fields: object) -> RclProject:
    return project.model_copy(update={"stages": tuple(stages), **fields})


def test_reached_stage_is_posted_once() -> None:
    w = World()
    project = _followed(w)
    w.rcl.put(
        _moved(
            project,
            *project.stages[:3],
            rcl_stage(4, "Opiniowanie", "reached", modified=dt.date(2026, 9, 8)),
            rcl_stage(9, "Stały Komitet Rady Ministrów", "active", modified=dt.date(2026, 9, 8)),
            *project.stages[5:],
            modified=dt.date(2026, 9, 8),
        )
    )

    report = w.run()
    w.clock.advance(days=1)
    again = w.run()

    assert (report.tracked, report.updates, again.updates) == (1, 1, 0)
    bill, change, reply_to = w.publisher.updates[0]
    assert (bill.number, reply_to) == (RCL, w.card_id(RCL))
    assert [st.stage_name for st in change.new_stages] == ["9. Stały Komitet Rady Ministrów"]
    text = MessageFormatter("ru").status_update(bill, change).text
    assert "Обновление — UC164" in text and "• 9. Stały Komitet Rady Ministrów" in text
    assert "Что дальше:</b> комитеты Совета министров и Komisja Prawnicza" in text


def test_unchanged_project_is_not_read_again_and_posts_nothing() -> None:
    w = World()
    _followed(w)
    reads = w.rcl.calls.count("get_project:12414100")

    report = w.run(full_track=True)

    assert (report.tracked, report.updates) == (1, 0)
    assert w.rcl.calls.count("get_project:12414100") == reads + 1
    assert not any(c.startswith("get_stage") for c in w.rcl.calls[-3:])  # catalogs unchanged


def test_consultation_opening_later_is_announced_with_the_deadline() -> None:
    w = World()
    early = rcl_project(consultation=None, stages=UZGODNIENIA_ONLY, modified=dt.date(2026, 9, 2))
    _followed(w, early)
    w.add_rcl_project(
        _moved(
            early,
            rcl_stage(2, "Uzgodnienia"),
            rcl_stage(3, "Konsultacje publiczne", "active", *CONSULTATION_FOLDERS),
            early.stages[2],
            modified=dt.date(2026, 9, 8),
        )
    )

    report = w.run()

    assert report.updates == 1
    bill, change, _ = w.publisher.updates[0]
    assert bill.consultation is not None and bill.consultation.end == dt.date(2026, 9, 8)
    text = MessageFormatter("ru").status_update(bill, change, today=dt.date(2026, 9, 8)).text
    assert "• 3. Konsultacje publiczne" in text
    assert "направить замечания на dep.prawny@mswia.gov.pl до 08.09.2026" in text


def test_deadline_reminder_uses_the_letter_and_is_sent_once() -> None:
    w = World()  # clock: 2026-09-07; the letter gives 7 days from 2026-09-01
    _followed(w)  # first run posts the card; the reminder is due (3 days ahead) on this run too

    reminders = list(w.publisher.consultations)
    w.clock.advance(days=1)
    w.run()

    assert [b.number for b, _, _ in reminders] == [RCL]
    assert len(w.publisher.consultations) == 1
    pub = w.publication(RCL, PublicationKind.CONSULTATION_DEADLINE)
    assert pub is not None and pub.message_id is not None


def test_published_opinions_are_announced_once() -> None:
    w = World()
    project = _followed(w)
    with_positions = tuple(
        rcl_folder(
            f.id, f.name, rcl_document(900, "uwagi_fundacji.pdf", created=dt.date(2026, 9, 8))
        )
        if f.kind == "positions"
        else f
        for f in CONSULTATION_FOLDERS
    )
    w.add_rcl_project(
        _moved(
            project,
            *project.stages[:2],
            rcl_stage(
                3, "Konsultacje publiczne", "reached", *with_positions, modified=dt.date(2026, 9, 8)
            ),
            *project.stages[3:],
            modified=dt.date(2026, 9, 8),
        )
    )

    report = w.run()
    w.clock.advance(days=1)
    again = w.run()

    assert (report.consultation_results_posted, again.consultation_results_posted) == (1, 0)
    assert report.updates == 0  # uploads into a folder are not a stage change
    bill, _ = w.publisher.consultation_results[0]
    assert bill.rcl is not None and bill.rcl.consultation is not None
    assert bill.rcl.consultation.positions == 1


def test_new_text_version_is_re_analysed_and_the_update_lists_the_changes() -> None:
    w = World()
    project = _followed(w)
    new_text = rcl_folder(
        777, "Projekt", rcl_document(801, "projekt_po_KP.pdf", created=dt.date(2026, 9, 8))
    )
    w.add_rcl_project(
        _moved(
            project,
            *project.stages[:4],
            rcl_stage(9, "Stały Komitet Rady Ministrów", "reached", modified=dt.date(2026, 9, 8)),
            rcl_stage(10, "Komisja Prawnicza", "active", new_text, modified=dt.date(2026, 9, 8)),
            *project.stages[5:],
            modified=dt.date(2026, 9, 8),
        )
    )

    report = w.run()

    assert (report.updates, report.reanalyzed) == (1, 1)
    bill, change, _ = w.publisher.updates[0]
    assert change.content_changed
    assert bill.analysis is not None and bill.analysis.revision == 2
    assert bill.analysis.source_url == new_text.documents[0].url
    assert w.llm.contexts[-1].previous_summary is not None


def test_hand_over_to_the_sejm_then_the_druk_continues_the_thread() -> None:
    w = World()
    project = _followed(w)
    w.rcl.put(
        _moved(
            project,
            *project.stages[:6],
            rcl_stage(
                14, "Skierowanie projektu ustawy do Sejmu", "active", modified=dt.date(2026, 9, 8)
            ),
            modified=dt.date(2026, 9, 8),
            rm_number="RM-0610-139-26",
            sejm_url="http://www.sejm.gov.pl/Sejm7.nsf/agent.xsp?symbol=RPL&Id=RM-0610-139-26",
        )
    )
    sent = w.run()
    card_id = w.card_id(RCL)
    w.clock.advance(days=1)
    druk = summary("3100", project.title, change="2026-09-09T09:00:00").model_copy(
        update={"rcl_num": "RM-0610-139-26"}
    )
    w.gateway.processes.append(druk)
    w.gateway.details["3100"] = detail(druk, START)

    linked = w.run()

    assert sent.updates == 1
    sent_text = MessageFormatter("ru").status_update(*w.publisher.updates[0][:2]).text
    assert "Проект направлен в Сейм — ждём номер druku" in sent_text
    assert (linked.published, linked.linked, linked.updates) == (0, 1, 1)
    assert len(w.publisher.new_bills) == 1  # no second card for the druk
    bill, change, reply_to = w.publisher.updates[1]
    assert (bill.number, bill.linked_number, reply_to) == ("3100", RCL, card_id)
    assert "Проекту присвоен номер druku: <b>3100</b>" in (
        MessageFormatter("ru").status_update(bill, change).text
    )
    assert w.bill(RCL).status is BillStatus.LINKED
    stored = w.bill("3100")
    assert stored.analysis is not None and stored.rcl is None


def test_druk_of_a_skipped_project_goes_the_normal_way() -> None:
    w = World()
    skipped = rcl_project(title="Projekt ustawy o zmianie niektórych ustaw", keywords=(), stages=())
    w.add_rcl_project(skipped)
    w.run()
    w.rcl.rm_numbers["RM-0610-1-26"] = skipped.id  # the RCL page does not show the RM number yet
    w.add_bill("3101", "Projekt ustawy o cudzoziemcach")
    druk = w.gateway.processes[-1].model_copy(
        update={"rcl_num": "RM-0610-1-26", "change_date": dt.datetime(2026, 9, 9, 9, 0)}
    )
    w.gateway.processes[-1] = druk
    w.gateway.details["3101"] = detail(druk, START)
    w.clock.advance(days=1)

    report = w.run()

    assert (report.discovered, report.published, report.linked) == (1, 1, 0)
    assert w.rcl.calls.count("resolve_project_id:RM-0610-1-26") == 1


def test_project_closed_on_rcl_ends_the_thread_once() -> None:
    w = World()
    project = _followed(w)
    w.rcl.put(project.model_copy(update={"status": "zamknięty", "modified": dt.date(2026, 9, 8)}))

    report = w.run()
    w.clock.advance(days=1)
    again = w.run()

    assert (report.updates, again.updates) == (1, 0)
    _, change, _ = w.publisher.updates[0]
    assert change.closure_detected and not change.new_stages


def test_rcl_outage_during_tracking_is_reported_and_sejm_tracking_goes_on() -> None:
    w = World()
    _followed(w)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.clock.advance(days=1)
    w.rcl.outages.add("get_project")

    report = w.run(full_track=True)

    assert report.tracked == 2  # the RCL project (failed) and the druk
    assert any("tracking: RCL: RCL unavailable" in e for e in report.errors)
