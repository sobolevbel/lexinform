"""Operator commands from the technical channel, end to end on the World harness: a command
waits in the inbox, the run executes it, answers under it and takes it out."""

import datetime as dt
from typing import Any

from lexinform.errors import LlmUnavailableError
from lexinform.models import BillStatus, OutcomeStatus, PublicationKind, RunMode, RunReport, Stage
from lexinform.services.commands import FORCE_HINT
from tests.fakes import FakeLlm, FakeTextExtractor, make_analysis
from tests.harness import (
    COMMITTEE_STAGES,
    RCL,
    RCL_ID,
    RPW,
    WYKAZ,
    World,
    rcl_project,
    submission,
)

TITLE = "Poselski projekt ustawy o zmianie ustawy o cudzoziemcach"
PLAIN = "Rządowy projekt ustawy o podatku VAT"  # says nothing about foreigners
TEXT_WITH_HITS = "Art. 1. Cudzoziemiec składa wniosek o zezwolenie na pobyt czasowy. " * 20
REJECTED_AT_FIRST_READING = Stage(
    stage_name="I czytanie na posiedzeniu Sejmu",
    stage_type="SejmReading",
    date=dt.date(2026, 1, 23),
    decision="odrzucono w pierwszym czytaniu",
)


def _commands_only(w: World, **options: Any) -> RunReport:
    """What `lexinform commands` runs: the commands phase and nothing else."""
    return w.run(
        mode=RunMode.COMMANDS, discover=False, track=False, max_analyze=0, max_publish=0, **options
    )


def test_analyze_fetches_an_unknown_bill_posts_its_card_and_follows_it() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.command("/analyze 3039")

    report = w.run()
    followed = w.run()

    assert (report.commands_handled, report.commands_failed) == (1, 0)
    assert report.commands == ["/analyze 3039 → 3039 analysed (message 101)"]
    assert [b.number for b, _ in w.publisher.new_bills] == ["3039"]  # once, not again by discovery
    assert w.bill("3039").status is BillStatus.ANALYZED
    (command, outcome), *_ = w.replier.replies
    assert command.text == "/analyze 3039"
    assert (outcome.status, outcome.message_id) == (OutcomeStatus.ANALYSED, 101)
    assert w.inbox.commands == [] and w.inbox.done_ids == [1]
    assert followed.tracked == 1  # the card is a followed bill from now on


def test_analyze_runs_the_text_prefilter_on_a_title_miss() -> None:
    w = World(extractor=FakeTextExtractor(TEXT_WITH_HITS))
    w.add_bill("4000", PLAIN)
    w.command("/analyze 4000")

    _commands_only(w)

    assert [b.number for b, _ in w.publisher.new_bills] == ["4000"]
    assert w.bill("4000").prefilter_hits  # the text hits are on record


def test_a_prefilter_miss_is_reported_with_the_reason_and_force_gets_past_it() -> None:
    w = World()
    w.add_bill("4000", PLAIN)
    w.command("/analyze 4000")
    w.command("/analyze 4000 force")

    _commands_only(w)

    (_, skipped), (_, forced) = w.replier.replies
    assert skipped.status is OutcomeStatus.SKIPPED
    assert "text prefilter: no keyword hits" in skipped.note and "force" in skipped.note
    assert forced.status is OutcomeStatus.ANALYSED and forced.message_id == 101
    assert len(w.llm.contexts) == 1  # the model was asked once: by the forced command


def test_an_analysed_bill_answers_from_the_stored_verdict() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.run()  # discovered, analysed and posted the usual way
    w.command("/analyze 3039")

    _commands_only(w)

    (_, outcome), *_ = w.replier.replies
    assert outcome.status is OutcomeStatus.ANALYSED and outcome.message_id is None
    assert outcome.note == "the card is in the channel already (message 101)"
    assert len(w.llm.contexts) == 1  # no second model call
    assert len(w.publisher.new_bills) == 1


