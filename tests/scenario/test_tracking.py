"""Following published bills: stage updates, re-analysis of new texts, closure, tracking scope."""

import datetime as dt

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.errors import LlmUnavailableError
from lexinform.models import (
    Attachment,
    BillStatus,
    Committee,
    PrintInfo,
    Publication,
    PublicationKind,
    PublicationStatus,
    Stage,
    Vote,
    VotingSummary,
    flatten_stages,
)
from tests.fakes import FakeTextExtractor, make_analysis
from tests.harness import COMMITTEE_STAGES, ELI, REFERRED, START, TERM, World, act, print_url


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


REPORT_TEXT = "Art. 1. Tekst po poprawkach komisji. " * 50


def test_committee_report_with_a_new_text_triggers_a_re_analysis_with_context() -> None:
    w = World(extractor=FakeTextExtractor(by_content={b"%PDF-report": REPORT_TEXT}))
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


def test_a_batched_re_analysis_posts_nothing_until_collected() -> None:
    """The new text is detected and filed to the batch on the run that finds it, exactly as a
    first analysis is; nothing is posted until it is collected, and re-detecting it on a run in
    between must not file it a second time."""
    w = World(batch=True, extractor=FakeTextExtractor(by_content={b"%PDF-report": REPORT_TEXT}))
    batch = w.batch
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    batch.resolve()
    w.run()  # collects the first analysis and publishes the card
    batch.submitted.clear()
    w.set_stages("3039", WITH_REPORT)
    w.gateway.files[REPORT_URL] = b"%PDF-report"
    w.clock.advance(days=1)

    submitted = w.run()

    # The new committee-report stage is still posted this run (it needs no LLM answer); the text
    # itself is filed to the batch instead, and this update says nothing about its content yet.
    assert submitted.reanalyzed == 0
    _, first_change, _ = w.publisher.updates[-1]
    assert not first_change.content_changed
    assert w.bill("3039").status is BillStatus.BATCH_PENDING
    assert len(batch.submitted) == 1

    w.clock.advance(days=1)
    resubmitted = w.run()  # a re-visit while still pending must not queue it again

    assert resubmitted.reanalyzed == 0 and len(batch.submitted) == 1

    batch.resolve()
    w.clock.advance(days=1)
    collected = w.run()

    assert collected.reanalyzed == 1
    assert w.bill("3039").status is BillStatus.ANALYZED
    _, change, reply_to = w.publisher.updates[-1]
    assert change.content_changed and reply_to == w.card_id("3039")


def test_a_stranded_re_analysis_reset_to_analyzed_is_filed_again_and_applied() -> None:
    """The `/reset … to=analyzed` that `/status` offers must get the new text filed again."""
    w = World(batch=True, extractor=FakeTextExtractor(by_content={b"%PDF-report": REPORT_TEXT}))
    batch = w.batch
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    batch.resolve()
    w.run()  # the card
    batch.submitted.clear()
    w.repo.set_status(TERM, "3039", BillStatus.BATCH_PENDING)  # held by no batch or intent
    w.set_stages("3039", WITH_REPORT)
    w.gateway.files[REPORT_URL] = b"%PDF-report"
    w.clock.advance(days=1)
    w.run()
    assert batch.submitted == [] and w.bill("3039").status is BillStatus.BATCH_PENDING
    w.command("/status")
    w.run(discover=False, track=False, max_analyze=0, max_publish=0)
    (_, status), *_ = w.replier.replies
    assert status.snapshot is not None
    assert [b.number for b in status.snapshot.orphaned] == ["3039"]
    w.command("/reset 3039 to=analyzed")
    w.clock.advance(days=1)

    refiled = w.run()

    assert refiled.reanalyzed == 0 and [r.number for r in batch.submitted] == ["3039"]
    assert w.bill("3039").status is BillStatus.BATCH_PENDING
    batch.resolve()
    w.clock.advance(days=1)
    collected = w.run()
    assert collected.reanalyzed == 1 and w.bill("3039").status is BillStatus.ANALYZED
    stored = w.bill("3039").analysis
    assert stored is not None and stored.source_url == REPORT_URL
    assert any(change.content_changed for _, change, _ in w.publisher.updates)


