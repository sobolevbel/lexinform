"""Operator commands from the technical channel, end to end on the World harness: a command
waits in the inbox, the run executes it, answers under it and takes it out."""

import datetime as dt
from typing import Any

from lexinform.errors import LlmUnavailableError
from lexinform.models import BillStatus, OutcomeStatus, PublicationKind, RunMode, RunReport
from lexinform.services.commands import FORCE_HINT
from tests.fakes import FakeLlm, FakeTextExtractor, make_analysis
from tests.harness import RCL, RCL_ID, World, rcl_project

TITLE = "Poselski projekt ustawy o zmianie ustawy o cudzoziemcach"
PLAIN = "Rządowy projekt ustawy o podatku VAT"  # says nothing about foreigners
TEXT_WITH_HITS = "Art. 1. Cudzoziemiec składa wniosek o zezwolenie na pobyt czasowy. " * 20


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


def test_a_bill_the_sejm_has_finished_with_gets_a_verdict_but_no_card() -> None:
    w = World()
    w.add_bill("2172", TITLE)
    w.touch("2172", dt.datetime(2026, 1, 23), closure_date=dt.date(2026, 1, 23), passed=True)
    w.command("/analyze 2172")

    _commands_only(w)

    (_, outcome), *_ = w.replier.replies
    assert outcome.status is OutcomeStatus.ANALYSED and outcome.message_id is None
    assert outcome.note == "the process ended on 2026-01-23 (passed): not posted"
    assert w.publisher.new_bills == []