def test_a_relevant_bill_under_the_threshold_is_posted_only_with_publish() -> None:
    w = World(llm_script={"3039": make_analysis(score=2)})
    w.add_bill("3039", TITLE)
    w.command("/analyze 3039")
    w.command("/analyze 3039 publish")

    _commands_only(w)

    (_, held), (_, posted) = w.replier.replies
    assert held.status is OutcomeStatus.ANALYSED and held.message_id is None
    assert "importance 2 is under the threshold 3" in held.note
    assert posted.message_id == 101
    assert len(w.llm.contexts) == 1  # the second command reused the analysis


def test_an_irrelevant_bill_gets_no_card() -> None:
    w = World(llm_script={"3039": make_analysis(relevant=False, score=1)})
    w.add_bill("3039", TITLE)
    w.command("/analyze 3039")

    _commands_only(w)

    (_, outcome), *_ = w.replier.replies
    assert outcome.status is OutcomeStatus.ANALYSED and outcome.note == "not relevant: no card"
    assert w.publisher.new_bills == []


def test_a_bill_nobody_knows_is_an_error_answered_under_the_command() -> None:
    w = World()
    w.command("/analyze 9999")

    report = _commands_only(w)

    (_, outcome), *_ = w.replier.replies
    assert outcome.status is OutcomeStatus.ERROR and "9999" in outcome.note
    assert (report.commands_handled, report.commands_failed) == (1, 1)
    assert w.inbox.commands == []  # answered: not tried again


def test_an_rcl_project_by_link_is_read_analysed_and_posted() -> None:
    w = World()
    w.add_rcl_project()
    w.command(f"/analyze https://legislacja.rcl.gov.pl/projekt/{RCL_ID}")

    _commands_only(w)

    assert [b.number for b, _ in w.publisher.new_bills] == [RCL]
    assert f"get_project:{RCL_ID}" in w.rcl.calls
    project = w.bill(RCL).rcl
    assert project is not None and project.consultation is not None


def test_wykaz_and_rm_numbers_name_the_project() -> None:
    w = World()
    w.add_rcl_project()
    w.rcl.rm_numbers["RM-0610-139-26"] = RCL_ID
    w.command("/analyze RM-0610-139-26")
    w.command("/show UC164")

    _commands_only(w)

    (_, analysed), (_, shown) = w.replier.replies
    assert analysed.status is OutcomeStatus.ANALYSED and analysed.message_id == 101
    assert shown.status is OutcomeStatus.SHOWN
    assert shown.bill is not None and shown.bill.number == RCL


def test_analyze_takes_a_planned_bill_by_its_wykaz_number() -> None:
    w = World()
    w.add_wykaz_entry()
    w.command("/analyze UD408")

    _commands_only(w)

    ((_, outcome),) = w.replier.replies
    assert outcome.status is OutcomeStatus.ANALYSED
    assert outcome.bill is not None and outcome.bill.number == WYKAZ
    assert [b.number for b, _ in w.publisher.new_bills] == [WYKAZ]


def test_a_wykaz_number_names_the_project_once_it_is_out_and_the_plan_before_that() -> None:
    w = World()
    w.add_wykaz_entry()
    w.run()
    w.command("/show UD408")
    _commands_only(w)

    w.add_rcl_project(rcl_project(wykaz_number="UD408"))
    w.run()
    w.command("/show UD408")
    _commands_only(w)

    (_, planned), (_, published) = w.replier.replies
    assert planned.bill is not None and planned.bill.number == WYKAZ
    assert published.bill is not None and published.bill.number == RCL


def test_a_wykaz_number_names_the_project_even_before_this_bot_has_walked_it() -> None:
    """The index of what RCL has listed answers for the operator too: asking after a plan whose
    project came out gives the row with the text, not a plan card promising one."""
    w = World()
    w.add_wykaz_entry()
    project = rcl_project(wykaz_number="UD408", created=dt.date(2026, 9, 2))
    w.add_rcl_project(project)
    w.rcl.listing.clear()  # never walked: the listing has not shown it as changed
    w.repo.remember_rcl_wykaz_number("UD408", project.id, project.created)
    w.command("/analyze UD408")

    _commands_only(w)

    ((_, outcome),) = w.replier.replies
    assert outcome.bill is not None and outcome.bill.number == RCL
    assert w.repo.find_wykaz(WYKAZ) is None  # no plan row, no second thread


