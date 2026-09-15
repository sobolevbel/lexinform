"""Following a government project on RCL: stages, consultation, new text, the way to the Sejm."""

import datetime as dt

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import BillStatus, PublicationKind, RclProject
from tests.fakes import FakeTextExtractor
from tests.harness import (
    CONSULTATION_FOLDERS,
    ELI,
    RCL,
    START,
    World,
    act,
    detail,
    rcl_document,
    rcl_folder,
    rcl_project,
    rcl_stage,
    summary,
)

RM_DATE = dt.date(2026, 9, 8)
"""The day the project was handed to the Sejm on the fake RCL page."""

UZGODNIENIA_ONLY = (
    rcl_stage(2, "Uzgodnienia", "active"),
    rcl_stage(3, "Konsultacje publiczne", "not_started", modified=None),
    rcl_stage(14, "Skierowanie projektu ustawy do Sejmu", "not_started", modified=None),
)


def _followed_project(w: World, project: RclProject | None = None) -> RclProject:
    """A project discovered and published on the first run; the clock moved on by a day."""
    project = w.add_rcl_project(project)
    w.run()
    w.clock.advance(days=1)
    return project


def _moved(project: RclProject, *stages: object, **fields: object) -> RclProject:
    return project.model_copy(update={"stages": tuple(stages), **fields})


def test_reached_stage_is_posted_once() -> None:
    w = World()
    project = _followed_project(w)
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
    # The header names the stage in the reader's language; the bullet keeps the original.
    assert "<b>Постоянный комитет Совета министров — UC164</b>" in text
    assert "• <i>9. Stały Komitet Rady Ministrów</i>" in text
    assert "Что дальше:</b> комитеты Совета министров и Komisja Prawnicza" in text


def test_unchanged_project_is_not_read_again_and_posts_nothing() -> None:
    w = World()
    _followed_project(w)
    reads = w.rcl.calls.count("get_project:12414100")

    report = w.run(full_track=True)

    assert (report.tracked, report.updates) == (1, 0)
    assert w.rcl.calls.count("get_project:12414100") == reads + 1
    assert not any(c.startswith("get_stage") for c in w.rcl.calls[-3:])  # catalogs unchanged


def test_consultation_opening_later_is_announced_with_the_deadline() -> None:
    w = World()
    early = rcl_project(consultation=None, stages=UZGODNIENIA_ONLY, modified=dt.date(2026, 9, 2))
    _followed_project(w, early)
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
    assert "• <i>3. Konsultacje publiczne</i>" in text
    assert "направить замечания на dep.prawny@mswia.gov.pl до 08.09.2026" in text
    assert change.consultation_opened and "#консультации" in text


def test_a_consultation_that_opens_without_a_new_stage_is_still_named() -> None:
    """The letter can appear under a stage the timeline already shows as reached, and then the
    post has no new stage to be named after: it went out headed «Обновление», over the one
    moment in the life of a government project that a reader can act on."""
    w = World()
    reached = rcl_stage(3, "Konsultacje publiczne", "reached")
    early = rcl_project(consultation=None, stages=(rcl_stage(2, "Uzgodnienia"), reached))
    _followed_project(w, early)
    w.add_rcl_project(
        _moved(
            early,
            early.stages[0],
            rcl_stage(
                3,
                "Konsultacje publiczne",
                "reached",
                *CONSULTATION_FOLDERS,
                modified=dt.date(2026, 9, 8),
            ),
            modified=dt.date(2026, 9, 8),
        )
    )

    report = w.run()

    assert report.updates == 1
    bill, change, _ = w.publisher.updates[0]
    assert change.new_stages == [] and change.consultation_opened
    text = MessageFormatter("ru").status_update(bill, change, today=dt.date(2026, 9, 8)).text
    assert "🗣 <b>Открылись публичные консультации — UC164</b>" in text
    assert "направить замечания на dep.prawny@mswia.gov.pl до 08.09.2026" in text


def test_a_window_that_has_already_shut_is_not_announced_as_opening() -> None:
    """Reading the letter late is not an event: a project taken by its text arrives with no
    window at all, and the run that finally reads one must not invite the reader to a door that
    closed weeks ago. The data is still repaired, quietly — the card is written from it."""
    w = World()
    reached = rcl_stage(3, "Konsultacje publiczne", "reached")
    early = rcl_project(consultation=None, stages=(rcl_stage(2, "Uzgodnienia"), reached))
    _followed_project(w, early)
    w.clock.advance(days=30)  # the letter gave seven days from 01.09
    w.add_rcl_project(
        _moved(
            early,
            early.stages[0],
            rcl_stage(
                3,
                "Konsultacje publiczne",
                "reached",
                *CONSULTATION_FOLDERS,
                modified=dt.date(2026, 10, 8),
            ),
            modified=dt.date(2026, 10, 8),
        )
    )

    w.run()

    stored = w.bill(RCL)
    assert stored.rcl is not None and stored.rcl.consultation is not None
    assert stored.rcl.consultation.deadline == dt.date(2026, 9, 8)
    assert not any(change.consultation_opened for _, change, _ in w.publisher.updates)


