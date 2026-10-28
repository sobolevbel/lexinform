from dataclasses import dataclass

import pytest
from typer.testing import CliRunner

from lexinform.cli import app
from lexinform.models import (
    DeliveryPlan,
    OutcomeStatus,
    Publication,
    PublicationKind,
    PublicationStatus,
)
from tests.checkpoint_state import StoredState
from tests.harness import CHANNEL, TERM, World, summary
from tests.scenario.test_card_remote_checkpoints import ready_world

ACTIVE = (
    PublicationStatus.QUEUED,
    PublicationStatus.FAILED,
    PublicationStatus.PENDING,
    PublicationStatus.UNKNOWN,
)


@dataclass(frozen=True)
class EditCase:
    world: World
    card: Publication
    root: int
    operation: int


def row(world: StoredState, identity: int) -> Publication:
    publication = world.repo.publication_by_id(identity)
    assert publication is not None, "the fixture retains each saved physical operation"
    return publication


def saved_edit(
    status: PublicationStatus = PublicationStatus.UNKNOWN,
    *,
    legacy: bool = False,
    child: bool = False,
    elsewhere: str = "",
) -> EditCase:
    world = ready_world()
    world.run()
    card = world.publication("3039")
    assert card is not None and card.id is not None and card.message_id is not None, (
        "the production batch fixture publishes its immutable card identity and target"
    )
    channel = "@elsewhere" if elsewhere == "channel" else CHANNEL
    target = card.message_id + 900 if elsewhere == "target" else card.message_id
    root = world.repo.create_publication(
        Publication(
            term=TERM,
            number="3039",
            kind=PublicationKind.MESSAGE_PART,
            channel_id=channel,
            status=PublicationStatus.QUEUED,
            ref=f"card/{card.id}/edition/1" if legacy else "saved-physical-card-edit",
            created_at=world.clock.now(),
            delivery=DeliveryPlan(
                bill_json=world.bill("3039").model_dump_json(),
                rendered_text="<b>Сохранённая редакция карточки: 200 PLN.</b>",
                edit_message_id=target,
                revision_of=None if legacy else card.id,
                reply_to=card.message_id,
                continuation_parent=card.id,
            ),
        )
    )
    operation = root
    if child:
        operation = world.repo.create_publication(
            Publication(
                term=TERM,
                number="3039",
                kind=PublicationKind.MESSAGE_PART,
                channel_id=channel,
                status=PublicationStatus.QUEUED,
                ref=f"card/{card.id}/edition/2",
                created_at=world.clock.now(),
                delivery=DeliveryPlan(
                    bill_json=world.bill("3039").model_dump_json(),
                    rendered_text="<b>Сохранённое продолжение карточки</b>",
                    continuation_root=root,
                    continuation_parent=root,
                    continuation_index=2,
                    reply_to=target,
                ),
            )
        )
        saved = row(world, root)
        assert saved.delivery is not None, "the saved root owns its physical continuations"
        assert world.repo.replace_unattempted_delivery(
            root, saved.delivery.model_copy(update={"continuation_ids": (operation,)})
        )
        world.repo.mark_publication(
            root, PublicationStatus.SENT, message_id=target, count_attempt=False
        )
    world.repo.mark_publication(
        operation,
        status,
        message_id=target if status is PublicationStatus.SENT else None,
        count_attempt=status is PublicationStatus.FAILED,
    )
    return EditCase(world, card, root, operation)


@pytest.mark.parametrize("status", ACTIVE)
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("child", [False, True])
def test_forget_refuses_every_active_saved_edit_or_unfinished_child(
    status: PublicationStatus, legacy: bool, child: bool
) -> None:
    case = saved_edit(status, legacy=legacy, child=child)
    world = case.world
    root, operation = row(world, case.root), row(world, case.operation)
    before = len(world.publisher.sent), len(world.publisher.edited)
    assert case.operation not in (case.card.delivery.continuation_ids if case.card.delivery else ())

    with pytest.raises(ValueError, match=f"card edit operations.*{case.operation}"):
        world.container.publishing_service(dry_run=False).forget_card(world.bill("3039"))

    assert world.publication("3039") == case.card
    assert row(world, case.root) == root and row(world, case.operation) == operation
    assert (len(world.publisher.sent), len(world.publisher.edited)) == before


@pytest.mark.parametrize("command", ["forget", "republish"])
@pytest.mark.parametrize("status", ACTIVE)
@pytest.mark.parametrize("child", [False, True])
def test_operator_cannot_forget_or_republish_while_a_physical_edit_remains_active(
    command: str, status: PublicationStatus, child: bool
) -> None:
    case = saved_edit(status, child=child)
    world = case.world
    root, operation = row(world, case.root), row(world, case.operation)
    before = len(world.publisher.sent), len(world.publisher.edited)
    incoming = world.command(f"/{command} 3039")
    service = world.container.command_service(dry_run=False)
    assert service is not None, "the production fixture has its audited command inbox"

    result = service.handle_pending(min_score=3, run_started_at=world.clock.now())

    assert result.handled == 1 and result.failed == 1
    _, outcome = world.replier.replies[-1]
    assert outcome.status is OutcomeStatus.ERROR
    assert f"card edit operations before forgetting the card: {case.operation}" in outcome.note
    assert world.publication("3039") == case.card
    assert row(world, case.root) == root and row(world, case.operation) == operation
    assert world.inbox.done_ids == [incoming.update_id]
    assert (len(world.publisher.sent), len(world.publisher.edited)) == before


