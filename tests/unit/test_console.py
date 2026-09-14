"""The dry-run publisher prints what Telegram would get, one numbered message per post."""

import io
from datetime import UTC, date, datetime

from lexinform.adapters.console import ConsolePublisher
from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import (
    ActInfo,
    AgendaItem,
    AnalysisRecord,
    ApplicantType,
    Bill,
    BillStatus,
    BillSubmission,
    Phase,
    ProcessSummary,
    Stage,
    StatusChange,
)
from tests.fakes import make_analysis

NOW = datetime(2026, 9, 7, tzinfo=UTC)
SUBMISSION = BillSubmission(
    term=10,
    number="RPW/1/2026",
    title="Projekt ustawy o cudzoziemcach",
    applicant=ApplicantType.GOVERNMENT,
    date_of_receipt=date(2026, 9, 1),
    public_consultation=True,
    consultation_end=date(2026, 9, 20),
)
BILL = Bill(
    summary=ProcessSummary.from_submission(SUBMISSION),
    status=BillStatus.ANALYZED,
    analysis=AnalysisRecord(
        analysis=make_analysis(),
        model="m",
        prompt_version="v",
        input_chars=0,
        truncated=False,
        text_source="metadata_only",
        created_at=NOW,
    ),
    submission=SUBMISSION,
    act=ActInfo(
        eli="DU/2026/1",
        display_address="Dz.U. 2026 poz. 1",
        title="Ustawa",
        entry_into_force=date(2026, 9, 7),
        fetched_at=NOW,
    ),
    first_seen_at=NOW,
    last_checked_at=NOW,
)
SITTING = AgendaItem(
    kind="committee", ref="ASW/1/2026-09-17", date=date(2026, 9, 17), committee_code="ASW"
)
CHANGE = StatusChange(
    term=10,
    number="RPW/1/2026",
    old_fingerprint=None,
    new_fingerprint="b",
    new_stages=[],
    detected_at=NOW,
)


HEARING = Stage(
    stage_name="Wysłuchanie publiczne", stage_type="PublicHearing", date=date(2026, 9, 20)
)
SENATE_DECISION = Phase(key="senate", deadline=date(2026, 10, 4))


def test_every_kind_is_printed_with_an_increasing_message_id() -> None:
    """`run --dry-run` is the documented way to read what a run would post before it posts it, so
    a kind this publisher cannot print is a kind nobody can check."""
    stream = io.StringIO()
    publisher = ConsolePublisher(MessageFormatter("ru"), stream=stream)

    ids = [
        publisher.publish_new_bill(BILL, None).message_id,
        publisher.publish_status_update(BILL, CHANGE, 1).message_id,
        publisher.publish_consultation_deadline(BILL, 1, today=date(2026, 9, 17)).message_id,
        publisher.publish_consultation_results(BILL, 1).message_id,
        publisher.publish_agenda(BILL, SITTING, 1).message_id,
        publisher.publish_act_published(BILL, 1).message_id,
        publisher.publish_in_force(BILL, 1, today=date(2026, 9, 7)).message_id,
        publisher.publish_joint_bill(BILL, BILL, None, 1).message_id,
        publisher.publish_hearing_deadline(BILL, HEARING, 1, today=date(2026, 9, 7)).message_id,
        publisher.publish_decision_deadline(
            BILL, SENATE_DECISION, 1, today=date(2026, 9, 27)
        ).message_id,
    ]

    out = stream.getvalue()
    assert ids == list(range(1, 11))
    assert "NEW BILL druk RPW/1/2026" in out
    assert "JOINT BILL druk RPW/1/2026 under druk RPW/1/2026 (reply to 1)" in out
    assert "STATUS UPDATE druk RPW/1/2026 (reply to 1)" in out
    assert "CONSULTATION DEADLINE druk RPW/1/2026 (reply to 1)" in out
    assert "осталось дней: 3" in out
    assert "CONSULTATION RESULTS druk RPW/1/2026" in out
    assert "AGENDA druk RPW/1/2026 ASW/1/2026-09-17 (reply to 1)" in out
    assert "ACT PUBLISHED druk RPW/1/2026" in out
    assert "IN FORCE druk RPW/1/2026" in out and "С сегодняшнего дня действует" in out
    assert "HEARING DEADLINE druk RPW/1/2026 (reply to 1)" in out
    # The phase is on the line, because the Senate's 30 days and the President's 21 are two
    # different warnings and each bill gets one of each.
    assert "DECISION DEADLINE druk RPW/1/2026 senate (reply to 1)" in out


def test_a_card_edited_in_place_says_which_message_it_replaces() -> None:
    """The refresher edits cards that have drifted; on a dry run nothing is edited, so the line
    printed is the only way to see what the channel would have been given instead."""
    stream = io.StringIO()
    publisher = ConsolePublisher(MessageFormatter("ru"), stream=stream)

    publisher.edit_new_bill(BILL, None, message_id=101)

    out = stream.getvalue()
    assert "EDIT CARD druk RPW/1/2026 (message #101)" in out
    assert "PDF проекта (сайт Сейма)" in out  # the card itself, not only the line above it