def test_show_reads_the_database_only() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.command("/show 3039")

    _commands_only(w)

    (_, outcome), *_ = w.replier.replies
    assert outcome.status is OutcomeStatus.NOT_FOUND
    assert outcome.note == "druk 3039 is not in the database; /analyze fetches it"
    assert "get_process:3039" not in w.gateway.calls


def test_skip_silences_a_bill_and_republish_posts_the_card_again() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.run()
    w.command("/skip 3039")
    w.command("/republish 3039")

    _commands_only(w)

    (_, silenced), (_, republished) = w.replier.replies
    assert silenced.status is OutcomeStatus.SILENCED and "message 101" in silenced.note
    assert w.bill("3039").status is BillStatus.SKIPPED_PREFILTER
    assert republished.status is OutcomeStatus.REPUBLISHED and republished.message_id == 102
    assert len(w.publisher.new_bills) == 2
    assert w.card_id("3039") == 102  # updates reply to the new card from now on


def test_forget_drops_a_silenced_bills_card_and_stops_following_it() -> None:
    """The card was deleted from the channel by hand; `/republish` would put it back.

    Left alone, the `sent` row keeps the bill in `list_tracked` and the refresher goes on
    editing a message that is not there, once a run, for ever.
    """
    w = World()
    w.add_bill("3039", TITLE)
    w.run()
    w.command("/skip 3039")
    w.command("/forget 3039")

    _commands_only(w)
    w.set_stages("3039", COMMITTEE_STAGES)
    w.touch("3039", dt.datetime(2026, 9, 10, 9, 0))
    w.run()

    (_, _silenced), (_, forgotten) = w.replier.replies
    assert forgotten.status is OutcomeStatus.FORGOTTEN
    assert "message 101" in forgotten.note and "not be posted again" in forgotten.note
    assert w.publication("3039") is None
    assert len(w.publisher.new_bills) == 1  # nothing was posted in its place
    assert w.publisher.updates == []  # and the stage change reaches no thread


def test_forget_of_a_live_bill_lets_the_next_run_post_a_fresh_card() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.run()
    w.command("/forget 3039")

    _commands_only(w)
    first = w.publication("3039")
    w.run()

    (_, forgotten), *_ = w.replier.replies
    assert forgotten.status is OutcomeStatus.FORGOTTEN and "fresh card" in forgotten.note
    assert first is None
    assert len(w.publisher.new_bills) == 2
    assert w.card_id("3039") == 102


def test_forget_of_a_bill_the_channel_never_carried_changes_nothing() -> None:
    w = World(llm_script={"3039": make_analysis(relevant=False, score=1)})
    w.add_bill("3039", TITLE)
    w.run()
    w.command("/forget 3039")

    _commands_only(w)

    (_, outcome), *_ = w.replier.replies
    assert outcome.status is OutcomeStatus.FORGOTTEN and "nothing to forget" in outcome.note
    assert w.publisher.new_bills == []


def test_republish_refuses_a_bill_without_a_relevant_analysis() -> None:
    w = World(llm_script={"3039": make_analysis(relevant=False, score=1)})
    w.add_bill("3039", TITLE)
    w.run()
    w.command("/republish 3039")

    _commands_only(w)

    (_, outcome), *_ = w.replier.replies
    assert outcome.status is OutcomeStatus.ERROR and "no relevant analysis" in outcome.note
    assert w.publisher.new_bills == []


def test_help_unknown_commands_and_missing_references_get_the_command_list() -> None:
    w = World()
    w.command("/help")
    w.command("/delete 3039")
    w.command("/analyze")
    w.command("just a note in the channel")

    report = _commands_only(w)

    statuses = [o.status for _, o in w.replier.replies]
    notes = [o.note for _, o in w.replier.replies]
    assert statuses == [OutcomeStatus.HELP] * 4
    assert notes == [
        "",
        "unknown command /delete",
        "/analyze needs a bill number or a link",
        "not a command",
    ]
    assert report.commands_failed == 0


