import datetime as dt

import pytest

from lexinform.attention import assess_attention
from lexinform.models import (
    BillSubmission,
    DeliveryPlan,
    Editorial,
    Mnemonic,
    Publication,
    PublicationKind,
    PublicationStatus,
    Stage,
)
from tests.harness import CHANNEL, COMMITTEE_STAGES, TERM, World


@pytest.mark.parametrize("workers", [1, 4])
def test_policy_groups_routine_events_but_releases_urgent_routine_immediately(workers: int) -> None:
    w = World(workers=workers, reader_attention="on")
    for number in ("3039", "3040", "3041"):
        w.add_bill(number, "Projekt ustawy o cudzoziemcach")
    w.run()
    w.repo.save_submission(
        TERM,
        "3041",
        BillSubmission(
            term=TERM,
            number="RPW/1/2026",
            title="Projekt ustawy о cudzoziemcach",
            date_of_receipt=w.clock.now().date(),
            public_consultation=True,
            consultation_end=w.clock.now().date() + dt.timedelta(days=2),
            survey_url="https://example.test/opinion",
        ),
    )
    for number in ("3039", "3040", "3041"):
        w.set_stages(number, COMMITTEE_STAGES)

    report = w.run()

    assert (report.updates, report.update_events) == (2, 3)
    urgent = w.publication("3041", PublicationKind.STATUS_UPDATE)
    assert urgent is not None and urgent.delivery is not None and urgent.attention is not None
    assert urgent.attention.importance == "routine" and urgent.attention.urgency == "urgent"
    assert not urgent.delivery.update_group
    assert "Нужно действовать" not in urgent.delivery.rendered_text
    deadline = w.formatter.fmt_date(w.clock.now().date() + dt.timedelta(days=2))
    assert deadline in urgent.delivery.rendered_text
    assert urgent.status is PublicationStatus.SENT
    assert report.attention["urgent"] == 1
    assert report.attention["events"] == 3 and report.attention["messages"] == 2


def test_started_delivery_retries_exact_html_even_after_policy_and_name_change() -> None:
    w = World(reader_attention="on")
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.publisher.fail_on.add("3039")
    w.set_stages("3039", COMMITTEE_STAGES)
    w.run()
    pub = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert pub is not None and pub.delivery is not None
    frozen = pub.delivery.rendered_text
    assert frozen and pub.status is PublicationStatus.FAILED
    mnemonic = Mnemonic(
        name="Новое редакторское имя", language="ru", basis="Правка", selected_at=w.clock.now()
    )
    w.repo.save_editorial(TERM, "3039", Editorial(mnemonic=mnemonic))
    w.repo.restore(w.repo.dump())
    w.publisher.fail_on.clear()
    w.clock.advance(days=2)
    w.container.settings.reader_attention = "off"

    w.run()

    pub = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert pub is not None and pub.status is PublicationStatus.SENT
    assert w.publisher.texts(PublicationKind.STATUS_UPDATE)[-1] == frozen


def test_attention_survives_sent_payload_retention() -> None:
    w = World(reader_attention="on")
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.set_stages("3039", COMMITTEE_STAGES)
    w.run()
    pub = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert pub is not None and pub.attention is not None
    expected = pub.attention

    w.repo.prune_history(before=w.clock.now() + dt.timedelta(days=1), memo_before=w.clock.now())
    w.repo.restore(w.repo.dump())

    pub = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert pub is not None and pub.delivery is None and pub.attention == expected


def test_unattempted_expired_hearing_reminder_is_retired_without_invitation() -> None:
    w = World(reader_attention="on")
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    hearing = Stage(
        stage_type="PublicHearing",
        stage_name="Wysłuchanie",
        date=w.clock.now().date() + dt.timedelta(days=11),
    )
    bill = w.bill("3039")
    identity = w.repo.create_publication(
        Publication(
            term=TERM,
            number=bill.number,
            kind=PublicationKind.HEARING_DEADLINE,
            status=PublicationStatus.QUEUED,
            channel_id=CHANNEL,
            ref=hearing.date.isoformat() if hearing.date else "",
            created_at=w.clock.now(),
            delivery=DeliveryPlan(
                bill_json=bill.model_dump_json(),
                hearing_json=hearing.model_dump_json(),
                policy_version="attention-v1",
                today=w.clock.now().date(),
                reply_to=w.card_id(bill.number),
                attention=assess_attention(
                    bill, PublicationKind.HEARING_DEADLINE, now=w.clock.now(), hearing=hearing
                ),
            ),
        )
    )
    w.clock.advance(days=2)

    report = w.run()

    reminder = w.repo.publication_by_id(identity)
    assert reminder is not None and reminder.status is PublicationStatus.SKIPPED
    assert reminder.delivery is not None and reminder.delivery.attention_history
    assert not w.publisher.texts(PublicationKind.HEARING_DEADLINE)
    assert report.attention["expired"] == 1 and report.ok


@pytest.mark.parametrize("workers", [1, 4])
def test_roundup_limit_splits_without_losing_individual_events(workers: int) -> None:
    w = World(reader_attention="on", workers=workers)
    numbers = [str(number) for number in range(3030, 3036)]
    for number in numbers:
        w.add_bill(number, "Projekt ustawy o cudzoziemcach")
    w.run()
    for number in numbers:
        w.set_stages(number, COMMITTEE_STAGES)

    report = w.run()

    assert (report.updates, report.update_events) == (2, 6)
    assert report.attention["grouped"] == 5
    for number in numbers:
        pub = w.publication(number, PublicationKind.STATUS_UPDATE)
        assert pub is not None and pub.status is PublicationStatus.SENT
