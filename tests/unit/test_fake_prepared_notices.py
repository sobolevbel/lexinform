from datetime import date, datetime
from typing import Literal

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.errors import TelegramUnavailableError
from lexinform.models import (
    Bill,
    DeliveryPlan,
    Phase,
    ProcessDetail,
    Publication,
    PublicationKind,
    PublicationStatus,
    Stage,
)
from lexinform.ports import PublishResult
from tests.fakes import FakePublisher
from tests.harness import act, submission

KINDS = (
    PublicationKind.ACT_PUBLISHED,
    PublicationKind.CONSULTATION_RESULTS,
    PublicationKind.IN_FORCE,
    PublicationKind.CONSULTATION_DEADLINE,
    PublicationKind.DECISION_DEADLINE,
    PublicationKind.HEARING_DEADLINE,
)
TODAY = date(2026, 9, 18)


def _saved(
    repo: SqliteBillRepository,
    process: ProcessDetail,
    now: datetime,
    kind: PublicationKind,
) -> tuple[Bill, Publication]:
    bill = repo.upsert_summary(process, now=now).model_copy(
        update={
            "act": act(entry_into_force=date(2026, 9, 18)),
            "submission": submission(print_number="3039", consultation_end=date(2026, 9, 20)),
        }
    )
    phase = Phase(key="senate", deadline=date(2026, 9, 21), deadline_exact=True)
    hearing = Stage(
        stage_name="Wysłuchanie publiczne",
        stage_type="PublicHearing",
        date=date(2026, 9, 30),
    )
    publication = Publication(
        id=11,
        term=bill.term,
        number=bill.number,
        kind=kind,
        status=PublicationStatus.PENDING,
        channel_id="@test",
        created_at=now,
        delivery=DeliveryPlan(
            bill_json=bill.model_dump_json(),
            reply_to=42,
            today=TODAY
            if kind not in (PublicationKind.ACT_PUBLISHED, PublicationKind.CONSULTATION_RESULTS)
            else None,
            phase_json=phase.model_dump_json()
            if kind is PublicationKind.DECISION_DEADLINE
            else None,
            hearing_json=hearing.model_dump_json()
            if kind is PublicationKind.HEARING_DEADLINE
            else None,
        ),
    )
    return bill, publication


def _legacy(publisher: FakePublisher, bill: Bill, publication: Publication) -> PublishResult:
    plan = publication.delivery
    assert plan is not None, "the fixture supplies the saved notice payload"
    if publication.kind is PublicationKind.ACT_PUBLISHED:
        return publisher.publish_act_published(bill, plan.reply_to)
    if publication.kind is PublicationKind.CONSULTATION_RESULTS:
        return publisher.publish_consultation_results(bill, plan.reply_to)
    assert plan.today is not None, "the fixture saves the reminder's date"
    if publication.kind is PublicationKind.IN_FORCE:
        return publisher.publish_in_force(bill, plan.reply_to, today=plan.today)
    if publication.kind is PublicationKind.CONSULTATION_DEADLINE:
        return publisher.publish_consultation_deadline(bill, plan.reply_to, today=plan.today)
    if publication.kind is PublicationKind.DECISION_DEADLINE:
        assert plan.phase_json is not None, "the fixture saves the constitutional phase"
        return publisher.publish_decision_deadline(
            bill,
            Phase.model_validate_json(plan.phase_json),
            plan.reply_to,
            today=plan.today,
        )
    assert publication.kind is PublicationKind.HEARING_DEADLINE and plan.hearing_json is not None, (
        "the fixture saves the hearing when it creates a hearing reminder"
    )
    return publisher.publish_hearing_deadline(
        bill,
        Stage.model_validate_json(plan.hearing_json),
        plan.reply_to,
        today=plan.today,
    )


def _records(publisher: FakePublisher) -> dict[PublicationKind, tuple[object, ...]]:
    return {
        PublicationKind.ACT_PUBLISHED: tuple(publisher.acts),
        PublicationKind.CONSULTATION_RESULTS: tuple(publisher.consultation_results),
        PublicationKind.IN_FORCE: tuple(publisher.in_force),
        PublicationKind.CONSULTATION_DEADLINE: tuple(publisher.consultations),
        PublicationKind.DECISION_DEADLINE: tuple(publisher.decision_deadlines),
        PublicationKind.HEARING_DEADLINE: tuple(publisher.hearings),
    }


def _bills(publisher: FakePublisher) -> list[Bill]:
    return [
        record[0]
        for records in (
            publisher.acts,
            publisher.consultation_results,
            publisher.in_force,
            publisher.consultations,
            publisher.decision_deadlines,
            publisher.hearings,
        )
        for record in records
    ]