def test_a_command_file_read_again_is_not_executed_twice() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.run()
    handled = w.command("/republish 3039", update_id=7)
    _commands_only(w)
    w.inbox.commands.append(handled)  # the workflow could not push the deletion

    _commands_only(w)

    assert len(w.publisher.new_bills) == 2  # the first run's card and one republication
    assert len(w.replier.replies) == 1
    assert w.inbox.commands == [] and w.inbox.done_ids == [7, 7]


def test_an_outage_leaves_the_command_for_the_next_run() -> None:
    w = World(llm_script={"3039": LlmUnavailableError("overloaded")})
    w.add_bill("3039", TITLE)
    w.command("/analyze 3039")

    failed = _commands_only(w)
    unanswered = len(w.replier.replies)
    w.llm.script.clear()
    recovered = _commands_only(w)

    assert failed.errors == ["commands: LLM API unavailable: overloaded"]
    assert failed.commands_handled == 0 and unanswered == 0
    assert recovered.commands_handled == 1 and recovered.ok
    assert len(w.replier.replies) == 1 and w.inbox.commands == []


def test_a_command_whose_answer_did_not_arrive_is_answered_again_not_run_again() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.run()  # the card is in the channel
    w.command("/republish 3039")
    w.replier.outage = True

    unanswered = _commands_only(w)
    left_in_the_inbox = [c.text for c in w.inbox.commands]
    w.replier.outage = False
    repeated = _commands_only(w)

    assert unanswered.errors == [
        "commands: Telegram API unavailable: sendMessage: ConnectError after 3 attempts"
    ]
    assert unanswered.commands_handled == 0 and left_in_the_inbox == ["/republish 3039"]
    assert repeated.commands_handled == 1 and repeated.ok
    assert len(w.publisher.new_bills) == 2  # the first card and one republication, not two
    (_, outcome), *_ = w.replier.replies
    assert outcome.status is OutcomeStatus.EXECUTED_EARLIER
    assert "republished (message 102)" in outcome.note  # what that run did, from the record
    assert w.inbox.commands == []


def test_a_command_a_dead_run_started_is_not_run_a_second_time() -> None:
    """The two marks keep a second card away when the answer fails. They did not when the job
    itself died: the row was recorded and nothing else, so the next run began from the top."""
    w = World()
    w.add_bill("3039", TITLE)
    w.run()
    incoming = w.command("/republish 3039")
    w.repo.record_command(incoming)  # as the run that then died had already done

    report = _commands_only(w)

    assert report.commands_handled == 1 and report.ok
    assert len(w.publisher.new_bills) == 1  # the original card, no second one
    (_, outcome), *_ = w.replier.replies
    assert outcome.status is OutcomeStatus.EXECUTED_EARLIER
    assert "did not finish" in outcome.note
    assert w.inbox.commands == []


def test_a_channel_that_falls_over_mid_phase_keeps_what_the_earlier_commands_reported() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.add_bill("4001", TITLE)
    first = w.command("/analyze 3039")
    second = w.command("/analyze 4001")
    w.replier.outage_after = 1

    report = _commands_only(w)

    assert report.commands_handled == 1
    assert report.commands == ["/analyze 3039 → 3039 analysed (message 101)"]
    assert report.llm_input_tokens > 0  # the accounting of the answered command survived
    assert [c.update_id for c in w.inbox.commands] == [second.update_id]
    assert w.inbox.done_ids == [first.update_id]


def test_a_text_over_the_cost_limit_is_answered_with_the_reason_and_force_pays_for_it() -> None:
    w = World(max_bill_cost_usd=0.0001)
    w.add_bill("3039", TITLE)
    w.command("/analyze 3039")
    w.command("/analyze 3039 force")

    _commands_only(w)

    (_, guarded), (_, forced) = w.replier.replies
    assert guarded.status is OutcomeStatus.SKIPPED
    assert "exceeds the $0.000 limit" in guarded.note and FORCE_HINT in guarded.note
    assert guarded.bill is not None and guarded.bill.status is BillStatus.SKIPPED_COST
    assert forced.status is OutcomeStatus.ANALYSED and forced.message_id == 101


def test_a_bill_silenced_by_the_operator_says_so_instead_of_blaming_the_prefilter() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.run()
    w.command("/skip 3039")
    w.command("/analyze 3039")

    _commands_only(w)

    (_, _silenced), (_, refused) = w.replier.replies
    assert refused.status is OutcomeStatus.SKIPPED
    assert refused.note == f"silenced by the operator (/skip); {FORCE_HINT}"
    assert len(w.publisher.new_bills) == 1  # the card of the first run, nothing new


