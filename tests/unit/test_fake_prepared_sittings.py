from datetime import date, datetime
from typing import Literal

import pytest

from lexinform.adapters.publisher_base import RenderingPublisher
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.errors import DeliveryUncertainError, TelegramUnavailableError
from lexinform.models import (
    AgendaItem,
    DeliveryPlan,
    ProcessDetail,
    Publication,
    PublicationKind,
    PublicationStatus,
    SittingNews,
)
from lexinform.ports import PublishResult
from tests.fakes import FakePublisher

FROZEN_ROOT = "<b>Saved first physical sitting part</b>"


def _sitting(
    repo: SqliteBillRepository,
    process: ProcessDetail,
    now: datetime,
    kind: PublicationKind,
) -> tuple[Publication, SittingNews]:
    bill = repo.upsert_summary(process, now=now)
    item = AgendaItem(
        kind="committee",
        ref="ASW/136/2026-09-17",
        date=date(2026, 9, 17),
        committee_code="ASW",
    )
    moved = item.model_copy(update={"date": date(2026, 9, 16)})
    news = SittingNews(
        bill=bill,
        item=item,
        moved_from=moved,
        cancelled=kind is PublicationKind.AGENDA_CANCELLED,
        still_meets=True,
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
            item_json=item.model_dump_json(),
            moved_from_json=moved.model_dump_json(),
            still_meets=True,
            rendered_text=FROZEN_ROOT,
            continuation_ids=(12,),
        ),
    )
    return publication, news


@pytest.mark.parametrize("kind", [PublicationKind.AGENDA, PublicationKind.AGENDA_CANCELLED])
def test_prepared_sitting_root_records_one_logical_event_and_child_only_the_physical_part(
    repo: SqliteBillRepository,
    process_3039: ProcessDetail,
    now: datetime,
    monkeypatch: pytest.MonkeyPatch,
    kind: PublicationKind,
) -> None:
    publication, news = _sitting(repo, process_3039, now, kind)
    publisher = FakePublisher()

    def refuse_render(*args: object, **kwargs: object) -> None:
        raise AssertionError("prepared sitting parts must use their saved HTML")

    monkeypatch.setattr(publisher, "render_sittings", refuse_render)
    monkeypatch.setattr(publisher, "publish_sittings", refuse_render)
    child = publication.model_copy(
        update={
            "id": 12,
            "kind": PublicationKind.MESSAGE_PART,
            "delivery": DeliveryPlan(
                bill_json=news.bill.model_dump_json(),
                rendered_text="<b>Saved final physical sitting part</b>",
                continuation_root=11,
                continuation_parent=11,
                continuation_index=2,
                reply_to=101,
            ),
        }
    )

    publisher.publish_prepared(publication)
    publisher.publish_prepared(child)

    assert len(publisher.sent) == 2
    assert (publisher.sent[0].kind, publisher.sent[0].text) == (kind, FROZEN_ROOT)
    assert publisher.sent[0].reply_to is None
    assert publisher.sent[1].reply_to == 101
    assert publisher.continuation_attempts == 1
    assert publisher.sittings == [(news,)]
    assert publisher.sittings[0][0].card_message_id is None
    assert publisher.agendas == ([] if news.cancelled else [(news.bill, news.item, None)])
    assert publisher.agenda_cancellations == (
        [(news.bill, news.item, True)] if news.cancelled else []
    )


@pytest.mark.parametrize("kind", [PublicationKind.AGENDA, PublicationKind.AGENDA_CANCELLED])
@pytest.mark.parametrize("reply_to", [None, 42])
def test_prepared_sitting_bookkeeping_uses_its_saved_bill_snapshot(
    repo: SqliteBillRepository,
    process_3039: ProcessDetail,
    now: datetime,
    kind: PublicationKind,
    reply_to: int | None,
) -> None:
    publication, news = _sitting(repo, process_3039, now, kind)
    plan = publication.delivery
    assert plan is not None, "the fixture supplies the saved sitting payload"
    publication = publication.model_copy(
        update={"delivery": plan.model_copy(update={"reply_to": reply_to})}
    )
    title = news.bill.summary.title
    news.bill.summary = news.bill.summary.model_copy(update={"title": "Changed current title"})
    publisher = FakePublisher()

    publisher.publish_prepared(publication)

    delivered = publisher.sittings[0][0]
    assert delivered.bill.summary.title == title
    assert delivered.bill is not news.bill
    assert delivered.item == news.item
    assert delivered.moved_from == news.moved_from
    assert delivered.card_message_id == reply_to
    assert publisher.sent[0].reply_to is None


@pytest.mark.parametrize("kind", [PublicationKind.AGENDA, PublicationKind.AGENDA_CANCELLED])
@pytest.mark.parametrize("failure", ["refused", "outage"])
def test_failed_prepared_sitting_records_no_logical_event_until_a_successful_send(
    repo: SqliteBillRepository,
    process_3039: ProcessDetail,
    now: datetime,
    kind: PublicationKind,
    failure: Literal["refused", "outage"],
) -> None:
    publication, news = _sitting(repo, process_3039, now, kind)
    publisher = FakePublisher(
        fail_on={"3039"} if failure == "refused" else set(),
        outage_on={"3039"} if failure == "outage" else set(),
    )
    error = TelegramUnavailableError if failure == "outage" else RuntimeError

    with pytest.raises(error):
        publisher.publish_prepared(publication)

    assert not publisher.sent
    assert not publisher.sittings
    assert not publisher.agendas
    assert not publisher.agenda_cancellations
    publisher.fail_on.clear()
    publisher.outage_on.clear()

    publisher.publish_prepared(publication)

    assert len(publisher.sent) == 1
    assert publisher.sittings == [(news,)]


@pytest.mark.parametrize("kind", [PublicationKind.AGENDA, PublicationKind.AGENDA_CANCELLED])
def test_accepted_but_unknown_prepared_sitting_is_not_recorded_as_a_successful_event(
    repo: SqliteBillRepository,
    process_3039: ProcessDetail,
    now: datetime,
    monkeypatch: pytest.MonkeyPatch,
    kind: PublicationKind,
) -> None:
    publication, _ = _sitting(repo, process_3039, now, kind)
    publisher = FakePublisher()
    original = RenderingPublisher.publish_prepared

    def uncertain(port: RenderingPublisher, saved: Publication) -> PublishResult:
        original(port, saved)
        raise DeliveryUncertainError("accepted physical root but response lost")

    monkeypatch.setattr(RenderingPublisher, "publish_prepared", uncertain)

    with pytest.raises(DeliveryUncertainError):
        publisher.publish_prepared(publication)

    assert len(publisher.sent) == 1
    assert publisher.sent[0].text == FROZEN_ROOT
    assert not publisher.sittings
    assert not publisher.agendas
    assert not publisher.agenda_cancellations
