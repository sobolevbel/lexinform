from pathlib import Path

import pytest

from lexinform.models import (
    Bill,
    DeliveryPlan,
    Phase,
    PrintInfo,
    Publication,
    PublicationKind,
    PublicationStatus,
    Stage,
    StatusChange,
    UpdateSnapshot,
)
from lexinform.ports import PublishResult
from tests.checkpoint_state import saved_state
from tests.fakes import FakePublisher
from tests.harness import CHANNEL, World
from tests.scenario.test_card_remote_checkpoints import connect, git, ready_world
from tests.scenario.test_notice_remote_checkpoints import KINDS
from tests.scenario.test_notice_remote_checkpoints import queued as notice_queued
from tests.scenario.test_update_remote_checkpoints import (
    plan,
    row,
)
from tests.scenario.test_update_remote_checkpoints import (
    queued as update_queued,
)
from tests.scenario.test_update_remote_checkpoints import (
    send as update_send,
)

LONG_HTML = "<b>Срок 30 дней; 200 PLN; прежние заявления.</b>\n" + "Сохранённый текст. " * 600


@pytest.mark.parametrize(
    "kind",
    [PublicationKind.AGENDA, PublicationKind.AGENDA_CANCELLED, PublicationKind.STATUS_UPDATE],
)
def test_direct_callback_renderer_preserves_existing_group_contracts(kind: PublicationKind) -> None:
    world = World()
    saved = DeliveryPlan(
        bill_json="{}",
        render_origin="legacy_snapshot",
        group_text="<b>Saved group HTML</b>",
        sitting_group=(1,) if kind is not PublicationKind.STATUS_UPDATE else (),
        update_group=(UpdateSnapshot(publication_id=1, bill_json="{}", change_json="{}"),)
        if kind is PublicationKind.STATUS_UPDATE
        else (),
    )
    publication = Publication(
        term=10,
        number="3039",
        kind=kind,
        status=PublicationStatus.FAILED,
        channel_id=CHANNEL,
        created_at=world.clock.now(),
        delivery=saved,
    )

    assert world.publisher.render_delivery(publication) == saved.group_text


def callback_text(publisher: FakePublisher, publication: Publication) -> str:
    saved = publication.delivery
    assert saved is not None, "the fixture stores semantic context before invoking its old callback"
    bill = Bill.model_validate_json(saved.bill_json)
    kind = publication.kind
    info = PrintInfo.model_validate_json(saved.print_json) if saved.print_json else None
    if kind is PublicationKind.NEW_BILL:
        publisher.publish_new_bill(bill, info)
    elif kind is PublicationKind.JOINT_BILL:
        assert saved.primary_json is not None, "the joint fixture records its primary bill"
        publisher.publish_joint_bill(
            bill, Bill.model_validate_json(saved.primary_json), info, saved.reply_to
        )
    elif kind is PublicationKind.STATUS_UPDATE:
        change = StatusChange.model_validate_json(saved.change_json)
        if saved.edit_message_id is not None:
            assert saved.today is not None, "the update edit fixture retains its original date"
            publisher.edit_status_update(
                bill,
                change,
                message_id=saved.edit_message_id,
                today=saved.today,
                with_act=saved.with_act,
            )
            return publisher.edited[-1][0].text
        publisher.publish_status_update(bill, change, saved.reply_to, with_act=saved.with_act)
    elif kind is PublicationKind.ACT_PUBLISHED:
        publisher.publish_act_published(bill, saved.reply_to)
    elif kind is PublicationKind.CONSULTATION_RESULTS:
        publisher.publish_consultation_results(bill, saved.reply_to)
    else:
        assert saved.today is not None, "every remaining old notice callback requires a saved day"
        if kind is PublicationKind.IN_FORCE:
            publisher.publish_in_force(bill, saved.reply_to, today=saved.today)
        elif kind is PublicationKind.CONSULTATION_DEADLINE:
            publisher.publish_consultation_deadline(bill, saved.reply_to, today=saved.today)
        elif kind is PublicationKind.DECISION_DEADLINE:
            assert saved.phase_json is not None, "the decision fixture retains its phase"
            publisher.publish_decision_deadline(
                bill, Phase.model_validate_json(saved.phase_json), saved.reply_to, today=saved.today
            )
        else:
            assert kind is PublicationKind.HEARING_DEADLINE and saved.hearing_json is not None, (
                "the callback fixture covers only the six supported notice kinds"
            )
            publisher.publish_hearing_deadline(
                bill,
                Stage.model_validate_json(saved.hearing_json),
                saved.reply_to,
                today=saved.today,
            )
    return publisher.sent[-1].text