def test_a_dry_run_answers_but_keeps_the_inbox_and_the_database() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.command("/analyze 3039")

    report = _commands_only(w, dry_run=True)

    assert report.commands_handled == 1 and len(w.replier.replies) == 1
    assert len(w.inbox.commands) == 1 and w.inbox.done_ids == []
    assert w.repo.get(10, "3039") is None  # rolled back


def test_a_commands_run_reports_to_the_log_channel_only_when_a_phase_failed() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.command("/analyze 9999")  # answered under the command with the error: no report needed
    _commands_only(w)
    quiet = len(w.notifier.calls)
    w.gateway.outages.add("get_process")
    w.command("/analyze 3039")

    _commands_only(w)

    assert quiet == 0
    assert len(w.notifier.calls) == 1
    report, _ = w.notifier.calls[0]
    assert report.errors == ["commands: Sejm API unavailable: get_process: connection refused"]


def test_the_card_a_command_posts_settles_the_bill_for_publishing() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.command("/analyze 3039")
    _commands_only(w)

    report = w.run()

    assert report.published == 0 and len(w.publisher.new_bills) == 1
    card = w.publication("3039", PublicationKind.NEW_BILL)
    assert card is not None and card.message_id == 101


def test_the_reply_carries_the_run_time_and_what_the_analysis_cost() -> None:
    w = World(triage=True)
    w.add_bill("3039", TITLE)
    w.command("/analyze 3039")
    w.command("/show 3039")

    _commands_only(w)

    (_, analysed), (_, shown) = w.replier.replies
    assert analysed.run_started_at == w.clock.now()
    assert analysed.seconds is not None and analysed.seconds >= 0
    # Both model calls are on the operator's bill: the triage and the analysis itself.
    assert analysed.usage["fake"].input == FakeLlm.ANALYSIS_TOKENS[0]
    assert analysed.usage["fake-triage"].input == FakeLlm.TRIAGE_TOKENS[0]
    assert shown.usage == {}  # /show never calls the model


def test_a_project_that_already_reached_the_sejm_leads_to_its_druk() -> None:
    """UD345: the project went to the Sejm months ago and the act is in force, but a command
    on the RCL number used to publish a card promising a druk number any day now."""
    w = World()
    project = w.add_rcl_project(rcl_project(rm_number="RM-0610-7-26", consultation=None))
    w.add_bill("2172", TITLE)
    w.gateway.processes = [
        p.model_copy(update={"rcl_num": "RM-0610-7-26"}) if p.number == "2172" else p
        for p in w.gateway.processes
    ]
    w.command(f"/analyze RCL/{project.id}")

    _commands_only(w)

    (_, outcome), *_ = w.replier.replies
    assert outcome.bill is not None and outcome.bill.number == "2172"  # the druk, not the project
    assert [b.number for b, _ in w.publisher.new_bills] == ["2172"]
    assert w.bill(RCL).status is BillStatus.LINKED  # the project joins the druk's thread
    assert w.bill(RCL).linked_number == "2172"
    assert w.bill("2172").linked_wykaz_number == "UC164"


def test_a_druk_that_continues_a_followed_project_joins_its_card() -> None:
    """The project is in the channel already; its druk named by hand must not get a second card
    (discovery links the two only for a druk it sees first, and a command may name it earlier)."""
    w = World()
    w.add_rcl_project(rcl_project(rm_number="RM-0610-7-26", consultation=None))
    w.run()
    card_id = w.card_id(RCL)
    w.add_bill("2172", TITLE)
    w.touch("2172", dt.datetime(2026, 9, 9, 9, 0), rcl_num="RM-0610-7-26")
    w.command("/analyze 2172")

    _commands_only(w)

    (_, outcome), *_ = w.replier.replies
    assert outcome.message_id == card_id  # the project's card, not a second one
    assert len(w.publisher.new_bills) == 1
    assert w.bill(RCL).status is BillStatus.LINKED
    assert w.bill("2172").linked_wykaz_number == "UC164"  # the card's tag stays with the thread


