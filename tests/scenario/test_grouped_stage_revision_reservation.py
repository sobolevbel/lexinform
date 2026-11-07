import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from threading import Barrier

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.grouped_update_preview import GroupedUpdateRender
from lexinform.models import (
    ContentVersion,
    DeliveryPlan,
    Publication,
    PublicationKind,
    PublicationStatus,
    UpdateNews,
)
from tests.checkpoint_state import saved_state
from tests.harness import World
from tests.scenario.test_grouped_stage_revision_reader import (
    corrected_render,
    original_fixture,
    revision_fixture,
)


def reserve(
    repo: SqliteBillRepository,
    original: ContentVersion,
    version: ContentVersion,
    candidate: GroupedUpdateRender,
    *,
    approved_sha256: str | None = None,
    base_sha256: str | None = None,
    base_id: str | None = None,
) -> int | None:
    evidence = original.grouped_stage_evidence
    assert evidence is not None and evidence.render_witness is not None, "the base has a witness"
    return repo.reserve_grouped_stage_revision(
        version,
        expected_content_id=base_id or original.content_id,
        expected_base_sha256=base_sha256 or evidence.render_witness.text_sha256,
        approved_sha256=approved_sha256 or candidate.sha256,
    )


def test_approved_reservation_is_inert_and_restores_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, root, original, news = original_fixture(monkeypatch)
    assert root.id is not None and root.message_id is not None, "the original group was sent"
    candidate = corrected_render(world, original, news, days=1)
    version = revision_fixture(original, candidate, target=root.message_id, changed_member=root.id)
    before = world.repo.dump()

    assert reserve(world.repo, original, version, candidate, approved_sha256="0" * 64) is None
    assert reserve(world.repo, original, version, candidate, base_sha256="0" * 64) is None
    assert (
        reserve(world.repo, original, version, candidate, base_id="content-v1:" + "0" * 64) is None
    )
    assert world.repo.dump() == before

    slot = reserve(world.repo, original, version, candidate)

    assert slot is not None, "the confirmed target and approval exactly match"
    assert world.repo.get_publication_content(slot) == version
    assert not world.repo.list_due_deliveries(root.channel_id, max_attempts=3)
    after = world.repo.dump()
    assert reserve(world.repo, original, version, candidate) is None
    assert world.repo.dump() == after
    with saved_state(after) as restored:
        assert restored.repo.get_publication_content(slot) == version
        assert restored.repo.dump() == after

    world.repo.mark_publication(
        slot, PublicationStatus.SENT, message_id=root.message_id, sent_at=world.clock.now()
    )
    confirmed = world.repo.dump()
    other = corrected_render(world, original, news, days=2)
    missing_base = "content-v1:" + "f" * 64
    stale = revision_fixture(
        original, other, target=root.message_id, changed_member=root.id
    ).model_copy(update={"previous_version_id": missing_base})
    assert reserve(world.repo, original, stale, other, base_id=missing_base) is None
    assert world.repo.dump() == confirmed
    current = revision_fixture(version, other, target=root.message_id, changed_member=root.id)
    assert reserve(world.repo, version, current, other) is not None


def sibling_render(
    world: World, original: ContentVersion, news: tuple[UpdateNews, ...]
) -> GroupedUpdateRender:
    evidence = original.grouped_stage_evidence
    assert evidence is not None and evidence.render_witness is not None, "the base has a witness"
    first, second = news
    stages = second.change.new_stages
    when = stages[0].date
    assert when is not None, "the second stage has a date to correct"
    changed = stages[0].model_copy(update={"date": when + timedelta(days=1)})
    event = second.change.model_copy(update={"new_stages": (changed, *stages[1:])})
    render = world.formatter.updates_with_witness(
        (first, second.model_copy(update={"change": event})),
        today=evidence.render_witness.today,
    )
    assert render is not None, "the correction stays in the second member's entry"
    return render


