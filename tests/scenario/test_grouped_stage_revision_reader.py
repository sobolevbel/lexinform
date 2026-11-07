import sqlite3
from collections.abc import Sequence
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

import pytest
from tools.content_history_doctor import audit

from lexinform.content_versions import grouped_stage_revision_plan, grouped_stage_revision_ref
from lexinform.grouped_update_preview import GroupedUpdateRender
from lexinform.models import (
    ContentVersion,
    DeliveryResolution,
    Publication,
    PublicationKind,
    PublicationStatus,
    UpdateNews,
)
from lexinform.models.content import GroupedStageRevisionEvidence
from lexinform.models.grouped_update_witness import GroupedRenderWitness
from lexinform.services.content_inspection import inspect_content
from lexinform.services.grouped_update_preview import preview_retained_grouped_update
from tests.checkpoint_state import saved_state
from tests.harness import World
from tests.scenario.test_commands import _commands_only
from tests.scenario.test_group_remote_checkpoints import flush, queued_world, rows


def original_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[World, Publication, ContentVersion, tuple[UpdateNews, ...]]:
    world, ids = queued_world("updates")
    original_render = world.publisher.render_updates
    captured: list[tuple[UpdateNews, ...]] = []

    def observe(news: Sequence[UpdateNews], *, today: date) -> str:
        captured.append(tuple(news))
        return original_render(news, today=today)

    monkeypatch.setattr(world.publisher, "render_updates", observe)
    flush(world, "updates", ids)
    root, _ = rows(world, ids)
    original = world.repo.get_publication_content(ids[0])
    assert original is not None and original.grouped_stage_evidence is not None, (
        "the same render froze a grouped original and its witness"
    )
    (news,) = captured
    return world, root, original, news


def corrected_render(
    world: World, original: ContentVersion, news: tuple[UpdateNews, ...], *, days: int
) -> GroupedUpdateRender:
    evidence = original.grouped_stage_evidence
    assert evidence is not None and evidence.render_witness is not None, (
        "the original has a witness"
    )
    first, second = news
    stages = first.change.new_stages
    old_date = stages[0].date
    assert old_date is not None, "the fixture retains the first member's dated stage"
    corrected = stages[0].model_copy(update={"date": old_date + timedelta(days=days)})
    change = first.change.model_copy(update={"new_stages": (corrected, *stages[1:])})
    render = world.formatter.updates_with_witness(
        (first.model_copy(update={"change": change}), second), today=evidence.render_witness.today
    )
    assert render is not None, "the explicitly corrected candidate keeps the original shape"
    return render


def revision_fixture(
    parent: ContentVersion,
    render: GroupedUpdateRender,
    *,
    target: int,
    changed_member: int,
) -> ContentVersion:
    evidence = parent.grouped_stage_evidence
    assert evidence is not None, "the parent retains its ordered group"
    return ContentVersion.model_validate(
        parent.model_dump()
        | {
            "reason": "grouped_stage_revision",
            "previous_version_id": parent.content_id,
            "rendered_text": render.text,
            "grouped_stage_evidence": evidence.model_copy(
                update={
                    "render_witness": GroupedRenderWitness.from_render(render),
                    "revision": GroupedStageRevisionEvidence(
                        member_publication_id=changed_member, target_message_id=target
                    ),
                }
            ).model_dump(),
        }
    )


def save_slot(
    world: World, root: Publication, version: ContentVersion, *, wrong_owner: bool = False
) -> int:
    bill = world.repo.get_content_snapshot(version.bill_snapshot_id)
    assert bill is not None, "the root compact bill is retained"
    plan = grouped_stage_revision_plan(version, bill)
    assert plan is not None, "the fixed one-slot format has its exact delivery plan"
    with world.repo.atomic():
        identity = world.repo.create_publication(
            Publication(
                term=root.term,
                number="99999" if wrong_owner else root.number,
                channel_id=root.channel_id,
                kind=PublicationKind.MESSAGE_PART,
                status=PublicationStatus.QUEUED,
                ref=grouped_stage_revision_ref(version),
                created_at=world.clock.now(),
                delivery=plan,
            )
        )
        world.repo.save_content_version(version)
        world.repo.bind_publication_content(identity, version.content_id)
    return identity


