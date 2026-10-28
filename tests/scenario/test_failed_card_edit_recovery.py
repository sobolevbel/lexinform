from dataclasses import replace
from pathlib import Path
from typing import Never

import pytest

from lexinform.models import (
    DIGEST_NUMBER,
    DIGEST_TERM,
    DeliveryPlan,
    DeliveryResolution,
    OutcomeStatus,
    Publication,
    PublicationKind,
    PublicationStatus,
    StatusChange,
)
from lexinform.ports import PublishResult
from lexinform.services.publications import inherit_card, open_card_edit_ids
from lexinform.services.tracking_result import TrackingResult
from tests.checkpoint_state import saved_state
from tests.harness import CHANNEL, TERM, World, summary
from tests.scenario.test_card_remote_checkpoints import connect, git
from tests.scenario.test_forget_card_edits import EditCase, row, saved_edit
from tests.scenario.test_group_remote_checkpoints import restored


def failed_case(*, attempts: int = 3, legacy: bool = False, child: bool = False) -> EditCase:
    case = saved_edit(PublicationStatus.QUEUED, legacy=legacy, child=child)
    for _ in range(attempts):
        case.world.repo.mark_publication(case.operation, PublicationStatus.FAILED)
    if not attempts:
        case.world.repo.mark_publication(
            case.operation, PublicationStatus.FAILED, count_attempt=False
        )
    return case


def commands(world: World) -> None:
    service = world.container.command_service(dry_run=False)
    assert service is not None, "the production fixture has the audited operator inbox"
    service.handle_pending(min_score=3, run_started_at=world.clock.now())


def refuse_render(*args: object, **kwargs: object) -> Never:
    raise AssertionError("manual recovery must replay the original frozen card edition")


def resolution(case: EditCase, action: str, update_id: int = 500) -> DeliveryResolution:
    incoming = case.world.command(f"/delivery {case.operation} {action}", update_id=update_id)
    operation = row(case.world, case.operation)
    return DeliveryResolution(
        publication_id=case.operation,
        term=operation.term,
        number=operation.number,
        kind=operation.kind,
        channel_id=operation.channel_id,
        action="retry" if action == "retry" else "dismiss",
        command=incoming,
        resolved_at=case.world.clock.now(),
        reason="checked" if action != "retry" else "",
    )


@pytest.mark.parametrize("action", ["retry", "dismiss"])
@pytest.mark.parametrize("attempts", [0, 1, 3])
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("child", [False, True])
def test_operator_can_audit_failed_card_edits_without_a_threshold_or_payload_rewrite(
    action: str, attempts: int, legacy: bool, child: bool
) -> None:
    case = failed_case(attempts=attempts, legacy=legacy, child=child)
    world = case.world
    before = row(world, case.operation)
    root = row(world, case.root)
    incoming = world.command(
        f"/delivery {case.operation} {action}" + (" checked" if action == "dismiss" else "")
    )
    effects = len(world.publisher.sent), len(world.publisher.edited)

    commands(world)

    _, outcome = world.replier.replies[-1]
    assert outcome.status is OutcomeStatus.DELIVERY
    saved = row(world, case.operation)
    assert saved.id == before.id and saved.delivery == before.delivery
    assert saved.status is (
        PublicationStatus.QUEUED if action == "retry" else PublicationStatus.DISMISSED
    )
    assert saved.attempts == (0 if action == "retry" else attempts)
    history = world.repo.delivery_resolutions(case.operation)
    assert len(history) == 1 and history[0].command == incoming and history[0].action == action
    assert world.publication("3039") == case.card
    if child:
        assert row(world, case.root) == root and root.status is PublicationStatus.SENT
    assert (len(world.publisher.sent), len(world.publisher.edited)) == effects


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("child", [False, True])
def test_exhausted_card_retry_has_its_original_payload_target_and_audit_remote_before_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, legacy: bool, child: bool
) -> None:
    case = failed_case(legacy=legacy, child=child)
    world = case.world
    saved = row(world, case.operation)
    assert saved.delivery is not None, "failed physical card operations retain their frozen HTML"
    remote = connect(world, tmp_path)
    incoming = world.command(f"/delivery {case.operation} retry")
    commands(world)
    prepared = world.publisher.publish_prepared
    seen: list[int] = []

    def inspect(publication: Publication) -> PublishResult:
        with saved_state(git(remote, "show", "state:state.sql")) as durable:
            pending = row(durable, case.operation)
            assert pending.status is PublicationStatus.PENDING and pending.attempts == 0
            assert pending.delivery == saved.delivery == publication.delivery
            assert durable.repo.delivery_resolutions(case.operation)[0].command == incoming
            assert durable.publication("3039") == case.card
            seen.append(case.operation)
            return prepared(publication)

    monkeypatch.setattr(world.publisher, "render_delivery", refuse_render)
    monkeypatch.setattr(world.publisher, "publish_prepared", inspect)

    report = world.container.delivery_service(dry_run=False).run()

    assert not report.errors
    assert seen == [case.operation]
    fresh = restored(remote, world, tmp_path)
    assert row(fresh, case.operation).status is PublicationStatus.SENT
    assert row(fresh, case.operation).delivery == saved.delivery
    assert len(fresh.repo.delivery_resolutions(case.operation)) == 1


