"""Following published bills: stage updates, re-analysis of new texts, closure, tracking scope."""

import datetime as dt

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import Attachment, Committee, PrintInfo, Stage, Vote, VotingSummary
from tests.fakes import FakeTextExtractor, make_analysis
from tests.harness import COMMITTEE_STAGES, ELI, REFERRED, START, World, act, print_url

# --------------------------------------------------------------------------- stage updates


def test_first_sight_of_the_stages_posts_nothing() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    w.run()

    assert w.publisher.updates == []


def test_new_stage_is_posted_exactly_once_as_a_reply_to_the_card() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.set_stages("3039", COMMITTEE_STAGES)
    w.clock.advance(days=1)

    report = w.run()
    w.clock.advance(days=1)
    again = w.run()

    assert report.updates == 1
    bill, change, reply_to = w.publisher.updates[0]
    assert [st.stage_type for st in change.new_stages] == ["ReadingReferral", "Referral"]
    assert reply_to == w.card_id("3039")
    assert again.updates == 0 and len(w.publisher.updates) == 1


def test_service_stages_are_held_and_told_with_the_next_substantive_one() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=REFERRED)
    w.run()
    reading = Stage(
        stage_name="I czytanie w komisjach", stage_type="Reading", date=dt.date(2026, 9, 6)
    )
    w.set_stages("3039", REFERRED + (reading,))
    w.clock.advance(days=1)
    held = w.run()
    w.set_stages("3039", REFERRED + (reading,) + WITH_REPORT[len(REFERRED) :])
    w.clock.advance(days=1)

    report = w.run()

    assert (held.updates, held.held) == (0, 1)
    assert len(w.publisher.updates) == 1 and report.updates == 1
    _, change, _ = w.publisher.updates[0]
    assert [st.stage_name for st in change.new_stages] == [
        "I czytanie w komisjach",
        "Praca w komisjach po I czytaniu",
        "Sprawozdanie komisji",
    ]
    assert w.repo.list_held_status_changes(10, "3039", "@test") == []  # released with the post


def test_closure_that_arrives_with_the_act_is_told_by_the_publication_notice_only() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.gateway.acts[ELI] = act()
    w.publish_act("3039")  # closure date, passed and the ELI address appear together
    w.clock.advance(days=1)

    report = w.run()

    assert (report.updates, report.held, report.acts_published) == (0, 1, 1)
    assert w.publisher.updates == [] and len(w.publisher.acts) == 1


def test_removed_stage_does_not_post_an_empty_update() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=REFERRED)
    w.run()
    w.set_stages("3039", START)  # the Sejm removed a stage
    w.clock.advance(days=1)

    report = w.run()

    assert report.updates == 0
    assert w.publisher.updates == []


def test_failed_update_is_retried_on_the_next_run() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.set_stages("3039", COMMITTEE_STAGES)
    w.publisher.fail_on = {"3039"}
    w.clock.advance(days=1)

    failed = w.run()
    posted_while_failing = list(w.publisher.updates)
    w.publisher.fail_on = set()
    w.clock.advance(days=1)
    retried = w.run()  # stages unchanged since, yet the lost update is posted now
    w.clock.advance(days=1)
    settled = w.run()

    assert failed.updates == 0 and failed.errors and posted_while_failing == []
    assert retried.updates == 1 and not retried.errors
    _, change, reply_to = w.publisher.updates[0]
    assert [st.stage_type for st in change.new_stages] == ["ReadingReferral", "Referral"]
    assert reply_to == w.card_id("3039")
    assert settled.updates == 0


def test_closure_is_announced_once_although_discovery_refreshes_the_summary() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.touch("3039", dt.datetime(2026, 9, 8, 10, 0), closure_date=dt.date(2026, 9, 8), passed=True)
    w.set_stages("3039", REFERRED)
    w.clock.advance(days=1)

    report = w.run()
    w.clock.advance(days=1)
    again = w.run()

    assert report.updates == 1
    _, change, _ = w.publisher.updates[0]
    assert change.closure_detected and change.passed
    assert again.updates == 0


# --------------------------------------------------------------------------- new texts

REPORT_URL = "https://api.test/sejm/term10/prints/2689/2689.pdf"
WITH_REPORT = REFERRED + (
    Stage(
        stage_name="Praca w komisjach po I czytaniu",
        stage_type="CommitteeWork",
        date=dt.date(2026, 9, 6),
        children=(
            Stage(
                stage_name="Sprawozdanie komisji",
                stage_type="CommitteeReport",
                date=dt.date(2026, 9, 6),
                print_number="2689",
                report_file=REPORT_URL,
            ),
        ),
    ),
)