def test_an_entry_that_already_has_its_druk_leads_to_the_druk() -> None:
    """The same the other way round: the operator names an RPW number nobody followed, and the
    Sejm gave it a druk in the meantime. The druk has the text; the entry has a promise."""
    w = World()
    w.add_bill("3039", TITLE)
    w.gateway.submissions.append(submission(print_number="3039"))
    w.command(f"/analyze {RPW}")

    _commands_only(w)

    (_, outcome), *_ = w.replier.replies
    assert outcome.bill is not None and outcome.bill.number == "3039"  # the druk, not the entry
    assert [b.number for b, _ in w.publisher.new_bills] == ["3039"]
    assert (w.bill(RPW).status, w.bill(RPW).linked_number) == (BillStatus.LINKED, "3039")
    assert w.bill("3039").linked_number == RPW
    assert w.bill("3039").submission is not None  # the entry's consultation dates travel along


def test_a_druk_that_continues_a_followed_entry_joins_its_card() -> None:
    """The entry is in the channel already: its druk inherits the card instead of getting a
    second one, exactly as tracking would have linked the two on the next run."""
    w = World()
    w.gateway.submissions.append(submission())
    w.run()
    card_id = w.card_id(RPW)
    w.gateway.submissions[0] = submission(print_number="3100")
    w.add_bill("3100", TITLE)
    w.command("/analyze 3100")

    _commands_only(w)

    (_, outcome), *_ = w.replier.replies
    assert outcome.message_id == card_id  # the entry's card, not a second one
    assert len(w.publisher.new_bills) == 1
    assert (w.bill(RPW).status, w.bill(RPW).linked_number) == (BillStatus.LINKED, "3100")
    assert w.bill("3100").linked_number == RPW
    card = w.publication("3100")
    assert card is not None and card.message_id == card_id
    edited, edited_message = w.publisher.edits[0]  # the card carries the druk's tag now
    assert (edited.number, edited_message) == (RPW, card_id)


def test_a_bill_whose_road_has_ended_gets_a_verdict_but_no_card() -> None:
    w = World()
    w.add_bill("2172", TITLE)
    w.touch("2172", dt.datetime(2026, 1, 23), closure_date=dt.date(2026, 1, 23), passed=False)
    w.command("/analyze 2172")

    _commands_only(w)

    (_, outcome), *_ = w.replier.replies
    assert outcome.status is OutcomeStatus.ANALYSED and outcome.message_id is None
    assert outcome.note == "the process ended on 2026-01-23 (closed without a law): not posted"
    assert w.publisher.new_bills == []


def test_the_verdict_names_how_the_road_ended() -> None:
    """A rejection, a withdrawal and a veto that stood are three different answers to "why is
    there no card", and the channel already tells them apart for its readers."""
    w = World()
    w.add_bill("2172", TITLE, stages=(REJECTED_AT_FIRST_READING,))
    w.touch("2172", dt.datetime(2026, 1, 23), closure_date=dt.date(2026, 1, 23), passed=False)
    w.command("/analyze 2172")

    _commands_only(w)

    (_, outcome), *_ = w.replier.replies
    assert outcome.note == "the process ended on 2026-01-23 (rejected): not posted"


def test_a_dropped_plan_is_not_reported_as_a_law_that_was_not_enacted() -> None:
    """A plan the government took off the wykaz never had a Sejm process to close."""
    w = World()
    w.add_wykaz_entry(status="Wycofany")
    w.command("/analyze UD408")

    _commands_only(w)

    (_, outcome), *_ = w.replier.replies
    assert outcome.note == "the process ended (dropped from the government's plan): not posted"


def test_unskip_puts_a_silenced_bill_back_in_the_queue() -> None:
    """`/skip` is the operator's only reversible mistake: the way back must be a command too,
    and not a laptop with the production dump on it."""
    w = World()
    w.add_bill("3039", TITLE)
    w.run()  # discovered, analysed, posted
    w.command("/skip 3039")
    _commands_only(w)
    w.command("/unskip 3039")

    _commands_only(w)

    (_, silenced), (_, queued) = w.replier.replies
    assert silenced.status is OutcomeStatus.SILENCED
    assert queued.status is OutcomeStatus.QUEUED
    assert queued.note == "was skipped_prefilter; the next run analyses it"
    assert w.bill("3039").status is BillStatus.ANALYSIS_PENDING
    assert w.bill("3039").analysis_attempts == 0  # a clean budget, not the spent one


