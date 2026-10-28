from datetime import datetime
from typing import Literal

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.errors import TelegramUnavailableError
from lexinform.models import (
    Bill,
    DeliveryPlan,
    ProcessDetail,
    Publication,
    PublicationKind,
    PublicationStatus,
    Stage,
    StatusChange,
    UpdateSnapshot,
)
from tests.fakes import FakePublisher
from tests.harness import act

FROZEN_TEXT = "<b>Saved status update</b>"


@pytest.fixture
def saved_update(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> tuple[Bill, StatusChange, Publication]:
    bill = repo.upsert_summary(process_3039, now=now).model_copy(update={"act": act()})
    change = StatusChange(
        id=71,
        term=bill.term,
        number=bill.number,
        old_fingerprint="before",
        new_fingerprint="saved",
        new_stages=[Stage(stage_name="Sprawozdanie komisji", stage_type="CommitteeReport")],
        detected_at=now,
    )
    publication = Publication(
        id=91,
        term=bill.term,
        number=bill.number,
        kind=PublicationKind.STATUS_UPDATE,
        status=PublicationStatus.PENDING,
        channel_id="@test",
        created_at=now,
        delivery=DeliveryPlan(
            bill_json=bill.model_dump_json(),
            change_json=change.model_dump_json(),
            reply_to=42,
            rendered_text=FROZEN_TEXT,
        ),
    )
    return bill, change, publication


@pytest.mark.parametrize("with_act", [False, True])
def test_prepared_single_update_matches_legacy_payload_and_bookkeeping_without_rendering(
    saved_update: tuple[Bill, StatusChange, Publication],
    now: datetime,
    monkeypatch: pytest.MonkeyPatch,
    with_act: bool,
) -> None:
    bill, change, publication = saved_update
    formatter = MessageFormatter("ru", today=lambda: now.date())
    legacy = FakePublisher(formatter=formatter)
    prepared = FakePublisher(formatter=formatter)
    legacy.publish_status_update(bill, change, 42, with_act=with_act)
    plan = publication.delivery
    assert plan is not None, "the fixture supplies a saved update payload"
    publication = publication.model_copy(
        update={
            "delivery": plan.model_copy(
                update={"rendered_text": legacy.sent[0].text, "with_act": with_act}
            )
        }
    )

    def refuse_render(*args: object, **kwargs: object) -> None:
        raise AssertionError("prepared delivery must use its saved HTML")

    monkeypatch.setattr(formatter, "status_update", refuse_render)

    result = prepared.publish_prepared(publication)

    assert result.message_id == 101
    assert prepared.sent == legacy.sent
    assert prepared.updates == legacy.updates == [(bill, change, 42)]
    assert not prepared.acts
    assert not prepared.edited


def test_prepared_single_update_bookkeeping_uses_detached_saved_snapshots(
    saved_update: tuple[Bill, StatusChange, Publication],
) -> None:
    bill, change, publication = saved_update
    saved_title = bill.summary.title
    bill.summary = bill.summary.model_copy(update={"title": "Changed current title"})
    change.new_fingerprint = "changed"
    change.new_stages.clear()
    publisher = FakePublisher()

    publisher.publish_prepared(publication)

    posted_bill, posted_change, parent = publisher.updates[0]
    assert posted_bill.summary.title == saved_title
    assert posted_change.new_fingerprint == "saved"
    assert [stage.stage_type for stage in posted_change.new_stages] == ["CommitteeReport"]
    assert parent == 42
    assert posted_bill is not bill and posted_change is not change
    assert publisher.texts(PublicationKind.STATUS_UPDATE) == [FROZEN_TEXT]


@pytest.mark.parametrize("failure", ["refused", "outage"])
def test_prepared_single_update_refusal_never_records_successful_bookkeeping(
    saved_update: tuple[Bill, StatusChange, Publication],
    failure: Literal["refused", "outage"],
) -> None:
    _, _, publication = saved_update
    publisher = FakePublisher(
        fail_on={"3039"} if failure == "refused" else set(),
        outage_on={"3039"} if failure == "outage" else set(),
    )
    error = TelegramUnavailableError if failure == "outage" else RuntimeError

    with pytest.raises(error):
        publisher.publish_prepared(publication)

    assert not publisher.sent
    assert not publisher.edited
    assert not publisher.updates


def test_prepared_status_edit_is_recorded_only_as_an_edit(
    saved_update: tuple[Bill, StatusChange, Publication],
) -> None:
    _, _, publication = saved_update
    plan = publication.delivery
    assert plan is not None, "the fixture supplies a saved update payload"
    publication = publication.model_copy(
        update={"delivery": plan.model_copy(update={"edit_message_id": 501})}
    )
    publisher = FakePublisher()

    result = publisher.publish_prepared(publication)

    assert result.message_id == 501
    assert not publisher.sent
    assert not publisher.updates
    assert len(publisher.edited) == 1
    assert (publisher.edited[0][0].text, publisher.edited[0][1]) == (FROZEN_TEXT, 501)


@pytest.mark.parametrize("group", ["updates", "sittings"])
def test_prepared_group_does_not_claim_a_single_update_delivery(
    saved_update: tuple[Bill, StatusChange, Publication],
    group: Literal["updates", "sittings"],
) -> None:
    bill, change, publication = saved_update
    plan = publication.delivery
    assert plan is not None, "the fixture supplies a saved update payload"
    planned = (
        plan.model_copy(
            update={
                "update_group": (
                    UpdateSnapshot(
                        publication_id=91,
                        bill_json=bill.model_dump_json(),
                        change_json=change.model_dump_json(),
                        card_message_id=42,
                    ),
                ),
                "group_text": "<b>Saved shared update</b>",
            }
        )
        if group == "updates"
        else plan.model_copy(
            update={"sitting_group": (91,), "group_text": "<b>Saved shared sitting</b>"}
        )
    )
    publisher = FakePublisher()

    prepared = publication.model_copy(update={"delivery": planned})
    if group == "sittings":
        with pytest.raises(ValueError, match="cannot carry a sitting group"):
            publisher.publish_prepared(prepared)
    else:
        publisher.publish_prepared(prepared)

    assert len(publisher.sent) == int(group == "updates")
    if publisher.sent:
        assert publisher.sent[0].text == planned.group_text
    assert not publisher.updates
    assert not publisher.edited
