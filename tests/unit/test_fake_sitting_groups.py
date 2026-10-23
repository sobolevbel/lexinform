from datetime import date, datetime
from typing import Literal

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.errors import TelegramUnavailableError
from lexinform.models import (
    AgendaItem,
    DeliveryPlan,
    ProcessDetail,
    Publication,
    PublicationKind,
    PublicationStatus,
    SittingNews,
)
from tests.fakes import FakePublisher

FROZEN_TEXT = "<b>Saved sitting roundup</b>"


def _group(
    repo: SqliteBillRepository, process: ProcessDetail, now: datetime
) -> tuple[dict[int, Publication], tuple[SittingNews, ...]]:
    first = repo.upsert_summary(process, now=now)
    second = first.model_copy(
        update={"summary": first.summary.model_copy(update={"number": "3040"})}
    )
    item = AgendaItem(
        kind="committee",
        ref="ASW/136/2026-09-17",
        date=date(2026, 9, 17),
        committee_code="ASW",
    )
    news = (
        SittingNews(bill=first, item=item, card_message_id=41),
        SittingNews(
            bill=second,
            item=item,
            cancelled=True,
            still_meets=True,
            card_message_id=42,
        ),
    )
    members = {
        identity: Publication(
            id=identity,
            term=line.bill.term,
            number=line.bill.number,
            kind=PublicationKind.AGENDA_CANCELLED if line.cancelled else PublicationKind.AGENDA,
            status=PublicationStatus.PENDING,
            channel_id="@test",
            created_at=now,
            delivery=DeliveryPlan(
                bill_json=line.bill.model_dump_json(),
                item_json=line.item.model_dump_json(),
                still_meets=line.still_meets,
                reply_to=line.card_message_id,
                sitting_group=(11, 12),
                group_text=FROZEN_TEXT,
            ),
        )
        for identity, line in zip((11, 12), news, strict=True)
    }
    return members, news


def test_prepared_sitting_group_records_saved_members_and_sends_frozen_text_once(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> None:
    members, news = _group(repo, process_3039, now)
    publisher = FakePublisher(publication_by_id=members.get)

    result = publisher.publish_prepared(members[11])

    assert result.message_id == 101
    assert publisher.texts(PublicationKind.AGENDA) == [FROZEN_TEXT]
    assert len(publisher.sent) == 1
    assert publisher.sittings == [news]
    assert publisher.agendas == [(news[0].bill, news[0].item, 41)]
    assert publisher.agenda_cancellations == [(news[1].bill, news[1].item, True)]


@pytest.mark.parametrize("failure", ["refused", "outage"])
def test_prepared_sitting_group_checks_every_member_before_send(
    repo: SqliteBillRepository,
    process_3039: ProcessDetail,
    now: datetime,
    failure: Literal["refused", "outage"],
) -> None:
    members, _ = _group(repo, process_3039, now)
    publisher = FakePublisher(
        fail_on={"3040"} if failure == "refused" else set(),
        outage_on={"3040"} if failure == "outage" else set(),
        publication_by_id=members.get,
    )
    error = TelegramUnavailableError if failure == "outage" else RuntimeError

    with pytest.raises(error):
        publisher.publish_prepared(members[11])

    assert not publisher.sent
    assert not publisher.sittings
    assert not publisher.agendas
    assert not publisher.agenda_cancellations


def test_prepared_sitting_group_without_lookup_records_only_the_actual_send(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> None:
    members, _ = _group(repo, process_3039, now)
    publisher = FakePublisher()

    publisher.publish_prepared(members[11])

    assert publisher.texts(PublicationKind.AGENDA) == [FROZEN_TEXT]
    assert not publisher.sittings
    assert not publisher.agendas
    assert not publisher.agenda_cancellations
