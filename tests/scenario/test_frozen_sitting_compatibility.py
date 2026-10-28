import datetime as dt
from dataclasses import dataclass
from typing import Never

import pytest

from lexinform.models import (
    AgendaItem,
    DeliveryPlan,
    DeliverySchedule,
    Publication,
    PublicationKind,
    PublicationStatus,
)
from lexinform.ports import PublishResult
from lexinform.services.tracking_result import TrackingResult
from tests.harness import CHANNEL, TERM, World
from tests.scenario.test_card_remote_checkpoints import Mode, ready_world
from tests.scenario.test_tracking_agenda import ASW, _sitting

SHORT = "<b>Сохранённое заседание</b> · 17 сентября · sala 412."
NEAR_LIMIT = "<b>Сохранённое заседание</b>\n" + "я" * 3900
TAILS = ("Сохранённая вторая часть", "Сохранённая третья часть")


@dataclass(frozen=True)
class SittingCase:
    world: World
    root: int
    identities: tuple[int, ...]
    item: AgendaItem
    texts: tuple[str, ...]


def publication(world: World, identity: int) -> Publication:
    row = world.repo.publication_by_id(identity)
    assert row is not None, "the fixture stores every frozen physical part before delivery"
    return row


def saved_sitting(
    mode: Mode,
    kind: PublicationKind = PublicationKind.AGENDA,
    *,
    multipart: bool = False,
    reply_to: int | None = None,
) -> SittingCase:
    world = ready_world(mode)
    world.run()
    item = AgendaItem.for_committee(
        _sitting(), committee_name=ASW.name, text="Pierwsze czytanie (druk nr 3039), stara sala 412"
    )
    texts = (NEAR_LIMIT, *TAILS) if multipart else (SHORT,)
    day = world.clock.now()
    plan = DeliveryPlan(
        bill_json=world.bill("3039").model_dump_json(),
        rendered_text=texts[0],
        item_json=item.model_dump_json(),
        reply_to=reply_to,
        today=day.date(),
        still_meets=kind is PublicationKind.AGENDA_CANCELLED,
        policy_version="attention-v1" if mode == "on" else "legacy",
        schedule=DeliverySchedule(
            route="daily_sittings",
            detected_at=day,
            not_before=day + dt.timedelta(hours=1),
            deliver_by=day + dt.timedelta(days=7),
        ),
    )
    root = world.repo.create_publication(
        Publication(
            term=TERM,
            number="3039",
            channel_id=CHANNEL,
            kind=kind,
            ref=item.ref,
            status=PublicationStatus.QUEUED,
            created_at=day,
            delivery=plan,
        )
    )
    children: list[int] = []
    parent = root
    for index, text in enumerate(texts[1:], start=2):
        parent = world.repo.create_publication(
            Publication(
                term=TERM,
                number="3039",
                kind=PublicationKind.MESSAGE_PART,
                channel_id=CHANNEL,
                status=PublicationStatus.QUEUED,
                created_at=day,
                ref=f"message/{root}/{index}",
                delivery=DeliveryPlan(
                    bill_json=plan.bill_json,
                    rendered_text=text,
                    continuation_root=root,
                    continuation_parent=parent,
                    continuation_index=index,
                ),
            )
        )
        children.append(parent)
    assert world.repo.replace_unattempted_delivery(
        root, plan.model_copy(update={"continuation_ids": tuple(children)})
    ), "the queued fixture has not attempted any physical part"
    world.repo.mark_publication(root, PublicationStatus.FAILED, error="definite prior rejection")
    card = world.publication("3039")
    assert card is not None and card.id is not None, "the production fixture published its card"
    world.repo.mark_publication(
        card.id, PublicationStatus.SENT, message_id=999, count_attempt=False
    )
    world.clock.advance(days=120)
    world.repo.restore(world.repo.dump())
    return SittingCase(world, root, (root, *children), item, texts)


def refuse_render(*args: object, **kwargs: object) -> Never:
    raise AssertionError("frozen sittings must not be rendered, grouped or expired again")


def block_rendering(monkeypatch: pytest.MonkeyPatch, world: World) -> None:
    monkeypatch.setattr(world.publisher, "render_sittings", refuse_render)
    monkeypatch.setattr(world.publisher, "render_delivery", refuse_render)
    monkeypatch.setattr(world.publisher, "publish_sittings", refuse_render)
    monkeypatch.setattr(world.formatter, "sittings", refuse_render)