@pytest.mark.parametrize("child", [False, True])
def test_failed_confirmation_never_certifies_a_card_edit_that_definitely_was_not_delivered(
    child: bool,
) -> None:
    case = failed_case(child=child)
    world = case.world
    before = row(world, case.operation)
    world.command(f"/delivery {case.operation} confirm {888 if child else case.card.message_id}")

    commands(world)

    assert world.replier.replies[-1][1].status is OutcomeStatus.ERROR
    assert row(world, case.operation) == before
    assert not world.repo.delivery_resolutions(case.operation)
    proposed = resolution(case, "dismiss", update_id=501).model_copy(
        update={"action": "confirm", "message_id": 888 if child else case.card.message_id}
    )
    assert not world.repo.resolve_delivery(proposed)


@pytest.mark.parametrize("action", ["retry", "dismiss"])
@pytest.mark.parametrize(
    "invalid",
    [
        "owner",
        "owner_status",
        "target",
        "html",
        "legacy_ref",
        "alias_owner",
        "child_membership",
        "root_status",
    ],
)
def test_service_and_repository_reject_the_same_unqualified_failed_card_operation(
    action: str, invalid: str
) -> None:
    child = invalid in {"child_membership", "root_status"}
    case = saved_edit(PublicationStatus.QUEUED, child=child)
    world = case.world
    root = row(world, case.root)
    assert root.delivery is not None, "the validation fixture starts with a saved card edit"
    if invalid == "child_membership":
        extra = world.repo.create_publication(
            row(world, case.operation).model_copy(update={"id": None, "ref": "unowned-child"})
        )
        case = replace(case, operation=extra)
    elif invalid == "root_status":
        world.repo.mark_publication(case.root, PublicationStatus.UNKNOWN, count_attempt=False)
    elif invalid == "owner_status":
        world.repo.mark_publication(
            case.card.id or 0, PublicationStatus.UNKNOWN, count_attempt=False
        )
    else:
        updates: dict[str, object] = {}
        if invalid == "owner":
            updates["revision_of"] = 999999
        elif invalid == "target":
            updates["edit_message_id"] = (case.card.message_id or 0) + 1
        elif invalid == "html":
            updates["rendered_text"] = ""
        elif invalid == "alias_owner":
            alias = inherit_card(
                world.repo,
                term=TERM,
                number="3040",
                channel_id=CHANNEL,
                card=case.card,
                now=world.clock.now(),
            )
            updates["revision_of"] = alias.id
        if invalid == "legacy_ref":
            replacement = world.repo.create_publication(
                root.model_copy(
                    update={
                        "id": None,
                        "ref": "card/not-a-number/edition/1",
                        "delivery": root.delivery.model_copy(update={"revision_of": None}),
                    }
                )
            )
            case = replace(case, root=replacement, operation=replacement)
        else:
            assert world.repo.replace_unattempted_delivery(
                case.root, root.delivery.model_copy(update=updates)
            )
    for _ in range(3):
        world.repo.mark_publication(case.operation, PublicationStatus.FAILED)
    before = row(world, case.operation)
    world.command(
        f"/delivery {case.operation} {action}" + (" checked" if action == "dismiss" else "")
    )

    commands(world)

    assert world.replier.replies[-1][1].status is OutcomeStatus.ERROR
    assert row(world, case.operation) == before
    assert not world.repo.delivery_resolutions(case.operation)
    assert not world.repo.resolve_delivery(resolution(case, action))
    assert row(world, case.operation) == before