@pytest.mark.parametrize("kind", [PublicationKind.NEW_BILL, PublicationKind.JOINT_BILL])
@pytest.mark.parametrize("has_delivery", [False, True])
@pytest.mark.parametrize("retry_status", [PublicationStatus.QUEUED, PublicationStatus.FAILED])
def test_attempted_first_publication_freezes_the_saved_card_plan_and_keeps_its_attempts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: PublicationKind,
    has_delivery: bool,
    retry_status: PublicationStatus,
) -> None:
    world = ready_world("on", kind)
    service = world.container.publishing_service(dry_run=False)
    bill = world.bill("3039")
    card = service.plan(bill)
    saved = DeliveryPlan(
        bill_json=card.bill.model_dump_json(),
        print_json=card.print_info.model_dump_json() if card.print_info else None,
        primary_json=card.primary.model_dump_json() if card.primary else None,
        reply_to=card.primary_message_id,
        today=world.clock.now().date(),
        policy_version="attention-v1",
    )
    identity = world.repo.create_publication(
        Publication(
            term=bill.term,
            number=bill.number,
            kind=kind,
            status=PublicationStatus.FAILED,
            channel_id=CHANNEL,
            delivery=saved if has_delivery else None,
            created_at=world.clock.now(),
        )
    )
    world.repo.mark_publication(identity, PublicationStatus.FAILED)
    world.repo.mark_publication(identity, retry_status, count_attempt=False)
    world.clock.advance(days=8)
    expected = callback_text(
        world.publisher, row(world, identity).model_copy(update={"delivery": saved})
    )
    before = len(world.publisher.sent)
    remote = connect(world, tmp_path)
    prepared = world.publisher.publish_prepared
    observed: list[tuple[int, str]] = []
    oracle = FakePublisher(formatter=world.formatter)

    def inspect(publication: Publication) -> PublishResult:
        with saved_state(git(remote, "show", "state:state.sql")) as durable:
            actual = row(durable, identity)
            assert actual.status is PublicationStatus.PENDING and actual.attempts == 1
            assert actual.delivery == publication.delivery
            expected_text = callback_text(oracle, actual)
            assert plan(durable, identity).rendered_text == expected_text
            if has_delivery:
                assert expected_text == expected
                assert plan(durable, identity).bill_json == saved.bill_json
            else:
                actual_bill = Bill.model_validate_json(plan(durable, identity).bill_json)
                assert actual_bill.model_dump(exclude={"joint"}) == card.bill.model_dump(
                    exclude={"joint"}
                )
                if kind is PublicationKind.JOINT_BILL:
                    assert actual_bill.joint is not None
            assert plan(durable, identity).primary_json == saved.primary_json
            assert plan(durable, identity).reply_to == saved.reply_to
            observed.append((identity, expected_text))
            return prepared(publication)

    monkeypatch.setattr(world.publisher, "publish_prepared", inspect)

    assert world.container.publishing_service(dry_run=False).publish_bill(bill)

    assert len(observed) == 1 and observed[0][0] == identity
    assert len(world.publisher.sent) == before + 1
    assert world.publisher.sent[-1].text == observed[0][1]
    assert row(world, identity).attempts == 1
    assert not world.llm.contexts


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("parent_none", [False, True])
@pytest.mark.parametrize("retry_status", [PublicationStatus.QUEUED, PublicationStatus.FAILED])
def test_attempted_attention_notices_freeze_callback_bytes_and_keep_top_level_none(
    monkeypatch: pytest.MonkeyPatch,
    kind: PublicationKind,
    parent_none: bool,
    retry_status: PublicationStatus,
) -> None:
    case = notice_queued(kind, "on")
    world = case.world
    saved = plan(world, case.identity)
    if parent_none:
        saved = saved.model_copy(update={"reply_to": None})
        assert world.repo.replace_unattempted_delivery(case.identity, saved)
    world.repo.mark_publication(case.identity, PublicationStatus.FAILED)
    world.repo.mark_publication(case.identity, retry_status, count_attempt=False)
    world.clock.advance(days=8)
    expected = callback_text(world.publisher, row(world, case.identity))
    before = len(world.publisher.sent)
    prepared = world.publisher.publish_prepared

    def inspect(publication: Publication) -> PublishResult:
        frozen = plan(world, case.identity)
        assert frozen.rendered_text == expected
        assert frozen.today == saved.today and frozen.reply_to == saved.reply_to
        assert frozen.bill_json == saved.bill_json and frozen.participation == saved.participation
        assert frozen.attention == saved.attention
        assert row(world, case.identity).attempts == 1
        return prepared(publication)

    monkeypatch.setattr(world.publisher, "publish_prepared", inspect)

    assert world.container.poster(dry_run=False).deliver(row(world, case.identity))

    assert len(world.publisher.sent) == before + 1
    assert world.publisher.sent[-1].text == expected
    assert world.publisher.sent[-1].reply_to == saved.reply_to
    assert row(world, case.identity).attempts == 1