def flush(case: SittingCase) -> TrackingResult:
    poster = case.world.container.poster(dry_run=False)
    poster.start_run()
    poster.queue_sitting(publication(case.world, case.root))
    result = TrackingResult()
    poster.flush_sittings(result)
    return result


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("kind", [PublicationKind.AGENDA, PublicationKind.AGENDA_CANCELLED])
@pytest.mark.parametrize("multipart", [False, True])
def test_sitting_reader_reuses_saved_top_level_html_and_each_original_parent(
    monkeypatch: pytest.MonkeyPatch, mode: Mode, kind: PublicationKind, multipart: bool
) -> None:
    case = saved_sitting(mode, kind, multipart=multipart)
    world = case.world
    block_rendering(monkeypatch, world)
    original = world.publisher.publish_prepared
    observed: list[int] = []
    before = len(world.publisher.sent)
    saved = publication(world, case.root).delivery

    def inspect(row: Publication) -> PublishResult:
        assert row.id is not None and row.delivery is not None, (
            "prepared work has its saved identity"
        )
        index = case.identities.index(row.id)
        assert row.delivery.rendered_text == case.texts[index]
        assert publication(world, row.id).status is PublicationStatus.PENDING
        if index == 0:
            assert row.delivery.reply_to is None
        else:
            parent = publication(world, case.identities[index - 1])
            assert parent.status is PublicationStatus.SENT
            assert row.delivery.reply_to == parent.message_id
        observed.append(row.id)
        return original(row)

    monkeypatch.setattr(world.publisher, "publish_prepared", inspect)

    result = flush(case)
    flush(case)

    assert observed == list(case.identities)
    assert [out.text for out in world.publisher.sent[before:]] == list(case.texts)
    assert world.publisher.sent[before].reply_to is None
    assert publication(world, case.root).delivery == saved
    assert all(
        publication(world, identity).status is PublicationStatus.SENT
        for identity in case.identities
    )
    assert result.agenda_cancelled == int(kind is PublicationKind.AGENDA_CANCELLED)
    assert result.agenda_posted == int(kind is PublicationKind.AGENDA)


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("action", ["retry", "confirm 777"])
def test_unknown_frozen_sitting_waits_for_operator_and_preserves_its_exact_chain(
    monkeypatch: pytest.MonkeyPatch, mode: Mode, action: str
) -> None:
    kind = PublicationKind.AGENDA if action == "retry" else PublicationKind.AGENDA_CANCELLED
    case = saved_sitting(mode, kind, multipart=True)
    world = case.world
    world.repo.mark_publication(case.root, PublicationStatus.UNKNOWN, count_attempt=False)
    block_rendering(monkeypatch, world)
    before = len(world.publisher.sent)

    flush(case)
    world.container.delivery_service(dry_run=False).run()

    assert len(world.publisher.sent) == before
    assert publication(world, case.root).status is PublicationStatus.UNKNOWN
    incoming = world.command(f"/delivery {case.root} {action}")
    commands = world.container.command_service(dry_run=False)
    assert commands is not None, "the production fixture enables the technical command inbox"
    result = commands.handle_pending(min_score=3, run_started_at=world.clock.now())
    assert result.handled == 1 and result.failed == 0

    world.container.delivery_service(dry_run=False).run()
    flush(case)

    expected = case.texts if action == "retry" else case.texts[1:]
    assert [out.text for out in world.publisher.sent[before:]] == list(expected)
    assert all(
        publication(world, identity).status is PublicationStatus.SENT
        for identity in case.identities
    )
    root = publication(world, case.root)
    assert root.delivery is not None and root.delivery.reply_to is None, (
        "operator resolution preserves the original top-level destination"
    )
    first = world.publisher.sent[before]
    assert first.reply_to == (None if action == "retry" else 777)
    history = world.repo.delivery_resolutions(case.root)
    assert len(history) == 1 and history[0].command == incoming


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
def test_frozen_sitting_cannot_be_dropped_and_keeps_saved_card_link_context(
    monkeypatch: pytest.MonkeyPatch, mode: Mode
) -> None:
    case = saved_sitting(mode, reply_to=42)
    world = case.world
    block_rendering(monkeypatch, world)
    before = len(world.publisher.sent)
    poster = world.container.poster(dry_run=False)
    saved = publication(world, case.root)
    poster.queue_sitting(saved)

    poster.drop_sitting(world.bill("3039"), case.item.ref)

    assert publication(world, case.root) == saved
    result = TrackingResult()
    poster.flush_sittings(result)
    assert result.agenda_posted == 1
    assert len(world.publisher.sent) == before + 1
    assert world.publisher.sent[-1].text == SHORT
    assert publication(world, case.root).delivery == saved.delivery
    assert world.publisher.sent[-1].reply_to is None