def test_grouped_revision_reader_tracks_exact_single_slot_and_hides_uncertain_current(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    world, root, original, news = original_fixture(monkeypatch)
    assert root.id is not None and root.message_id is not None, "the grouped root is confirmed"
    rendered = corrected_render(world, original, news, days=1)
    version = revision_fixture(original, rendered, target=root.message_id, changed_member=root.id)

    slot = save_slot(world, root, version)

    history = inspect_content(world.repo, root)
    assert history is not None and history.current_id == original.content_id
    world.repo.mark_publication(slot, PublicationStatus.PENDING, count_attempt=False)
    history = inspect_content(world.repo, root)
    assert history is not None and history.current_id is None
    world.repo.mark_publication(slot, PublicationStatus.UNKNOWN, count_attempt=False)
    child = revision_fixture(
        version,
        corrected_render(world, original, news, days=2),
        target=root.message_id,
        changed_member=root.id,
    )
    with pytest.raises(ValueError, match="grouped_revision_stale_parent"):
        save_slot(world, root, child)
    unknown = world.repo.dump()
    with saved_state(unknown) as restored:
        assert restored.repo.get_publication_content(slot) == version
        history = inspect_content(restored.repo, root)
        assert history is not None and history.integrity == "valid" and history.current_id is None
        assert restored.repo.dump() == unknown
    database = tmp_path / "unknown.db"
    with closing(sqlite3.connect(database)) as conn:
        conn.executescript(unknown)
    report = audit(database, publication_id=root.id)
    assert report["status"] == "recorded"
    assert report["histories"][0]["current_content_id"] is None

    world.repo.mark_publication(
        slot, PublicationStatus.SENT, message_id=root.message_id, sent_at=world.clock.now()
    )
    expiry = world.clock.now() + timedelta(days=1)
    world.repo.prune_history(before=expiry, memo_before=expiry)
    exact = dump = world.repo.dump()
    for _ in range(2):
        with saved_state(dump) as restored:
            history = inspect_content(restored.repo, root)
            assert history is not None and history.current_id == version.content_id
            assert restored.repo.get_publication_content(slot) == version
            assert (
                preview_retained_grouped_update(
                    restored.repo,
                    root.id,
                    corrected_render(world, original, news, days=2),
                    owner=rendered.members[0],
                    expected_content_id=original.content_id,
                    expected_sha256=rendered.sha256,
                )
                is None
            )
            next_render = corrected_render(world, original, news, days=2)
            preview = preview_retained_grouped_update(
                restored.repo,
                root.id,
                next_render,
                owner=rendered.members[0],
                expected_content_id=version.content_id,
                expected_sha256=rendered.sha256,
            )
            assert preview is not None and preview.html == next_render.text
            dump = restored.repo.dump()
            assert dump == exact


@pytest.mark.parametrize(
    ("failure", "error"),
    [
        ("member", "grouped_revision_member_patch"),
        ("target", "grouped_revision_parent_scope"),
        ("slot_owner", "grouped_revision_slot_owner"),
    ],
)
def test_grouped_revision_reader_rejects_mismatched_owner_or_target_atomically(
    failure: str,
    error: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, root, original, news = original_fixture(monkeypatch)
    evidence = original.grouped_stage_evidence
    assert evidence is not None and root.id is not None and root.message_id is not None, (
        "the fixture contains exact sent group and card identities"
    )
    version = revision_fixture(
        original,
        corrected_render(world, original, news, days=1),
        target=root.message_id + int(failure == "target"),
        changed_member=evidence.members[1].publication_id if failure == "member" else root.id,
    )
    before = world.repo.dump()

    with pytest.raises(ValueError, match=error):
        save_slot(world, root, version, wrong_owner=failure == "slot_owner")

    assert world.repo.dump() == before


@pytest.mark.parametrize(
    "status",
    [
        PublicationStatus.QUEUED,
        PublicationStatus.FAILED,
        PublicationStatus.PENDING,
        PublicationStatus.UNKNOWN,
    ],
)
def test_reader_only_slot_is_excluded_from_sender_and_operator_resolution(
    status: PublicationStatus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, root, original, news = original_fixture(monkeypatch)
    assert root.id is not None and root.message_id is not None, "the group is confirmed"
    version = revision_fixture(
        original,
        corrected_render(world, original, news, days=1),
        target=root.message_id,
        changed_member=root.id,
    )
    identity = save_slot(world, root, version)
    world.repo.mark_publication(identity, status, count_attempt=False)
    (slot,) = rows(world, (identity,))
    sent, edited = len(world.publisher.sent), len(world.publisher.edited)

    assert identity not in {
        item.id for item in world.repo.list_due_deliveries(root.channel_id, max_attempts=3)
    }
    assert identity in {item.id for item in world.repo.list_open_deliveries(root.channel_id)}
    assert not world.container.poster(dry_run=False).deliver(slot)
    if status is PublicationStatus.UNKNOWN:
        incoming = world.command(f"/delivery {identity} confirm {root.message_id}")
        assert not world.repo.resolve_delivery(
            DeliveryResolution(
                publication_id=identity,
                term=slot.term,
                number=slot.number,
                kind=slot.kind,
                channel_id=slot.channel_id,
                action="confirm",
                command=incoming,
                resolved_at=world.clock.now(),
                message_id=root.message_id,
            )
        )
        world.command(f"/delivery {identity} retry")
        _commands_only(world)
        assert not world.repo.delivery_resolutions(identity)
    assert world.repo.publication_by_id(identity) == slot
    assert (len(world.publisher.sent), len(world.publisher.edited)) == (sent, edited)