@pytest.mark.parametrize("edit", [False, True])
@pytest.mark.parametrize("entrypoint", ["status_update", "deliver"])
@pytest.mark.parametrize("retry_status", [PublicationStatus.QUEUED, PublicationStatus.FAILED])
def test_attempted_attention_update_uses_actual_callback_date_and_entrypoint_parent(
    edit: bool, entrypoint: str, retry_status: PublicationStatus
) -> None:
    case = update_queued("on")
    world = case.world
    saved = plan(world, case.identity).model_copy(
        update={"edit_message_id": 444 if edit else None, "reply_to": None}
    )
    assert world.repo.replace_unattempted_delivery(case.identity, saved)
    world.repo.mark_publication(case.identity, PublicationStatus.FAILED)
    world.repo.mark_publication(case.identity, retry_status, count_attempt=False)
    world.clock.advance(days=8)
    expected = callback_text(world.publisher, row(world, case.identity))
    before = len(world.publisher.edited) if edit else len(world.publisher.sent)
    card = world.publication("3039")
    assert card is not None, "the status_update entrypoint has a delivered owner card"

    assert (
        update_send(case)
        if entrypoint == "status_update"
        else world.container.poster(dry_run=False).deliver(row(world, case.identity))
    )

    frozen = plan(world, case.identity)
    assert frozen.rendered_text == expected and frozen.edit_message_id == saved.edit_message_id
    assert frozen.today == (saved.today if edit else world.clock.now().date())
    assert frozen.bill_json == saved.bill_json and frozen.attention == saved.attention
    assert frozen.reply_to == (
        card.message_id if not edit and entrypoint == "status_update" else None
    )
    if edit:
        assert len(world.publisher.edited) == before + 1
        assert world.publisher.edited[-1][1] == 444
        assert world.publisher.edited[-1][0].text == expected
    else:
        assert len(world.publisher.sent) == before + 1
        assert world.publisher.sent[-1].reply_to == frozen.reply_to
    assert row(world, case.identity).attempts == 1