def test_a_pilny_bill_is_re_analysed_at_once_even_with_batching_on() -> None:
    w = World(batch=True, extractor=FakeTextExtractor(by_content={b"%PDF-report": REPORT_TEXT}))
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.touch("3039", dt.datetime(2026, 9, 7, 5, tzinfo=dt.UTC), urgency_status="URGENT")
    w.run()  # the first analysis is not batched either
    w.set_stages("3039", WITH_REPORT)
    w.gateway.files[REPORT_URL] = b"%PDF-report"
    w.clock.advance(days=1)

    report = w.run()

    assert report.reanalyzed == 1 and w.batch.submitted == []
    assert w.bill("3039").status is BillStatus.ANALYZED
    _, change, reply_to = w.publisher.updates[-1]
    assert change.content_changed and reply_to == w.card_id("3039")


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


def test_llm_outage_during_the_re_analysis_does_not_lose_the_new_stages() -> None:
    w = World(extractor=FakeTextExtractor(by_content={b"%PDF-report": REPORT_TEXT}))
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.set_stages("3039", WITH_REPORT)
    w.gateway.files[REPORT_URL] = b"%PDF-report"
    w.clock.advance(days=1)
    w.llm.script = {"3039": LlmUnavailableError("RateLimitError: 429")}
    outage = w.run()
    w.llm.script = {"3039": make_analysis(score=4)}
    w.clock.advance(days=1)

    report = w.run()

    assert any("LLM API unavailable" in e for e in outage.errors) and outage.updates == 0
    assert (report.reanalyzed, report.updates) == (1, 1)
    _, change, _ = w.publisher.updates[-1]
    assert change.content_changed
    assert [s.stage_type for s in change.new_stages] == [
        "ReadingReferral",
        "CommitteeWork",
        "CommitteeReport",
    ]


def test_unreadable_new_text_keeps_the_previous_analysis() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.set_stages("3039", WITH_REPORT)  # the report's file is announced but not downloadable
    w.clock.advance(days=1)

    report = w.run()

    assert (report.reanalyzed, report.updates) == (0, 1)
    assert len(w.llm.contexts) == 1  # the model was not asked about an empty text
    stored = w.bill("3039").analysis
    assert stored is not None and (stored.revision, stored.source_url) == (1, print_url("3039"))
    _, change, _ = w.publisher.updates[-1]
    assert not change.content_changed and len(change.new_stages) == 3


def test_updated_print_triggers_a_re_analysis_without_a_stage_change() -> None:
    autopoprawka = "Art. 1. Tekst po autopoprawce. " * 50
    w = World(extractor=FakeTextExtractor(by_content={b"%PDF-v2": autopoprawka}))
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.clock.advance(days=2)
    # The autopoprawka is published as a new file of the same print.
    revised = print_url("3039").replace("3039.pdf", "3039_autopoprawka.pdf")
    w.gateway.prints["3039"] = w.gateway.prints["3039"].model_copy(
        update={
            "change_date": w.clock.now().replace(tzinfo=None),
            "attachments": (
                Attachment(print_number="3039", name="3039_autopoprawka.pdf", url=revised),
            ),
        }
    )
    w.gateway.files[revised] = b"%PDF-v2"

    report = w.run()

    assert (report.reanalyzed, report.updates) == (1, 1)
    _, change, _ = w.publisher.updates[-1]
    assert change.content_changed and change.new_stages == []
    assert w.llm.contexts[-1].text.startswith("Art. 1. Tekst po autopoprawce.")