def test_unskip_of_an_analysed_bill_does_not_pay_for_the_model_again() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.run()
    w.command("/unskip 3039")

    _commands_only(w)

    (_, outcome), *_ = w.replier.replies
    assert outcome.status is OutcomeStatus.QUEUED and "already analysed" in outcome.note
    assert w.bill("3039").status is BillStatus.ANALYZED


def test_preview_renders_the_card_without_posting_it() -> None:
    w = World(llm_script={"3039": make_analysis(score=2)})  # under the threshold: never posted
    w.add_bill("3039", TITLE)
    w.command("/analyze 3039")
    _commands_only(w)
    w.command("/preview 3039")

    _commands_only(w)

    (_, analysed), (_, preview) = w.replier.replies
    assert analysed.message_id is None
    assert preview.status is OutcomeStatus.PREVIEWED
    assert preview.bill is not None and preview.print_info is not None
    assert preview.note == "not posted to the channel"
    assert w.publisher.new_bills == []  # a preview is an answer, not a post


def test_preview_of_a_joint_print_shows_the_reply_the_channel_would_get() -> None:
    """A print considered jointly with one already in the channel gets a short reply, not a
    card. A preview that rendered a card would show the operator the one message `/republish`
    would never send."""
    w = World()
    w.add_bill("1933", TITLE)
    w.run()
    w.add_bill("1929", "Rządowy projekt ustawy o zmianie ustawy o cudzoziemcach")
    w.touch("1929", dt.datetime(2026, 9, 8, 9, 0), prints_considered_jointly=("1933",))
    w.run()
    w.command("/preview 1929")

    _commands_only(w)

    (_, outcome), *_ = w.replier.replies
    assert outcome.status is OutcomeStatus.PREVIEWED
    assert outcome.joint_primary is not None and outcome.joint_primary.number == "1933"
    assert outcome.note == (
        f"considered jointly with 1933: a reply under its card"
        f" (message {w.card_id('1933')}), not a card of its own"
    )


def test_preview_of_a_bill_without_an_analysis_says_there_is_no_card() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.run(max_analyze=0)  # in the database, waiting for the model
    w.command("/preview 3039")

    _commands_only(w)

    (_, outcome), *_ = w.replier.replies
    assert outcome.status is OutcomeStatus.ERROR
    assert outcome.note == "not analysed: no card to render"


def test_refresh_posts_the_update_a_scheduled_run_would_have_found() -> None:
    """The Sejm moves when it moves; the operator should not have to wait for 05:23 UTC."""
    w = World()
    w.add_bill("3039", TITLE)
    w.run()
    w.set_stages("3039", COMMITTEE_STAGES)
    w.clock.advance(days=1)
    w.command("/refresh 3039")

    report = _commands_only(w)

    (_, outcome), *_ = w.replier.replies
    assert outcome.status is OutcomeStatus.REFRESHED and "1 post(s)" in outcome.note
    bill, change, reply_to = w.publisher.updates[0]
    assert (bill.number, reply_to) == ("3039", w.card_id("3039"))
    assert [st.stage_type for st in change.new_stages] == ["ReadingReferral", "Referral"]
    assert report.commands_failed == 0


def test_refresh_of_an_unchanged_bill_says_so_and_posts_nothing() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.run()
    w.command("/refresh 3039")

    _commands_only(w)

    (_, outcome), *_ = w.replier.replies
    assert outcome.note == "nothing new: the card and the stages are as they were"
    assert w.publisher.updates == []


