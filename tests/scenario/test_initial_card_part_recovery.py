from pathlib import Path

import pytest

from lexinform.errors import TelegramUnavailableError
from lexinform.models import (
    OutcomeStatus,
    Publication,
    PublicationKind,
    PublicationStatus,
    failed_card_part_resolution_allowed,
)
from lexinform.ports import PublishResult
from tests.harness import World
from tests.scenario.test_card_remote_checkpoints import Mode, connect, git, ready_world, restored


def row(world: World, identity: int) -> Publication:
    saved = world.repo.publication_by_id(identity)
    assert saved is not None, "the production fixture retains each physical identity"
    return saved


def failed_card(
    kind: PublicationKind, mode: Mode, attempts: int
) -> tuple[World, Publication, Publication]:
    world = ready_world(mode, kind, long=True)
    original = world.publisher.publish_prepared

    def refuse(publication: Publication) -> PublishResult:
        if publication.number == "3039" and publication.kind is PublicationKind.MESSAGE_PART:
            if not attempts:
                raise TelegramUnavailableError("synthetic child outage")
            raise RuntimeError("synthetic definite child refusal")
        return original(publication)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(world.publisher, "publish_prepared", refuse)
        for _ in range(attempts or 1):
            world.run(discover=False, analyze=False)
    root = world.publication("3039", kind)
    assert root is not None and root.id is not None and root.delivery is not None, (
        "the production fixture stores its sent root with a frozen plan"
    )
    assert root.status is PublicationStatus.SENT and root.delivery.continuation_ids
    child = row(world, root.delivery.continuation_ids[0])
    assert child.status is PublicationStatus.FAILED and child.attempts == attempts
    return world, root, child


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("kind", [PublicationKind.NEW_BILL, PublicationKind.JOINT_BILL])
@pytest.mark.parametrize("attempts", [0, 1, 3])
@pytest.mark.parametrize("action", ["retry", "dismiss"])
def test_failed_initial_card_part_has_an_audited_recovery_without_forgetting_its_sent_root(
    mode: Mode, kind: PublicationKind, attempts: int, action: str
) -> None:
    world, root, child = failed_card(kind, mode, attempts)
    before = len(world.publisher.sent), len(world.publisher.edited)
    incoming = world.command(
        f"/delivery {child.id} {action}" + (" checked absent" if action == "dismiss" else "")
    )
    commands = world.container.command_service(dry_run=False)
    assert commands is not None, "the production fixture enables the audited operator inbox"

    result = commands.handle_pending(min_score=3, run_started_at=world.clock.now())

    assert not result.failed
    assert world.replier.replies[-1][1].status is OutcomeStatus.DELIVERY
    saved = row(world, child.id or 0)
    assert saved.delivery == child.delivery
    assert saved.status is (
        PublicationStatus.QUEUED if action == "retry" else PublicationStatus.DISMISSED
    )
    assert saved.attempts == (0 if action == "retry" else attempts)
    assert row(world, root.id or 0) == root
    assert (len(world.publisher.sent), len(world.publisher.edited)) == before
    history = world.repo.delivery_resolutions(child.id or 0)
    assert len(history) == 1 and history[0].command == incoming
    if action == "dismiss":
        assert root.delivery is not None, "the sent root retains physical membership"
        assert all(
            row(world, identity).status is PublicationStatus.DISMISSED
            for identity in root.delivery.continuation_ids
        )


@pytest.mark.parametrize("kind", [PublicationKind.NEW_BILL, PublicationKind.JOINT_BILL])
def test_initial_card_retry_saves_its_audit_before_replaying_exact_parts_after_fresh_clone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: PublicationKind
) -> None:
    world, root, child = failed_card(kind, "on", 3)
    remote = connect(world, tmp_path)
    incoming = world.command(f"/delivery {child.id} retry")
    commands = world.container.command_service(dry_run=False)
    assert commands is not None, "the fixture enables audited recovery commands"
    commands.handle_pending(min_score=3, run_started_at=world.clock.now())
    fresh = restored(remote, world, tmp_path)
    fresh.clock.advance(days=120)
    original = fresh.publisher.publish_prepared
    seen: list[int] = []

    def inspect(publication: Publication) -> PublishResult:
        durable = World()
        durable.repo.restore(git(remote, "show", "state:state.sql"))
        pending = row(durable, publication.id or 0)
        assert pending.status is PublicationStatus.PENDING
        assert publication.delivery == pending.delivery
        assert row(durable, root.id or 0) == root
        assert durable.repo.delivery_resolutions(child.id or 0)[0].command == incoming
        assert publication.id is not None, "each physical continuation is saved before API"
        seen.append(publication.id)
        return original(publication)

    monkeypatch.setattr(fresh.publisher, "publish_prepared", inspect)

    result = fresh.container.delivery_service(dry_run=False).run()

    assert not result.errors
    assert root.delivery is not None, "the sent root has its original frozen membership"
    assert seen == list(root.delivery.continuation_ids)
    assert all(row(fresh, identity).status is PublicationStatus.SENT for identity in seen)
    assert row(fresh, root.id or 0) == root
    assert len(fresh.repo.delivery_resolutions(child.id or 0)) == 1


@pytest.mark.parametrize(
    "invalid",
    [
        "kind",
        "status",
        "channel",
        "term",
        "number",
        "no_html",
        "edit_target",
        "root",
        "parent",
        "index",
        "membership",
        "root_status",
        "root_html",
        "root_edit",
        "root_parent",
        "root_revision",
        "confirm",
    ],
)
def test_unqualified_failed_initial_card_parts_cannot_be_recovered(invalid: str) -> None:
    _, root, child = failed_card(PublicationKind.NEW_BILL, "on", 3)
    root_plan, child_plan = root.delivery, child.delivery
    assert root_plan is not None and child_plan is not None, (
        "the fixture retains exact physical plans"
    )
    action = "retry"
    if invalid == "kind":
        child = child.model_copy(update={"kind": PublicationKind.STATUS_UPDATE})
    elif invalid == "status":
        child = child.model_copy(update={"status": PublicationStatus.SENT})
    elif invalid in ("channel", "number"):
        child = child.model_copy(
            update={"channel_id" if invalid == "channel" else "number": "unrelated"}
        )
    elif invalid == "term":
        child = child.model_copy(update={"term": child.term + 1})
    elif invalid in ("no_html", "edit_target", "root", "parent", "index"):
        key, value = {
            "no_html": ("rendered_text", ""),
            "edit_target": ("edit_message_id", 900000),
            "root": ("continuation_root", 900000),
            "parent": ("continuation_parent", 900000),
            "index": ("continuation_index", 900000),
        }[invalid]
        child = child.model_copy(update={"delivery": child_plan.model_copy(update={key: value})})
    elif invalid == "root_status":
        root = root.model_copy(update={"status": PublicationStatus.UNKNOWN})
    elif invalid == "confirm":
        action = "confirm"
    else:
        key, value = {
            "membership": ("continuation_ids", (900000,)),
            "root_html": ("rendered_text", ""),
            "root_edit": ("edit_message_id", 900000),
            "root_parent": ("continuation_root", 900000),
            "root_revision": ("revision_of", 900000),
        }[invalid]
        root = root.model_copy(update={"delivery": root_plan.model_copy(update={key: value})})

    assert not failed_card_part_resolution_allowed(child, root, None, action)
