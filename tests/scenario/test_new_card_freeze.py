import datetime as dt
from dataclasses import replace
from typing import Literal

import pytest

from lexinform.adapters.telegram_format import RenderedMessage
from lexinform.models import (
    Bill,
    DeliveryPlan,
    PrintInfo,
    Publication,
    PublicationKind,
    PublicationStatus,
)
from lexinform.ports import PublishResult
from tests.fakes import make_analysis, make_comparison
from tests.harness import CHANNEL, TERM, World

AttentionMode = Literal["off", "shadow", "on"]
TITLE = "Projekt ustawy o cudzoziemcach"


def _world(mode: AttentionMode, kind: PublicationKind, *, long: bool = False) -> World:
    analysis = make_analysis()
    if long:
        analysis.practical_impact = "Старые заявления не подавать заново; 200 PLN. " * 220
    world = World(
        reader_attention=mode,
        llm_script={"3039": analysis},
        joint_script={
            "3039": make_comparison(differences=["Новый срок для подачи заявления. " * 220])
        }
        if long
        else None,
    )
    if kind is PublicationKind.JOINT_BILL:
        world.add_bill("3038", TITLE)
        world.run()
    world.add_bill("3039", TITLE)
    if kind is PublicationKind.JOINT_BILL:
        world.touch("3039", world.clock.now(), prints_considered_jointly=("3038",))
        world.touch("3038", world.clock.now(), prints_considered_jointly=("3039",))
    world.run(max_publish=0)
    return world


def _legacy_text(world: World, publication: Publication) -> str:
    plan = publication.delivery
    assert plan is not None, "a card carries the bill and print snapshots before rendering"
    bill = Bill.model_validate_json(plan.bill_json)
    info = PrintInfo.model_validate_json(plan.print_json) if plan.print_json else None
    if publication.kind is PublicationKind.NEW_BILL:
        return world.formatter.new_bill(bill, info).text
    assert plan.primary_json, "a joint reply saves the primary bill for its original thread"
    return world.formatter.joint_bill(bill, Bill.model_validate_json(plan.primary_json), info).text


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("kind", [PublicationKind.NEW_BILL, PublicationKind.JOINT_BILL])
def test_new_card_freezes_the_same_html_as_the_existing_renderer_before_sending(
    mode: AttentionMode, kind: PublicationKind, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _world(mode, kind)
    send = world.publisher.publish_prepared
    saved: list[str] = []

    def inspect(publication: Publication) -> PublishResult:
        row = world.repo.publication_by_id(publication.id or 0)
        assert row is not None and row.delivery is not None, (
            "the send is preceded by a saved payload"
        )
        assert row.status is PublicationStatus.PENDING
        expected = _legacy_text(world, publication)
        assert row.delivery.rendered_text == expected
        saved.append(expected)
        return send(publication)

    monkeypatch.setattr(world.publisher, "publish_prepared", inspect)

    world.run()

    assert len(saved) == 1
    assert world.publisher.texts(kind)[-1] == saved[0]
    if kind is PublicationKind.JOINT_BILL:
        assert world.publisher.sent[-1].reply_to == world.card_id("3038")
        assert len(world.publisher.joint_bills) == 1
    else:
        assert len(world.publisher.new_bills) == 1


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("kind", [PublicationKind.NEW_BILL, PublicationKind.JOINT_BILL])
def test_card_retry_keeps_frozen_html_after_state_and_formatter_change(
    mode: AttentionMode, kind: PublicationKind, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _world(mode, kind)
    world.publisher.fail_on.add("3039")
    world.run()
    failed = world.publication("3039", kind)
    assert failed is not None and failed.delivery is not None, (
        "a failed card retains its saved HTML"
    )
    frozen = failed.delivery.rendered_text
    assert frozen
    record = world.bill("3039").analysis
    assert record is not None, "the publish candidate has an analysis"
    changed = record.analysis.model_copy(update={"summary": "Другая редакция после отправки"})
    world.repo.save_analysis(TERM, "3039", record.model_copy(update={"analysis": changed}))
    world.clock.advance(days=7)
    world.repo.restore(world.repo.dump())
    world.container = replace(world.container)
    world.pipeline = world.container.pipeline(dry_run=False)

    def changed_card(
        bill: Bill, info: PrintInfo | None, *, today: dt.date | None = None
    ) -> RenderedMessage:
        return RenderedMessage(text="Changed card renderer")

    def changed_joint(
        bill: Bill, primary: Bill, info: PrintInfo | None, *, today: dt.date | None = None
    ) -> RenderedMessage:
        return RenderedMessage(text="Changed joint renderer")

    monkeypatch.setattr(world.formatter, "new_bill", changed_card)
    monkeypatch.setattr(world.formatter, "joint_bill", changed_joint)
    world.publisher.fail_on.clear()

    world.run(track=False)

    assert world.publisher.texts(kind)[-1] == frozen
    assert len([message for message in world.publisher.sent if message.number == "3039"]) == 1
    if kind is PublicationKind.JOINT_BILL:
        assert world.publisher.sent[-1].reply_to == failed.delivery.reply_to


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("kind", [PublicationKind.NEW_BILL, PublicationKind.JOINT_BILL])
def test_multipart_card_saves_every_physical_part_before_the_first_api_call(
    mode: AttentionMode, kind: PublicationKind, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _world(mode, kind, long=True)
    send = world.publisher.publish_prepared
    inspected: list[tuple[str, ...]] = []

    def inspect(publication: Publication) -> PublishResult:
        plan = publication.delivery
        assert plan is not None, "each physical send carries its saved delivery"
        if publication.kind is kind:
            assert plan.continuation_ids
            expected = world.publisher.split_message(_legacy_text(world, publication))
            texts = [plan.rendered_text]
            parent = publication.id
            for identity in plan.continuation_ids:
                row = world.repo.publication_by_id(identity)
                assert row is not None and row.delivery is not None, (
                    "all parts are saved before send"
                )
                assert row.status is PublicationStatus.QUEUED and row.attempts == 0
                assert row.delivery.continuation_parent == parent
                texts.append(row.delivery.rendered_text)
                parent = identity
            assert tuple(texts) == expected
            inspected.append(expected)
        return send(publication)

    monkeypatch.setattr(world.publisher, "publish_prepared", inspect)

    world.run()

    assert len(inspected) == 1
    assert (
        tuple(message.text for message in world.publisher.sent if message.number == "3039")
        == (inspected[0])
    )


def test_attempted_legacy_card_without_html_keeps_the_existing_send_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _world("off", PublicationKind.NEW_BILL)
    publication_id = world.repo.create_publication(
        Publication(
            term=TERM,
            number="3039",
            kind=PublicationKind.NEW_BILL,
            channel_id=CHANNEL,
            status=PublicationStatus.QUEUED,
            created_at=world.clock.now(),
            delivery=DeliveryPlan(
                bill_json=world.bill("3039").model_dump_json(),
                print_json=world.gateway.prints["3039"].model_dump_json(),
            ),
        )
    )
    world.repo.mark_publication(publication_id, PublicationStatus.FAILED, count_attempt=True)

    def no_prepared_send(publication: Publication) -> PublishResult:
        raise AssertionError("attempted legacy payload must keep its existing send path")

    monkeypatch.setattr(world.publisher, "publish_prepared", no_prepared_send)

    report = world.run()

    assert report.published == 1
    assert len(world.publisher.new_bills) == 1
    sent = world.repo.publication_by_id(publication_id)
    assert sent is not None and sent.delivery is not None, "legacy delivery identity survives retry"
    assert sent.status is PublicationStatus.SENT and sent.delivery.rendered_text == ""