def test_deadline_reminder_uses_the_letter_and_is_sent_once() -> None:
    w = World()  # clock: 2026-09-07; the letter gives 7 days from 2026-09-01
    _followed_project(
        w
    )  # first run posts the card; the reminder is due (3 days ahead) on this run too

    reminders = list(w.publisher.consultations)
    w.clock.advance(days=1)
    w.run()

    assert [b.number for b, _, _ in reminders] == [RCL]
    assert len(w.publisher.consultations) == 1
    pub = w.publication(RCL, PublicationKind.CONSULTATION_DEADLINE)
    assert pub is not None and pub.message_id is not None


def test_failed_opinions_notice_is_retried_on_the_next_run() -> None:
    w = World()
    project = _followed_project(w)
    with_positions = tuple(
        rcl_folder(f.id, f.name, rcl_document(900, "uwagi.pdf", created=dt.date(2026, 9, 8)))
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
    w.publisher.fail_on = {RCL}
    failed = w.run()
    w.publisher.fail_on = set()
    w.clock.advance(days=1)

    retried = w.run()

    assert (failed.consultation_results_posted, retried.consultation_results_posted) == (0, 1)
    assert len(w.publisher.consultation_results) == 1
    stored = w.bill(RCL).rcl
    assert stored is not None and stored.consultation is not None
    assert stored.consultation.positions == 1


def test_published_opinions_are_announced_once() -> None:
    w = World()
    project = _followed_project(w)
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
    project = _followed_project(w)
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


# 426,000 characters, far over any per-bill limit, and dense with hits from end to end: what
# the cut keeps then really does fill the budget it was cut to, which is where the count of the
# excerpt can come back over the limit the whole text was scaled to fit.
_TOO_BIG = "Art. 500. Przepis dotyczy cudzoziemców przebywających na terytorium RP. " * 6_000


def _new_text_stage(w: World, project: RclProject) -> RclProject:
    """The project with a fresh redaction of its text under Komisja Prawnicza."""
    return w.add_rcl_project(
        _moved(
            project,
            *project.stages[:4],
            rcl_stage(9, "Stały Komitet Rady Ministrów", "reached", modified=dt.date(2026, 9, 8)),
            rcl_stage(
                10,
                "Komisja Prawnicza",
                "active",
                rcl_folder(
                    777,
                    "Projekt",
                    rcl_document(801, "projekt_po_KP.pdf", created=dt.date(2026, 9, 8)),
                ),
                modified=dt.date(2026, 9, 8),
            ),
            *project.stages[5:],
            modified=dt.date(2026, 9, 8),
        )
    )


def test_a_re_analysis_too_big_to_cut_down_is_read_anyway_rather_than_failing() -> None:
    """The per-bill guard refuses a first analysis and never a re-analysis.

    A refusal here has nowhere to go: `reanalyze_bill` is called from the tracking loop, whose
    per-bill `except` would count a failure and move on, so the same text would be offered and
    refused every run, with no `skipped_cost` row to `reset` and no `/unskip` to undo — and the
    card would go on describing the text before this one.
    """
    extractor = FakeTextExtractor()
    w = World(extractor=extractor, max_bill_cost_usd=0.01, text_budget_chars=1_000_000)
    project = _followed_project(w)
    extractor.text = _TOO_BIG  # over the limit by ~100x: nothing left to cut down to

    _new_text_stage(w, project)

    report = w.run()

    assert (report.reanalyzed, report.errors) == (1, [])
    analysis = w.bill(RCL).analysis
    assert analysis is not None and analysis.revision == 2


def test_a_re_analysis_that_counts_over_the_limit_after_the_cut_is_still_read() -> None:
    """The second refusal, and the live one: what `excerpts` keeps tokenizes worse than the ratio
    the whole document measured, so the cut text can still count over the limit."""
    extractor = FakeTextExtractor()
    w = World(extractor=extractor, max_bill_cost_usd=0.30, text_budget_chars=1_000_000)
    project = _followed_project(w)
    extractor.text = _TOO_BIG
    w.llm.count_overshoot = 1.5

    _new_text_stage(w, project)

    report = w.run()

    assert (report.reanalyzed, report.errors) == (1, [])
    assert w.llm.contexts[-1].truncated  # cut to fit, then sent even though it did not


def test_the_runs_cost_limit_holds_a_re_analysis_back_until_the_next_run() -> None:
    # The per-bill guard does not apply to a re-analysis on purpose, and until the limit covered
    # the tracking phase too, a new text was read whatever the run had already spent.
    w = World(max_run_cost_usd=0.001)
    w.llm.MODEL = "claude-opus-5"  # priced: 100 in + 50 out per call ≈ $0.002
    project = _followed_project(w)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")  # spends the budget before tracking
    new_text = rcl_folder(
        777, "Projekt", rcl_document(801, "projekt_po_KP.pdf", created=dt.date(2026, 9, 8))
    )
    moved = _moved(
        project,
        *project.stages[:4],
        rcl_stage(9, "Stały Komitet Rady Ministrów", "reached", modified=dt.date(2026, 9, 8)),
        rcl_stage(10, "Komisja Prawnicza", "active", new_text, modified=dt.date(2026, 9, 8)),
        *project.stages[5:],
        modified=dt.date(2026, 9, 8),
    )
    w.add_rcl_project(moved)

    held = w.run()

    assert held.reanalyzed == 0 and not held.errors
    assert "tracking: run cost limit reached" in " ".join(held.notes)
    held_analysis = w.bill(RCL).analysis
    assert held_analysis is not None and held_analysis.revision == 1

    w.clock.advance(days=1)
    again = w.run()

    assert again.reanalyzed == 1  # nothing was written down, so the next run reads the same text
    analysis = w.bill(RCL).analysis
    assert analysis is not None and analysis.source_url == new_text.documents[0].url


def test_republished_identical_text_is_not_analysed_again() -> None:
    w = World()
    project = _followed_project(w)
    analysed = w.bill(RCL).analysis
    assert analysed is not None
    consulted = next(f for st in project.stages for f in st.folders if f.kind == "project")
    # Komisja Prawnicza republishes the same three files (projekt, uzasadnienie, OSR) in its
    # own folder: new URLs, same text.
    same_files = rcl_folder(
        777,
        "Projekt",
        *(
            rcl_document(900 + i, doc.name, created=dt.date(2026, 9, 8))
            for i, doc in enumerate(consulted.documents)
        ),
    )
    w.add_rcl_project(
        _moved(
            project,
            *project.stages[:4],
            rcl_stage(9, "Stały Komitet Rady Ministrów", "reached", modified=dt.date(2026, 9, 8)),
            rcl_stage(10, "Komisja Prawnicza", "active", same_files, modified=dt.date(2026, 9, 8)),
            *project.stages[5:],
            modified=dt.date(2026, 9, 8),
        )
    )

    report = w.run()

    assert (report.updates, report.reanalyzed) == (1, 0)  # the stage is news, the text is not
    bill, change, _ = w.publisher.updates[0]
    assert not change.content_changed
    assert bill.analysis is not None and bill.analysis.revision == analysed.revision
    assert bill.analysis.source_url == same_files.documents[0].url  # repointed, not re-read
    assert len(w.llm.contexts) == 1


def test_hand_over_to_the_sejm_then_the_druk_continues_the_thread() -> None:
    w = World()
    project = _followed_project(w)
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
    update_text = MessageFormatter("ru").status_update(bill, change).text
    assert "Проекту присвоен номер druku: <b>3100</b>" in update_text
    assert "#kadencja10druk3100 #UC164" in update_text  # both tags find the thread
    assert w.bill(RCL).status is BillStatus.LINKED
    stored = w.bill("3100")
    assert stored.analysis is not None and stored.rcl is None
    assert stored.linked_wykaz_number == "UC164"
    # Every edit lands on the card: first the hand-over to the Sejm, then the druk's own card
    # in the same message, carrying both tags.
    assert {message for _, message in w.publisher.edits} == {card_id}
    edited, _ = w.publisher.edits[-1]
    assert edited.number == "3100"
    assert "#kadencja10druk3100 #UC164" in MessageFormatter("ru").new_bill(edited, None).text


def test_druk_of_a_skipped_project_goes_the_normal_way() -> None:
    w = World()
    skipped = rcl_project(title="Projekt ustawy o zmianie niektórych ustaw", keywords=(), stages=())
    w.add_rcl_project(skipped)
    w.run()
    w.rcl.rm_numbers["RM-0610-1-26"] = skipped.id  # the RCL page does not show the RM number yet
    w.add_bill("3101", "Projekt ustawy o cudzoziemcach")
    druk = w.gateway.processes[-1].model_copy(
        update={
            "rcl_num": "RM-0610-1-26",
            "change_date": dt.datetime(2026, 9, 9, 9, 0, tzinfo=dt.UTC),
        }
    )
    w.gateway.processes[-1] = druk
    w.gateway.details["3101"] = detail(druk, START)
    w.clock.advance(days=1)

    report = w.run()

    assert (report.discovered, report.published, report.linked) == (1, 1, 0)
    assert w.rcl.calls.count("resolve_project_id:RM-0610-1-26") == 1


def test_project_closed_on_rcl_ends_the_thread_once() -> None:
    w = World()
    project = _followed_project(w)
    w.rcl.put(project.model_copy(update={"status": "zamknięty", "modified": dt.date(2026, 9, 8)}))

    report = w.run()
    w.clock.advance(days=1)
    again = w.run()

    assert (report.updates, again.updates) == (1, 0)
    _, change, _ = w.publisher.updates[0]
    assert change.closure_detected and not change.new_stages


def test_rcl_outage_during_tracking_is_reported_and_sejm_tracking_goes_on() -> None:
    w = World()
    _followed_project(w)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.clock.advance(days=1)
    w.rcl.outages.add("get_project")

    report = w.run(full_track=True)

    assert report.tracked == 2  # the RCL project (failed) and the druk
    assert any("tracking: RCL: RCL unavailable" in e for e in report.errors)


def test_a_handed_over_project_asks_the_sejm_for_its_druk_every_run() -> None:
    """Sejm discovery stamps the print number only on the run that first sees the druk; without
    a second chance a project whose process carries no `rclNum` stays at "sent to the Sejm"."""
    w = World()
    project = _followed_project(w)
    w.rcl.put(
        _moved(
            project,
            *project.stages[:6],
            rcl_stage(
                14, "Skierowanie projektu ustawy do Sejmu", "active", modified=dt.date(2026, 9, 8)
            ),
            modified=dt.date(2026, 9, 8),
            rm_number="RM-0610-139-26",
        )
    )
    w.run()
    card_id = w.card_id(RCL)

    # The druk exists, and discovery does not run: only tracking can find it.
    w.clock.advance(days=1)
    druk = summary("3100", project.title, change="2026-09-09T09:00:00").model_copy(
        update={"rcl_num": "RM-0610-139-26"}
    )
    w.gateway.processes.append(druk)
    w.gateway.details["3100"] = detail(druk, START)

    linked = w.run(discover=False)

    assert linked.linked == 1
    assert w.bill(RCL).status is BillStatus.LINKED
    assert w.publication("3100", PublicationKind.NEW_BILL) is not None
    assert w.card_id("3100") == card_id


def test_the_druk_of_a_project_whose_act_is_out_brings_the_act_with_it() -> None:
    """RCL project 12405609 was picked up on 2026-09-13; its druk 2172 had been Dz.U. 2026
    poz. 203 since February and in force since March. Nothing fetched that act — the print is
    created during the tracking phase, after the list `_check_processes` and `CardRefresher`
    work from was taken, and it is past every window `list_tracked` follows a closed bill for —
    so the thread was told «дальше: публикация в Dziennik Ustaw · без движения уже 6 мес.» over
    a law that had been applying for half a year, and nothing would ever have corrected it.
    """
    w = World()
    project = _followed_project(w)
    w.rcl.put(
        _moved(
            project,
            *project.stages[:6],
            rcl_stage(14, "Skierowanie projektu ustawy do Sejmu", "active", modified=RM_DATE),
            modified=RM_DATE,
            rm_number="RM-0610-139-26",
        )
    )
    w.run()
    w.clock.advance(days=1)
    druk = summary("3100", project.title, change="2026-09-09T09:00:00").model_copy(
        update={
            "rcl_num": "RM-0610-139-26",
            "closure_date": dt.date(2026, 2, 3),
            "passed": True,
            "eli": ELI,
            "display_address": "Dz.U. 2026 poz. 1099",
        }
    )
    w.gateway.processes.append(druk)
    w.gateway.details["3100"] = detail(druk, START)
    w.gateway.acts[ELI] = act(entry_into_force=dt.date(2026, 3, 5), in_force="IN_FORCE")

    report = w.run()

    assert (report.linked, report.acts_published) == (1, 1)
    stored = w.bill("3100")
    assert stored.act is not None and stored.act.entry_into_force == dt.date(2026, 3, 5)
    edited, message_id = w.publisher.edits[-1]
    assert (edited.number, message_id) == ("3100", w.card_id(RCL))
    card = MessageFormatter("ru").new_bill(edited, None).text
    assert "Dz.U. 2026 poz. 1099" in card
    assert "публикация в Dziennik Ustaw" not in card
    assert "без движения" not in card
