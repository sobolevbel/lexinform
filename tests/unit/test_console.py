"""The dry-run publisher prints what Telegram would get."""

import io
from datetime import UTC, date, datetime

from lexinform.adapters.console import ConsolePublisher
from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import (
    ActInfo,
    AnalysisRecord,
    ApplicantType,
    Bill,
    BillStatus,
    BillSubmission,
    ProcessSummary,
)
from tests.fakes import make_analysis


def _bill() -> Bill:
    summary = ProcessSummary.from_submission(
        BillSubmission(
            term=10,
            number="RPW/1/2026",
            title="Projekt ustawy o cudzoziemcach",
            applicant=ApplicantType.GOVERNMENT,
            date_of_receipt=date(2026, 9, 1),
            public_consultation=True,
            consultation_end=date(2026, 9, 20),
        )
    )
    now = datetime(2026, 9, 7, tzinfo=UTC)
    return Bill(
        summary=summary,
        status=BillStatus.ANALYZED,
        analysis=AnalysisRecord(
            analysis=make_analysis(),
            model="m",
            prompt_version="v",
            input_chars=0,
            truncated=False,
            text_source="metadata_only",
            created_at=now,
        ),
        submission=summary.model_dump().get("submission")
        or BillSubmission(
            term=10,
            number="RPW/1/2026",
            title="Projekt ustawy o cudzoziemcach",
            date_of_receipt=date(2026, 9, 1),
            public_consultation=True,
            consultation_end=date(2026, 9, 20),
        ),
        act=ActInfo(
            eli="DU/2026/1",
            display_address="Dz.U. 2026 poz. 1",
            title="Ustawa",
            entry_into_force=date(2026, 9, 7),
            fetched_at=now,
        ),
        first_seen_at=now,
        last_checked_at=now,
    )


def test_console_publisher_emits_every_kind_with_increasing_ids() -> None:
    stream = io.StringIO()
    publisher = ConsolePublisher(MessageFormatter("ru"), stream=stream)
    bill = _bill()
    ids = [
        publisher.publish_new_bill(bill, None).message_id,
        publisher.publish_consultation_deadline(bill, 1, today=date(2026, 9, 17)).message_id,
        publisher.publish_act_published(bill, 1).message_id,
        publisher.publish_in_force(bill, 1).message_id,
    ]
    assert ids == [1, 2, 3, 4]
    out = stream.getvalue()
    assert "CONSULTATION DEADLINE RPW/1/2026 (reply to 1)" in out
    assert "осталось дней: 3" in out and "С сегодняшнего дня действует" in out