def test_refresh_of_a_bill_with_no_card_posts_nothing_into_the_channel() -> None:
    """A scheduled run only ever tracks bills that have a card; every post replies under it.
    Tracking one analysed below the threshold would drop an update into the channel with
    nothing above it — the reader would meet a bill at its committee stage and never learn
    which bill it is."""
    w = World(llm_script={"3039": make_analysis(score=2)})
    w.add_bill("3039", TITLE)
    w.command("/analyze 3039")
    _commands_only(w)
    w.set_stages("3039", COMMITTEE_STAGES)
    w.clock.advance(days=1)
    w.command("/refresh 3039")

    _commands_only(w)

    (_, analysed), (_, refreshed) = w.replier.replies
    assert analysed.message_id is None  # never posted
    assert refreshed.status is OutcomeStatus.ERROR
    assert "no card in the channel" in refreshed.note
    assert w.publisher.updates == []


def test_refresh_names_the_source_that_was_down_instead_of_saying_nothing_is_new() -> None:
    """RCL is unreachable from a GitHub-hosted runner altogether, so "nothing new" about a page
    nobody read is the answer the operator would get every single time."""
    w = World()
    w.add_rcl_project(rcl_project(consultation=None))
    w.run()
    w.rcl.outages.add("get_project")
    w.command(f"/refresh RCL/{RCL_ID}")

    _commands_only(w)

    (_, outcome), *_ = w.replier.replies
    assert outcome.status is OutcomeStatus.REFRESHED
    assert outcome.note.startswith("nothing new from the sources that answered · not read: RCL:")


def test_unskip_of_a_linked_row_leaves_the_card_to_the_bill_that_continues_it() -> None:
    """The row handed its thread over: its card is the druk's now, rendered from the druk's
    state. Queueing it again would put both rows in the card refresher, editing one message
    from two states every run."""
    w = World()
    w.add_bill("3039", TITLE)
    w.gateway.submissions.append(submission(print_number="3039"))
    w.command(f"/analyze {RPW}")
    _commands_only(w)
    w.command(f"/unskip {RPW}")

    _commands_only(w)

    (_, _analysed), (_, outcome) = w.replier.replies
    assert outcome.status is OutcomeStatus.QUEUED
    assert outcome.note == "linked: its card belongs to 3039, ask for that one"
    assert w.bill(RPW).status is BillStatus.LINKED  # not queued for the model again


def test_find_names_the_bills_whose_title_carries_the_words() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.add_bill("4000", PLAIN)
    w.run()
    w.command("/find cudzoziemcach")

    _commands_only(w)

    (_, outcome), *_ = w.replier.replies
    assert outcome.status is OutcomeStatus.FOUND
    assert [b.number for b in outcome.found] == ["3039"]
    assert outcome.note == "1 match(es) for 'cudzoziemcach'"


def test_find_answers_a_miss_without_an_error() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.run()
    w.command("/find przewozy kolejowe")

    report = _commands_only(w)

    (_, outcome), *_ = w.replier.replies
    assert outcome.status is OutcomeStatus.FOUND and outcome.found == ()
    assert outcome.ok and report.commands_failed == 0


def test_status_counts_the_queues_the_posts_and_the_runs() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.run()  # analysed and posted
    w.add_bill("3100", TITLE)
    w.clock.advance(days=1)
    w.run(max_analyze=0)  # discovered, still waiting for the model
    w.command("/status")

    _commands_only(w)

    (_, outcome), *_ = w.replier.replies
    snapshot = outcome.snapshot
    assert snapshot is not None
    assert snapshot.bills[BillStatus.ANALYZED] == 1
    assert snapshot.publications["sent"] == 1
    assert snapshot.followed == 1
    assert [b.number for b in snapshot.waiting] == ["3100"]
    assert len(snapshot.runs) == 2 and snapshot.runs[0].published == 0


def test_a_bill_the_sejm_has_just_passed_still_gets_a_card() -> None:
    """`closureDate` is set at the third reading, with the Senate's 30 days still to come."""
    w = World()
    w.add_bill("2172", TITLE)
    w.touch("2172", dt.datetime(2026, 9, 4), closure_date=dt.date(2026, 9, 4), passed=True)
    w.command("/analyze 2172")

    _commands_only(w)

    (_, outcome), *_ = w.replier.replies
    assert outcome.status is OutcomeStatus.ANALYSED and outcome.message_id == 101
    assert [b.number for b, _ in w.publisher.new_bills] == ["2172"]