def test_committee_report_with_a_new_text_triggers_a_re_analysis_with_context() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    updated = make_analysis(score=4)
    updated.changes_since_previous = ["Срок сокращён с 30 до 14 дней"]
    w.llm.script = {"3039": updated}
    w.set_stages("3039", WITH_REPORT)
    w.gateway.files[REPORT_URL] = b"%PDF-report"
    w.clock.advance(days=1)

    report = w.run()

    assert (report.reanalyzed, report.updates) == (1, 1)
    ctx = w.llm.contexts[-1]
    assert ctx.source_kind == "committee_report" and ctx.previous_summary
    stored = w.bill("3039").analysis
    assert stored is not None and (stored.revision, stored.source_url) == (2, REPORT_URL)
    _, change, reply_to = w.publisher.updates[-1]
    assert change.content_changed and reply_to == w.card_id("3039")
    assert [s.stage_type for s in change.new_stages] == [
        "ReadingReferral",
        "CommitteeWork",
        "CommitteeReport",
    ]


def test_same_document_is_not_analysed_twice() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.set_stages("3039", WITH_REPORT)
    w.gateway.files[REPORT_URL] = b"%PDF-report"
    w.clock.advance(days=1)
    w.run()
    w.clock.advance(days=1)

    report = w.run()

    assert (report.reanalyzed, report.updates) == (0, 0)


def test_updated_print_triggers_a_re_analysis_without_a_stage_change() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.clock.advance(days=2)
    w.gateway.prints["3039"] = w.gateway.prints["3039"].model_copy(
        update={"change_date": w.clock.now().replace(tzinfo=None)}
    )

    report = w.run()

    assert (report.reanalyzed, report.updates) == (1, 1)
    _, change, _ = w.publisher.updates[-1]
    assert change.content_changed and change.new_stages == []
    assert w.gateway.files[print_url("3039")]  # the original print was re-read


# --------------------------------------------------------------------------- amendments

SENATE_PRINT_URL = "https://api.test/sejm/term10/prints/2994/2994.pdf"
SENATE_AMENDED = REFERRED + (
    Stage(
        stage_name="III czytanie na posiedzeniu Sejmu",
        stage_type="SejmReading",
        date=dt.date(2026, 9, 10),
        decision="uchwalono",
    ),
    Stage(
        stage_name="Stanowisko Senatu",
        stage_type="SenatePosition",
        date=dt.date(2026, 9, 20),
        position="wniósł poprawki",
        print_number="2994",
    ),
)
A_REPORT_URL = "https://api.test/sejm/term10/prints/2689-A/2689-A.pdf"
WITH_A_REPORT = WITH_REPORT + (
    Stage(
        stage_name="Praca w komisjach po II czytaniu",
        stage_type="CommitteeWork",
        date=dt.date(2026, 9, 8),
        children=(
            Stage(
                stage_name="Sprawozdanie komisji",
                stage_type="CommitteeReport",
                date=dt.date(2026, 9, 8),
                print_number="2689-A",
                proposal="przyjąć poprawki",
                report_file=A_REPORT_URL,
            ),
        ),
    ),
)


def test_senate_amendments_are_summarised_from_the_senate_print() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=REFERRED)
    w.run()
    w.gateway.prints["2994"] = PrintInfo(
        term=10,
        number="2994",
        title="Uchwała Senatu",
        attachments=(Attachment(print_number="2994", name="2994.pdf", url=SENATE_PRINT_URL),),
    )
    w.gateway.files[SENATE_PRINT_URL] = b"%PDF-senat"
    w.set_stages("3039", SENATE_AMENDED)
    w.clock.advance(days=1)

    report = w.run()
    w.clock.advance(days=1)
    again = w.run()

    assert report.updates == 1 and again.updates == 0
    _, change, _ = w.publisher.updates[0]
    assert change.amendments is not None
    assert change.amendments.source_kind == "senate_amendments"
    assert change.amendments.source_url == SENATE_PRINT_URL
    assert change.amendments.amendments.changes[0].startswith("Срок подачи заявления продлён")
    ctx = w.llm.amendment_contexts[-1]
    assert ctx.previous_summary and ctx.source_kind == "senate_amendments"
    assert len(w.llm.amendment_contexts) == 1  # not summarised again
    text = MessageFormatter("ru").status_update(*w.publisher.updates[0][:2]).text
    assert text.startswith("📋 <b>Сенат внёс поправки — druk nr 3039</b>")
    assert "🆕 <b>Что меняют поправки Сената</b>\nСенат смягчил проект" in text
    assert "• Убран сбор за дубликат" in text and 'href="' + SENATE_PRINT_URL in text
    assert "#сенат #поправки #kadencja10druk3039" in text


def test_additional_committee_report_is_summarised_with_its_proposal() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=WITH_REPORT)
    w.gateway.files[REPORT_URL] = b"%PDF-report"
    w.run()
    w.gateway.files[A_REPORT_URL] = b"%PDF-A"
    w.set_stages("3039", WITH_A_REPORT)
    w.clock.advance(days=1)

    report = w.run()

    assert (report.updates, report.reanalyzed) == (1, 0)  # a table of amendments, not a text
    _, change, _ = w.publisher.updates[-1]
    assert change.amendments is not None and change.amendments.source_url == A_REPORT_URL
    assert w.llm.amendment_contexts[-1].proposal == "przyjąć poprawki"
    text = MessageFormatter("ru").status_update(*w.publisher.updates[-1][:2]).text
    assert "отчёт комиссии (sprawozdanie) (druk 2689-A): предлагает принять поправки" in text
    assert "Что меняют поправки (по отчёту комиссии)" in text