def test_print_re_dated_with_the_same_text_is_not_analysed_again() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.clock.advance(days=2)
    w.gateway.prints["3039"] = w.gateway.prints["3039"].model_copy(
        update={"change_date": w.clock.now().replace(tzinfo=None)}
    )  # an attachment (stanowisko rządu, opinia) re-dated the print; the PDF is the same

    report = w.run()
    w.clock.advance(days=1)
    again = w.run()

    assert (report.reanalyzed, report.updates, again.reanalyzed) == (0, 0, 0)
    assert len(w.llm.contexts) == 1  # the first analysis only
    analysis = w.bill("3039").analysis
    assert analysis is not None and analysis.revision == 1
    assert analysis.source_checked_at is not None and analysis.text_sha256


AFTER3_URL = "https://api.test/sejm/term10/processes/3039/attachment/3039_u3.pdf"
FULL_REPORT = WITH_REPORT[:-1] + (
    WITH_REPORT[-1].model_copy(
        update={
            "children": (
                WITH_REPORT[-1]
                .children[0]
                .model_copy(update={"proposal": "załączony projekt ustawy", "minority_motions": 0}),
            )
        }
    ),
)
ADOPTED_AS_REPORTED = FULL_REPORT + (
    Stage(
        stage_name="II czytanie na posiedzeniu Sejmu",
        stage_type="SejmReading",
        date=dt.date(2026, 9, 9),
        decision="niezwłocznie przystąpiono do III czytania",
    ),
    Stage(
        stage_name="III czytanie na posiedzeniu Sejmu",
        stage_type="SejmReading",
        date=dt.date(2026, 9, 10),
        decision="uchwalono",
        text_after3=AFTER3_URL,
    ),
)


def test_text_after_third_reading_is_not_read_when_the_sejm_adopted_the_report_as_is() -> None:
    w = World(extractor=FakeTextExtractor(by_content={b"%PDF-report": REPORT_TEXT}))
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=FULL_REPORT)
    w.gateway.files[REPORT_URL] = b"%PDF-report"
    w.run()
    w.set_stages("3039", ADOPTED_AS_REPORTED)
    w.gateway.files[AFTER3_URL] = b"%PDF-after3"
    w.clock.advance(days=1)

    report = w.run()

    assert (report.reanalyzed, report.updates) == (0, 1)
    _, change, _ = w.publisher.updates[-1]
    assert not change.content_changed
    assert f"download:{AFTER3_URL}" not in w.gateway.calls  # not even downloaded
    analysis = w.bill("3039").analysis
    assert analysis is not None and analysis.source_url == REPORT_URL


def test_text_after_third_reading_is_read_when_minority_motions_were_voted() -> None:
    after3 = "Art. 1. Tekst po III czytaniu z wnioskiem mniejszości. " * 50
    w = World(
        extractor=FakeTextExtractor(
            by_content={b"%PDF-report": REPORT_TEXT, b"%PDF-after3": after3}
        )
    )
    contested = FULL_REPORT[:-1] + (
        FULL_REPORT[-1].model_copy(
            update={
                "children": (
                    FULL_REPORT[-1].children[0].model_copy(update={"minority_motions": 2}),
                )
            }
        ),
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=contested)
    w.gateway.files[REPORT_URL] = b"%PDF-report"
    w.run()
    w.set_stages("3039", contested + ADOPTED_AS_REPORTED[len(FULL_REPORT) :])
    w.gateway.files[AFTER3_URL] = b"%PDF-after3"
    w.clock.advance(days=1)

    report = w.run()

    assert (report.reanalyzed, report.updates) == (1, 1)
    analysis = w.bill("3039").analysis
    assert analysis is not None and analysis.source_url == AFTER3_URL


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
    assert change.amendments is not None, "the Senate's resolution print is summarised"
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
    assert "#сенат #поправки #важность5 #легализация #kadencja10druk3039" in text


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
    assert "отчёт комиссии (sprawozdanie) (druk nr 2689-A): предлагает принять поправки" in text
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
    assert voting.voting is not None, "the Voting stage carries its totals"
    assert [(c.club, c.yes, c.abstain) for c in voting.voting.clubs] == [
        ("KO", 1, 0),
        ("PiS", 0, 1),
    ]
    referral = next(s for s in change.new_stages if s.stage_type == "Referral")
    assert referral.committee_name == "Komisja ASW"
    # And the name is kept on the bill, not only in the post: the card's «направить мнение в
    # комиссию» is what a reader acts on, and it used to name the bare code.
    stored = next(s for s in flatten_stages(w.bill("3039").stages) if s.stage_type == "Referral")
    assert stored.committee_name == "Komisja ASW"