@pytest.mark.parametrize("kind", KINDS)
def test_prepared_notice_matches_legacy_html_and_domain_records_without_rendering(
    repo: SqliteBillRepository,
    process_3039: ProcessDetail,
    now: datetime,
    monkeypatch: pytest.MonkeyPatch,
    kind: PublicationKind,
) -> None:
    bill, publication = _saved(repo, process_3039, now, kind)
    formatter = MessageFormatter("ru", today=lambda: TODAY)
    legacy = FakePublisher(formatter=formatter)
    prepared = FakePublisher(formatter=formatter)
    _legacy(legacy, bill, publication)
    plan = publication.delivery
    assert plan is not None, "the fixture supplies the saved notice payload"
    publication = publication.model_copy(
        update={"delivery": plan.model_copy(update={"rendered_text": legacy.sent[0].text})}
    )

    def refuse_render(*args: object, **kwargs: object) -> None:
        raise AssertionError("prepared notices must use their saved HTML")

    for method in (
        "act_published",
        "consultation_results",
        "in_force",
        "consultation_deadline",
        "decision_deadline",
        "hearing_deadline",
    ):
        monkeypatch.setattr(formatter, method, refuse_render)

    result = prepared.publish_prepared(publication)

    assert result.message_id == 101
    assert len(prepared.sent) == 1
    assert prepared.sent[0].kind is kind
    assert prepared.sent[0].text == legacy.sent[0].text
    assert prepared.sent[0].bill == legacy.sent[0].bill
    assert prepared.sent[0].reply_to == legacy.sent[0].reply_to
    assert prepared.sent[0].detail == legacy.sent[0].detail
    assert _records(prepared) == _records(legacy)
    assert sum(len(records) for records in _records(prepared).values()) == 1
    assert not prepared.edited
    assert not prepared.continuation_attempts


@pytest.mark.parametrize("kind", KINDS)
def test_prepared_notice_uses_detached_bill_and_saved_reminder_facts(
    repo: SqliteBillRepository,
    process_3039: ProcessDetail,
    now: datetime,
    kind: PublicationKind,
) -> None:
    bill, publication = _saved(repo, process_3039, now, kind)
    plan = publication.delivery
    assert plan is not None, "the fixture supplies the saved notice payload"
    publication = publication.model_copy(
        update={"delivery": plan.model_copy(update={"rendered_text": "<b>Saved notice</b>"})}
    )
    title = bill.summary.title
    bill.summary = bill.summary.model_copy(update={"title": "Changed current title"})
    publisher = FakePublisher()

    publisher.publish_prepared(publication)

    recorded = _bills(publisher)
    assert len(recorded) == 1
    assert recorded[0].summary.title == title
    assert recorded[0] is not bill
    if kind is PublicationKind.CONSULTATION_DEADLINE:
        assert publisher.consultations[0][1:] == (42, TODAY)
    elif kind is PublicationKind.DECISION_DEADLINE:
        assert publisher.decision_deadlines[0][1:] == (
            Phase(key="senate", deadline=date(2026, 9, 21), deadline_exact=True),
            42,
            TODAY,
        )
    elif kind is PublicationKind.HEARING_DEADLINE:
        assert publisher.hearings[0][1:] == (
            Stage(
                stage_name="Wysłuchanie publiczne",
                stage_type="PublicHearing",
                date=date(2026, 9, 30),
            ),
            42,
            TODAY,
        )


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("failure", ["refused", "outage"])
def test_failed_prepared_notice_never_records_success(
    repo: SqliteBillRepository,
    process_3039: ProcessDetail,
    now: datetime,
    kind: PublicationKind,
    failure: Literal["refused", "outage"],
) -> None:
    _, publication = _saved(repo, process_3039, now, kind)
    plan = publication.delivery
    assert plan is not None, "the fixture supplies the saved notice payload"
    publication = publication.model_copy(
        update={"delivery": plan.model_copy(update={"rendered_text": "<b>Saved notice</b>"})}
    )
    publisher = FakePublisher(
        fail_on={"3039"} if failure == "refused" else set(),
        outage_on={"3039"} if failure == "outage" else set(),
    )
    error = TelegramUnavailableError if failure == "outage" else RuntimeError

    with pytest.raises(error):
        publisher.publish_prepared(publication)

    assert not publisher.sent
    assert not publisher.edited
    assert not any(_records(publisher).values())


@pytest.mark.parametrize("delivery", ["edit", "group", "child"])
def test_prepared_edit_group_and_continuation_do_not_record_a_notice_root(
    repo: SqliteBillRepository,
    process_3039: ProcessDetail,
    now: datetime,
    delivery: Literal["edit", "group", "child"],
) -> None:
    _, publication = _saved(repo, process_3039, now, PublicationKind.HEARING_DEADLINE)
    plan = publication.delivery
    assert plan is not None, "the fixture supplies the saved notice payload"
    plan = plan.model_copy(update={"rendered_text": "<b>Saved physical delivery</b>"})
    if delivery == "edit":
        plan = plan.model_copy(update={"edit_message_id": 501})
    elif delivery == "group":
        plan = plan.model_copy(update={"sitting_group": (11,), "group_text": plan.rendered_text})
    else:
        publication = publication.model_copy(update={"kind": PublicationKind.MESSAGE_PART})
        plan = plan.model_copy(
            update={"continuation_root": 10, "continuation_parent": 10, "continuation_index": 2}
        )
    publisher = FakePublisher()

    publisher.publish_prepared(publication.model_copy(update={"delivery": plan}))

    assert not any(_records(publisher).values())
    assert len(publisher.edited) == int(delivery == "edit")
    assert len(publisher.sent) == int(delivery != "edit")
    assert publisher.continuation_attempts == int(delivery == "child")