def test_unreadable_amendments_document_leaves_the_bare_event() -> None:
    w = World(extractor=FakeTextExtractor(error=RuntimeError("no text layer")))
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=WITH_REPORT, with_pdf=False)
    w.run()
    w.gateway.files[A_REPORT_URL] = b"%PDF-A"
    w.set_stages("3039", WITH_A_REPORT)
    w.clock.advance(days=1)

    report = w.run()

    assert report.updates == 1 and not report.errors
    _, change, _ = w.publisher.updates[-1]
    assert change.amendments is None and w.llm.amendment_contexts == []


# --------------------------------------------------------------------------- enrichment

VOTED = REFERRED + (
    Stage(
        stage_name="III czytanie na posiedzeniu Sejmu",
        stage_type="SejmReading",
        date=dt.date(2026, 9, 10),
        decision="uchwalono",
        sitting_num=62,
        children=(
            Stage(
                stage_name="Głosowanie",
                stage_type="Voting",
                date=dt.date(2026, 9, 10),
                voting=VotingSummary(yes=261, no=0, abstain=182, sitting=62, voting_number=16),
            ),
        ),
    ),
)
REFERRAL = Stage(stage_name="Skierowanie", stage_type="Referral", committee_code="ASW")
VOTED_WITH_REFERRAL = (
    VOTED[0],
    VOTED[1].model_copy(update={"children": (REFERRAL,)}),
    VOTED[2],
)


def test_votes_get_the_club_breakdown_and_referrals_the_committee_name() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.gateway.votings[(62, 16)] = (
        Vote(mp=1, club="KO", vote="YES"),
        Vote(mp=2, club="PiS", vote="ABSTAIN"),
    )
    w.gateway.committees["ASW"] = Committee(term=10, code="ASW", name="Komisja ASW")
    w.set_stages("3039", VOTED_WITH_REFERRAL)
    w.clock.advance(days=1)

    report = w.run()

    assert report.updates == 1
    _, change, _ = w.publisher.updates[0]
    voting = next(s for s in change.new_stages if s.stage_type == "Voting")
    assert voting.voting is not None
    assert [(c.club, c.yes, c.abstain) for c in voting.voting.clubs] == [
        ("KO", 1, 0),
        ("PiS", 0, 1),
    ]
    referral = next(s for s in change.new_stages if s.stage_type == "Referral")
    assert referral.committee_name == "Komisja ASW"


def test_vote_detail_failure_degrades_to_totals_only() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.set_stages("3039", VOTED)  # no per-MP votes in the fake: a KeyError inside enrichment
    w.clock.advance(days=1)

    report = w.run()

    assert report.updates == 1 and not report.errors
    _, change, _ = w.publisher.updates[0]
    voting = next(s for s in change.new_stages if s.stage_type == "Voting")
    assert voting.voting is not None
    assert (voting.voting.clubs, voting.voting.yes) == ((), 261)


# --------------------------------------------------------------------------- scope


def test_daily_tracking_checks_only_bills_the_api_listed_as_changed() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.add_bill("3040", "Projekt ustawy o obywatelstwie polskim")
    w.run()  # Monday: a full check, both published
    w.clock.advance(days=1)  # Tuesday
    w.touch("3040", dt.datetime(2026, 9, 8, 9, 0))
    w.set_stages("3040", COMMITTEE_STAGES)
    w.gateway.calls.clear()

    report = w.run(since=dt.datetime(2026, 9, 7, tzinfo=dt.UTC))

    assert (report.tracked, report.updates) == (1, 1)
    assert "get_process:3040" in w.gateway.calls
    assert "get_process:3039" not in w.gateway.calls


def test_full_track_flag_and_the_weekly_check_fetch_every_followed_bill() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.add_bill("3040", "Projekt ustawy o obywatelstwie polskim")
    w.run()
    w.clock.advance(days=1)  # Tuesday: nothing listed as changed

    w.gateway.calls.clear()
    flagged = w.run(since=dt.datetime(2026, 9, 8, tzinfo=dt.UTC), full_track=True)
    w.clock.advance(days=6)  # next Monday
    w.gateway.calls.clear()
    weekly = w.run(since=dt.datetime(2026, 9, 14, tzinfo=dt.UTC))

    assert flagged.tracked == 2 and "get_process:3039" in w.gateway.calls
    assert weekly.tracked == 2


def test_passed_bills_waiting_for_their_act_are_always_checked() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.set_stages("3039", REFERRED)
    w.touch("3039", dt.datetime(2026, 9, 7, 9, 0), passed=True, closure_date=dt.date(2026, 9, 7))
    w.run()  # Monday: the full check stores passed=True
    w.clock.advance(days=1)
    w.gateway.calls.clear()

    report = w.run(since=dt.datetime(2026, 9, 8, tzinfo=dt.UTC))  # nothing listed as changed

    assert report.tracked == 1
    assert "get_process:3039" in w.gateway.calls