def test_two_connections_competing_for_sibling_edits_reserve_one_slot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    world, root, original, news = original_fixture(monkeypatch)
    evidence = original.grouped_stage_evidence
    assert evidence is not None and root.message_id is not None, "the base is a sent group"
    first = corrected_render(world, original, news, days=1)
    second = sibling_render(world, original, news)
    proposals = tuple(
        revision_fixture(
            original, render, target=root.message_id, changed_member=member.publication_id
        )
        for render, member in zip((first, second), evidence.members, strict=True)
    )
    database = tmp_path / "shared.sqlite"
    with closing(SqliteBillRepository(database)) as repo:
        repo.restore(world.repo.dump())
    barrier = Barrier(2)

    def attempt(version: ContentVersion, render: GroupedUpdateRender) -> int | None:
        with closing(SqliteBillRepository(database)) as repo:
            barrier.wait(timeout=5)
            return reserve(repo, original, version, render)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(attempt, version, render)
            for version, render in zip(proposals, (first, second), strict=True)
        ]
        choices = [future.result(timeout=10) for future in futures]

    (winner,) = [slot for slot in choices if slot is not None]
    with closing(SqliteBillRepository(database)) as repo:
        assert repo.get_publication_content(winner) == proposals[choices.index(winner)]
        assert len(repo.list_content_versions(original.root_publication_id)) == 2
        assert not repo.list_due_deliveries(root.channel_id, max_attempts=3)


@pytest.mark.parametrize("operation", ["save_content_version", "bind_publication_content"])
def test_failed_reservation_rolls_back_slot_sequence_version_and_binding(
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, root, original, news = original_fixture(monkeypatch)
    assert root.id is not None and root.message_id is not None, "the original group was sent"
    candidate = corrected_render(world, original, news, days=1)
    version = revision_fixture(original, candidate, target=root.message_id, changed_member=root.id)
    before = world.repo.dump()

    def reject(*args: object) -> None:
        raise RuntimeError("editorial write failed")

    monkeypatch.setattr(world.repo, operation, reject)
    with pytest.raises(RuntimeError, match="editorial write failed"):
        reserve(world.repo, original, version, candidate)

    assert world.repo.dump() == before


@pytest.mark.parametrize("status", [PublicationStatus.UNKNOWN, PublicationStatus.SENT])
def test_existing_cross_kind_edit_conflicts_even_after_its_payload_expires(
    status: PublicationStatus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, root, original, news = original_fixture(monkeypatch)
    assert root.id is not None and root.message_id is not None, "the original group was sent"
    candidate = corrected_render(world, original, news, days=1)
    version = revision_fixture(original, candidate, target=root.message_id, changed_member=root.id)
    existing = world.repo.create_publication(
        Publication(
            term=root.term,
            number="99999",
            kind=PublicationKind.AMENDMENTS_REVISION,
            status=status,
            channel_id=root.channel_id,
            ref="other-owner",
            created_at=world.clock.now(),
            message_id=root.message_id if status is PublicationStatus.SENT else None,
            sent_at=world.clock.now() if status is PublicationStatus.SENT else None,
            delivery=(
                DeliveryPlan(bill_json="", edit_message_id=root.message_id)
                if status is PublicationStatus.UNKNOWN
                else None
            ),
        )
    )
    before = world.repo.dump()

    assert reserve(world.repo, original, version, candidate) is None

    assert world.repo.dump() == before
    stored = world.repo.publication_by_id(existing)
    assert stored is not None and stored.status is status


@pytest.mark.parametrize("legacy_payload", ['{"edit_message_id":', '["ambiguous"]'])
def test_malformed_open_legacy_payload_fails_closed_without_allocating(
    legacy_payload: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    world, root, original, news = original_fixture(monkeypatch)
    assert root.id is not None and root.message_id is not None, "the original group was sent"
    candidate = corrected_render(world, original, news, days=1)
    version = revision_fixture(original, candidate, target=root.message_id, changed_member=root.id)
    existing = world.repo.create_publication(
        Publication(
            term=root.term,
            number="99999",
            kind=PublicationKind.AMENDMENTS_REVISION,
            status=PublicationStatus.UNKNOWN,
            channel_id=root.channel_id,
            ref="legacy-owner",
            created_at=world.clock.now(),
            delivery=DeliveryPlan(bill_json="", edit_message_id=root.message_id),
        )
    )
    database = tmp_path / "legacy.sqlite"
    with closing(SqliteBillRepository(database)) as repo:
        repo.restore(world.repo.dump())
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.execute(
            "UPDATE publications SET delivery_json=? WHERE id=?", (legacy_payload, existing)
        )
    with closing(SqliteBillRepository(database)) as repo:
        before = repo.dump()

        assert reserve(repo, original, version, candidate) is None

        assert repo.dump() == before