@pytest.mark.parametrize("policy", ["legacy", "attention-v1"])
def test_oversized_retry_edit_freezes_all_parts_and_releases_only_after_the_leaf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, policy: str
) -> None:
    case = update_queued("on")
    world = case.world
    saved = plan(world, case.identity).model_copy(
        update={"edit_message_id": 444, "policy_version": policy}
    )
    assert world.repo.replace_unattempted_delivery(case.identity, saved)
    world.repo.mark_publication(case.identity, PublicationStatus.FAILED)
    monkeypatch.setattr(world.publisher, "render_delivery", lambda publication: LONG_HTML)
    expected = world.publisher.split_message(LONG_HTML)
    assert len(expected) >= 3, "the fixture exercises an edit root and multiple receipt parents"
    remote = connect(world, tmp_path)
    prepared = world.publisher.publish_prepared
    observed: list[int] = []

    def inspect(publication: Publication) -> PublishResult:
        with saved_state(git(remote, "show", "state:state.sql")) as durable:
            frozen = plan(durable, case.identity)
            identities = (case.identity, *frozen.continuation_ids)
            assert (
                tuple(plan(durable, identity).rendered_text for identity in identities) == expected
            )
            assert frozen.edit_message_id == 444 and frozen.today == saved.today
            assert not frozen.held_change_ids
            assert plan(durable, identities[-1]).held_change_ids == saved.held_change_ids
            assert row(durable, case.included).status is PublicationStatus.SKIPPED
            assert row(durable, case.later).status is PublicationStatus.SKIPPED
            assert publication.id is not None, (
                "every prepared physical part has its durable identity"
            )
            index = identities.index(publication.id)
            if index:
                previous = row(durable, identities[index - 1])
                assert previous.status is PublicationStatus.SENT
                assert plan(durable, publication.id).reply_to == previous.message_id
                assert plan(durable, publication.id).edit_message_id is None
            observed.append(publication.id)
            return prepared(publication)

    monkeypatch.setattr(world.publisher, "publish_prepared", inspect)

    assert update_send(case)

    identities = (case.identity, *plan(world, case.identity).continuation_ids)
    assert observed == list(identities)
    assert world.publisher.edited[-1][1] == 444
    assert world.publisher.sent[-len(expected) + 1].reply_to == 444
    assert row(world, case.included).status is PublicationStatus.SENT
    assert row(world, case.later).status is PublicationStatus.SKIPPED


def test_unrendered_continuation_never_freezes_a_human_preview_error() -> None:
    case = notice_queued(PublicationKind.ACT_PUBLISHED, "on")
    world = case.world
    parent = world.publication("3039")
    assert parent is not None, "the malformed continuation has a delivered parent"
    identity = world.repo.create_publication(
        Publication(
            term=10,
            number="3039",
            kind=PublicationKind.MESSAGE_PART,
            status=PublicationStatus.QUEUED,
            channel_id=CHANNEL,
            created_at=world.clock.now(),
            delivery=DeliveryPlan(
                bill_json=world.bill("3039").model_dump_json(),
                continuation_parent=parent.id,
                continuation_root=parent.id,
                continuation_index=2,
                policy_version="attention-v1",
            ),
        )
    )
    before = world.repo.dump()
    posts = len(world.publisher.sent)

    with pytest.raises(ValueError, match="frozen physical HTML"):
        world.container.poster(dry_run=False).deliver(row(world, identity))

    assert world.repo.dump() == before
    assert len(world.publisher.sent) == posts


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("frozen", [False, True])
def test_notice_edit_flag_conflict_is_rejected_before_any_reader_api(
    kind: PublicationKind, frozen: bool
) -> None:
    case = notice_queued(kind, "on")
    world = case.world
    saved = plan(world, case.identity).model_copy(
        update={"edit_message_id": 444, "rendered_text": "saved notice" if frozen else ""}
    )
    assert world.repo.replace_unattempted_delivery(case.identity, saved)
    world.repo.mark_publication(case.identity, PublicationStatus.FAILED)
    before = world.repo.dump()
    posts, edits = len(world.publisher.sent), len(world.publisher.edited)

    with pytest.raises(ValueError, match="cannot replace"):
        world.container.poster(dry_run=False).deliver(row(world, case.identity))

    assert world.repo.dump() == before
    assert len(world.publisher.sent) == posts and len(world.publisher.edited) == edits