@pytest.mark.parametrize("command", ["forget", "republish"])
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("child", [False, True])
def test_cli_refusal_reports_the_saved_operation_without_deleting_or_posting(
    monkeypatch: pytest.MonkeyPatch, command: str, legacy: bool, child: bool
) -> None:
    case = saved_edit(legacy=legacy, child=child)
    world = case.world
    before = len(world.publisher.sent), len(world.publisher.edited)
    saved = row(world, case.operation)
    monkeypatch.setattr("lexinform.cli._container", lambda: world.container)
    monkeypatch.setattr(world.container, "close", lambda: None)

    result = CliRunner().invoke(app, [command, "3039", "-y"], terminal_width=200)

    assert result.exit_code == 2, result.output
    assert "Resolve unfinished card edit operations" in result.output
    assert str(case.operation) in result.output
    assert "Invalid value" in result.output
    assert isinstance(result.exception, SystemExit)
    assert world.publication("3039") == case.card
    assert row(world, case.operation) == saved
    assert (len(world.publisher.sent), len(world.publisher.edited)) == before


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("child", [False, True])
def test_a_shared_physical_card_alias_keeps_the_original_saved_edit_owner(
    legacy: bool, child: bool
) -> None:
    case = saved_edit(legacy=legacy, child=child)
    world = case.world
    alias = world.repo.upsert_summary(summary("3040", "Projekt ustawy"), now=world.clock.now())
    alias_id = world.repo.create_publication(
        Publication(
            term=TERM,
            number="3040",
            kind=PublicationKind.NEW_BILL,
            channel_id=CHANNEL,
            status=PublicationStatus.SENT,
            message_id=case.card.message_id,
            created_at=world.clock.now(),
        )
    )
    saved_alias = row(world, alias_id)

    with pytest.raises(ValueError, match=f"card edit operations.*{case.operation}"):
        world.container.publishing_service(dry_run=False).forget_card(alias)

    assert row(world, alias_id) == saved_alias
    assert world.publication("3039") == case.card
    assert row(world, case.operation).status is PublicationStatus.UNKNOWN


@pytest.mark.parametrize("status", [PublicationStatus.SENT, PublicationStatus.DISMISSED])
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("child", [False, True])
def test_settled_saved_edits_and_children_allow_forgetting_the_logical_card(
    status: PublicationStatus, legacy: bool, child: bool
) -> None:
    case = saved_edit(status, legacy=legacy, child=child)
    world = case.world
    root, operation = row(world, case.root), row(world, case.operation)
    before = len(world.publisher.sent), len(world.publisher.edited)

    world.container.publishing_service(dry_run=False).forget_card(world.bill("3039"))

    assert world.publication("3039") is None
    assert row(world, case.root) == root and row(world, case.operation) == operation
    assert (len(world.publisher.sent), len(world.publisher.edited)) == before


@pytest.mark.parametrize("elsewhere", ["channel", "target"])
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("child", [False, True])
def test_unrelated_physical_edits_do_not_block_forgetting_the_card(
    elsewhere: str, legacy: bool, child: bool
) -> None:
    case = saved_edit(legacy=legacy, child=child, elsewhere=elsewhere)
    world = case.world
    saved = row(world, case.operation)

    world.container.publishing_service(dry_run=False).forget_card(world.bill("3039"))

    assert world.publication("3039") is None
    assert row(world, case.operation) == saved


def test_original_card_continuations_still_use_the_existing_forget_guard() -> None:
    world = ready_world(long=True)
    world.publisher.fail_continuation_attempt = 1
    world.run()
    card = world.publication("3039")
    assert card is not None and card.delivery is not None, "the fixture sends a multipart card root"
    unfinished = card.delivery.continuation_ids[0]
    assert row(world, unfinished).status is PublicationStatus.FAILED

    with pytest.raises(
        ValueError, match=f"physical parts before forgetting the card: {unfinished}"
    ):
        world.container.publishing_service(dry_run=False).forget_card(world.bill("3039"))

    assert world.publication("3039") == card


def test_forget_reports_all_active_edit_identities_without_dropping_either() -> None:
    case = saved_edit()
    world = case.world
    original = row(world, case.root)
    second = world.repo.create_publication(
        original.model_copy(update={"id": None, "ref": "another-saved-physical-edit"})
    )

    with pytest.raises(ValueError) as failure:
        world.container.publishing_service(dry_run=False).forget_card(world.bill("3039"))

    assert f"{case.operation}, {second}" in str(failure.value)
    assert world.publication("3039") == case.card
    assert row(world, second).status is PublicationStatus.UNKNOWN