@pytest.mark.parametrize("action", ["retry", "dismiss"])
@pytest.mark.parametrize(
    "kind",
    [
        PublicationKind.NEW_BILL,
        PublicationKind.JOINT_BILL,
        PublicationKind.STATUS_UPDATE,
        PublicationKind.ACT_PUBLISHED,
        PublicationKind.CONSULTATION_REVIEW,
        PublicationKind.COMMISSION_REVIEW,
        PublicationKind.AMENDMENTS_REVISION,
    ],
)
def test_failed_non_card_edit_publications_keep_their_existing_resolution_rules(
    action: str, kind: PublicationKind
) -> None:
    case = failed_case()
    world = case.world
    world.repo.upsert_summary(summary("3040", "Projekt ustawy"), now=world.clock.now())
    change_id = world.repo.add_status_change(
        StatusChange(
            term=TERM,
            number="3040",
            old_fingerprint="old",
            new_fingerprint="new",
            new_stages=[],
            detected_at=world.clock.now(),
        )
    )
    identity = world.repo.create_publication(
        Publication(
            term=TERM,
            number="3040",
            kind=kind,
            channel_id=CHANNEL,
            status=PublicationStatus.FAILED,
            status_change_id=change_id if kind is PublicationKind.STATUS_UPDATE else None,
            ref="ordinary-failed",
            created_at=world.clock.now(),
            delivery=DeliveryPlan(
                bill_json=world.bill("3039").model_dump_json(),
                rendered_text="Saved ordinary text",
                reply_to=case.card.message_id,
            ),
        )
    )
    before = row(world, identity)
    world.command(f"/delivery {identity} {action}" + (" checked" if action == "dismiss" else ""))

    commands(world)

    assert world.replier.replies[-1][1].status is OutcomeStatus.ERROR
    assert row(world, identity) == before
    rejected = replace(case, operation=identity)
    assert not world.repo.resolve_delivery(resolution(rejected, action))
    assert not world.repo.delivery_resolutions(identity)


@pytest.mark.parametrize("child", [False, True])
def test_a_physical_alias_does_not_change_the_saved_logical_owner_during_failed_retry(
    child: bool,
) -> None:
    case = failed_case(child=child)
    world = case.world
    alias = inherit_card(
        world.repo,
        term=TERM,
        number="3040",
        channel_id=CHANNEL,
        card=case.card,
        now=world.clock.now(),
    )
    saved = row(world, case.operation)
    world.command(f"/delivery {case.operation} retry")

    commands(world)

    assert world.replier.replies[-1][1].status is OutcomeStatus.DELIVERY
    assert row(world, case.operation).delivery == saved.delivery
    assert world.publication("3040") == alias and world.publication("3039") == case.card


def test_a_failed_card_dismissal_releases_the_reader_barrier_without_marking_the_card_sent_again() -> (
    None
):
    case = failed_case()
    world = case.world
    world.command(f"/delivery {case.operation} dismiss checked")

    commands(world)

    assert row(world, case.operation).status is PublicationStatus.DISMISSED
    assert world.publication("3039") == case.card
    assert not open_card_edit_ids(world.repo, case.card)


def test_one_frozen_root_does_not_turn_its_already_sent_snapshot_child_into_a_failure(
    tmp_path: Path,
) -> None:
    case = saved_edit(PublicationStatus.QUEUED, child=True)
    world = case.world
    world.repo.mark_publication(case.root, PublicationStatus.QUEUED, count_attempt=False)
    world.repo.set_card_digest(case.root, "d" * 64)
    remote = connect(world, tmp_path)
    before = len(world.publisher.sent), len(world.publisher.edited)

    service = world.container.delivery_service(dry_run=False)
    tracked = TrackingResult()
    service.retry(tracked)
    assert tracked.failed == 0
    report = service.run()

    assert not report.errors
    assert len(world.publisher.sent) == before[0] + 1
    assert len(world.publisher.edited) == before[1] + 1
    fresh = restored(remote, world, tmp_path)
    assert row(fresh, case.root).status is PublicationStatus.SENT
    assert row(fresh, case.operation).status is PublicationStatus.SENT
    assert fresh.publication("3039") is not None
    assert row(fresh, case.card.id or 0).rendered_sha256 == "d" * 64


@pytest.mark.parametrize(
    ("action", "attempts", "allowed"),
    [("retry", 1, False), ("retry", 3, True), ("dismiss", 3, False), ("confirm", 3, False)],
)
def test_failed_digest_resolution_keeps_its_prior_retry_only_threshold(
    action: str, attempts: int, allowed: bool
) -> None:
    case = failed_case()
    world = case.world
    identity = world.repo.create_publication(
        Publication(
            term=DIGEST_TERM,
            number=DIGEST_NUMBER,
            kind=PublicationKind.DIGEST,
            channel_id=CHANNEL,
            status=PublicationStatus.QUEUED,
            ref="2026-W35",
            created_at=world.clock.now(),
            delivery=DeliveryPlan(bill_json="", rendered_text="Saved digest"),
        )
    )
    for _ in range(attempts):
        world.repo.mark_publication(identity, PublicationStatus.FAILED)
    before = row(world, identity)
    suffix = " checked" if action == "dismiss" else " 888" if action == "confirm" else ""
    world.command(f"/delivery {identity} {action}{suffix}")

    commands(world)

    assert (world.replier.replies[-1][1].status is OutcomeStatus.DELIVERY) is allowed
    if allowed:
        assert row(world, identity).status is PublicationStatus.QUEUED
        assert row(world, identity).attempts == 0
    else:
        assert row(world, identity) == before
        assert not world.repo.delivery_resolutions(identity)