def test_a_committee_is_named_on_the_card_even_when_nothing_moved() -> None:
    """The tree is stored as the API gives it, and the API never names a committee, so the name
    has to be written whether or not the fingerprint moved — none of the 86 referrals in the
    state dump of 2026-09-13 had one, and every card addressed a three-letter code."""
    w = World()
    w.gateway.committees["ASW"] = Committee(term=10, code="ASW", name="Komisja ASW")
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=COMMITTEE_STAGES)
    w.run()
    stored = w.bill("3039")
    assert stored.stages_fingerprint is not None, "the first analysis seeds the stage fingerprint"
    w.repo.save_stages(10, "3039", _unnamed(stored.stages), stored.stages_fingerprint)
    w.clock.advance(days=1)

    report = w.run()

    assert report.updates == 0  # the name is not part of the fingerprint: nothing to announce
    referral = next(s for s in flatten_stages(w.bill("3039").stages) if s.stage_type == "Referral")
    assert referral.committee_name == "Komisja ASW"


def _unnamed(stages: tuple[Stage, ...]) -> tuple[Stage, ...]:
    return tuple(
        st.model_copy(update={"committee_name": None, "children": _unnamed(st.children)})
        for st in stages
    )


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
    assert voting.voting is not None, "the Voting stage carries its totals"
    assert (voting.voting.clubs, voting.voting.yes) == ((), 261)


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


def test_a_no_publish_run_does_not_swallow_the_stages_it_did_not_post() -> None:
    """The change row is written and unique, so without a `skipped` row nothing would ever
    detect these stages again."""
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=START)
    w.run()
    w.clock.advance(days=1)
    w.set_stages("3039", COMMITTEE_STAGES)
    w.touch("3039", dt.datetime(2026, 9, 8, 9, 0))

    silent = w.run(publish=False)
    posted_silently = list(w.publisher.updates)
    w.clock.advance(days=1)
    w.set_stages("3039", (*COMMITTEE_STAGES, Stage(stage_type="Voting", stage_name="Głosowanie")))
    w.touch("3039", dt.datetime(2026, 9, 9, 9, 0))
    told = w.run()

    assert (silent.updates, posted_silently) == (1, [])
    assert told.updates == 2
    _, change, _ = w.publisher.updates[0]
    kinds = {st.stage_type for st in change.new_stages}
    assert "Referral" in kinds and "Voting" not in kinds
    assert [st.stage_type for st in w.publisher.updates[1][1].new_stages] == ["Voting"]


def test_an_act_notice_a_crash_lost_does_not_also_swallow_the_closure() -> None:
    """A row a crashed run left behind is never sent again — that is deliberate. But it used to
    count as "the publication notice told it", so the closure update was held as well and the
    reader heard nothing at all about a bill that had reached Dziennik Ustaw."""
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.repo.create_publication(
        Publication(
            term=10,
            number="3039",
            kind=PublicationKind.ACT_PUBLISHED,
            status=PublicationStatus.PENDING,
            channel_id="@test",
            created_at=w.clock.now(),
        )
    )
    w.gateway.acts[ELI] = act()
    w.publish_act("3039")
    w.clock.advance(days=1)

    report = w.run()

    assert report.acts_published == 0  # the lost notice is not re-sent
    assert report.updates == 1
    _, change, _ = w.publisher.updates[0]
    assert change.closure_detected and change.passed


SECOND_READING = Stage(
    stage_name="II czytanie na posiedzeniu Sejmu",
    stage_type="SejmReading",
    decision="skierowano ponownie do komisji w celu przedstawienia sprawozdania",
    date=dt.date(2026, 9, 8),
)
WORK_AFTER_SECOND_READING = Stage(
    stage_name="Praca w komisjach po II czytaniu",
    stage_type="CommitteeWork",
    date=dt.date(2026, 9, 8),
    children=(
        Stage(
            stage_name="Sprawozdanie komisji",
            stage_type="CommitteeReport",
            proposal="przyjąć część poprawek",
            date=dt.date(2026, 9, 8),
            print_number="2689-A",
        ),
    ),
)


def test_a_stage_the_sejm_publishes_late_is_held_and_told_with_the_next_post() -> None:
    """Druk 1929, 15 Sept 2026: the committee's work after the second reading was published to
    the tree an hour before the reading that sent the bill there, so the second post announced
    the cause of the first and repeated seven of its eleven lines."""
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=WITH_REPORT)
    w.run()
    w.set_stages("3039", WITH_REPORT + (WORK_AFTER_SECOND_READING,))
    w.clock.advance(days=1)
    told = w.run()
    w.set_stages("3039", WITH_REPORT + (SECOND_READING, WORK_AFTER_SECOND_READING))
    w.clock.advance(days=1)

    backfilled = w.run()

    assert (told.updates, told.held) == (1, 0)
    assert (backfilled.updates, backfilled.held) == (0, 1)
    assert len(w.publisher.updates) == 1

    w.set_stages(
        "3039",
        WITH_REPORT
        + (
            SECOND_READING,
            WORK_AFTER_SECOND_READING,
            Stage(
                stage_name="III czytanie na posiedzeniu Sejmu",
                stage_type="SejmReading",
                decision="uchwalono",
                date=dt.date(2026, 9, 10),
            ),
        ),
    )
    w.clock.advance(days=1)
    w.run()

    _, change, _ = w.publisher.updates[1]
    assert "II czytanie na posiedzeniu Sejmu" in [st.stage_name for st in change.new_stages]
    assert w.repo.list_held_status_changes(10, "3039", "@test") == []


def test_a_law_the_senate_held_past_the_window_is_still_followed() -> None:
    """Druk 210 of term 9: the Sejm passed it on 2020-02-14 and answered the Senate on
    2020-08-14, 182 days later — two days after the window measured from `closureDate`, which
    the Sejm sets at the third reading. The bill left `list_tracked` while its road ran, so the
    override, the hand-over, the signature and the act were all lost, and the card kept saying
    the Senate had rejected a law that was in force.
    """
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=SENATE_AMENDED)
    w.run()
    w.touch("3039", w.clock.now(), closure_date=dt.date(2026, 9, 10), passed=True)
    w.run()
    told = len(w.publisher.updates)

    w.clock.advance(days=190)
    w.set_stages(
        "3039",
        SENATE_AMENDED
        + (
            Stage(
                stage_name="Rozpatrywanie na forum Sejmu stanowiska Senatu",
                stage_type="SenatePositionConsideration",
                date=dt.date(2027, 3, 16),
                decision="odrzucono uchwałę Senatu",
            ),
            Stage(
                stage_name="Ustawę przekazano Prezydentowi do podpisu",
                stage_type="ToPresident",
                date=dt.date(2027, 3, 16),
            ),
        ),
    )
    w.touch("3039", w.clock.now(), closure_date=dt.date(2026, 9, 10), passed=True)

    report = w.run()

    assert report.updates == 1
    _, change, _ = w.publisher.updates[told]
    assert [st.stage_type for st in change.new_stages] == [
        "SenatePositionConsideration",
        "ToPresident",
    ]
