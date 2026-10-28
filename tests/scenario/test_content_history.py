import json
import sqlite3
from contextlib import closing

import pytest

from lexinform.models import (
    Bill,
    ContentSnapshot,
    DigestItemMeta,
    OutcomeStatus,
    PublicationStatus,
    ReadyAnalysis,
    StatusChange,
)
from lexinform.models.bill import LocatedText
from lexinform.services.content_history import FORMATTER_VERSION, restore_content_delivery
from tests.scenario.test_amendments_command import published_world
from tests.state_snapshots import dump_at_version


def test_legacy_unfinished_input_is_not_duplicated_in_editorial_history() -> None:
    world, original = published_world()
    assert original.id is not None and original.delivery is not None, (
        "published_world sends the frozen original"
    )
    bill = Bill.model_validate_json(original.delivery.bill_json)
    assert bill.analysis is not None, "the fixture has the original published analysis"
    marker = "UNAPPLIED-INPUT-MUST-STAY-WITH-ITS-OWNER" * 10000
    bill.ready_analysis = ReadyAnalysis(record=bill.analysis, located=LocatedText(), text=marker)
    plan = original.delivery.model_copy(update={"bill_json": bill.model_dump_json()})
    with closing(sqlite3.connect(":memory:")) as legacy:
        legacy.executescript(world.repo.dump())
        legacy.execute(
            "UPDATE publications SET delivery_json=? WHERE id=?",
            (plan.model_dump_json(), original.id),
        )
        dump = dump_at_version(legacy, version=54)
    world.repo.restore(dump)

    world.command("/amendments 1929")
    world.run(track=False)

    content = world.repo.get_publication_content(original.id)
    assert content is not None, "the caller records the original content before its effect"
    snapshot = world.repo.get_content_snapshot(content.bill_snapshot_id)
    assert snapshot is not None and marker not in snapshot.payload_json, (
        "content history retains its semantic view without unfinished input"
    )
    assert "ready_analysis" not in json.loads(snapshot.payload_json)
    assert len(world.batch.submitted) == 1
    assert marker not in world.batch.submitted[0].ctx.model_dump_json()
    batch = world.repo.list_open_llm_batches()[0]
    item = world.repo.list_llm_batch_items(batch.batch_id)[0]
    assert isinstance(item.meta, DigestItemMeta) and item.meta.amendment_revision is not None, (
        "amendment revisions keep their immutable delivery target in the batch metadata"
    )
    assert marker not in item.meta.amendment_revision.delivery.bill_json
    assert item.meta.amendment_revision.delivery.bill_json == snapshot.payload_json
    saved = world.repo.publication_by_id(original.id)
    assert saved is not None and saved.delivery is not None, (
        "the original operational payload remains intact"
    )
    assert marker in saved.delivery.bill_json
    assert world.repo.get_publication_content(original.id) == content


@pytest.mark.parametrize("workers", [1, 4])
def test_single_update_captures_original_and_can_be_revised_after_retention(workers: int) -> None:
    world, original = published_world(workers=workers)
    assert original.id is not None and original.delivery is not None, (
        "published_world sends the frozen original"
    )
    content = world.repo.get_publication_content(original.id)
    assert content is not None, "the caller records the original content before its effect"
    assert content.rendered_text == original.delivery.rendered_text
    assert content.formatter_version == FORMATTER_VERSION
    assert restore_content_delivery(world.repo, content) == original.delivery
    baseline_sql = world.repo.dump()
    world.repo.restore(baseline_sql)
    world.clock.advance(days=181)
    world.repo.prune_history(before=world.clock.now(), memo_before=world.clock.now())
    pruned = world.repo.publication_by_id(original.id)
    assert pruned is not None and pruned.delivery is None, (
        "retention clears payloads while preserving publication identities"
    )

    world.command(f"/amendments 1929 publication={original.id}")
    world.run(track=False)
    assert world.replier.replies[-1][1].status is OutcomeStatus.QUEUED
    world.batch.resolve()
    world.run()

    revised = world.repo.list_amendment_revisions(original.id)[0]
    revision = world.repo.get_publication_content(revised.id or 0)
    assert revision is not None and revised.status is PublicationStatus.SENT, (
        "collection and posting save the revision and its confirmed content"
    )
    assert revision.previous_version_id == content.content_id
    assert revision.bill_snapshot_id == content.bill_snapshot_id
    assert revision.change_snapshot_id != content.change_snapshot_id
    assert revised.delivery is not None, "a sent amendment revision retains its frozen edit plan"
    assert revised.delivery.today == original.delivery.today
    assert revised.delivery.bill_json == original.delivery.bill_json
    assert world.repo.get_publication_content(original.id) == content
    assert world.repo.get_content_snapshot(content.bill_snapshot_id) == ContentSnapshot(
        kind="bill", payload_json=original.delivery.bill_json
    )
    saved = world.repo.dump()
    world.run(track=False)
    assert world.repo.get_publication_content(revised.id or 0) == revision
    world.repo.restore(saved)
    assert world.repo.get_content_version(revision.content_id) == revision


def test_uncertain_revision_is_history_without_confirmation_or_a_second_model_call() -> None:
    world, original = published_world()
    assert original.id is not None, "published_world saves the original publication identity"
    world.command("/amendments 1929")
    world.run(track=False)
    world.batch.resolve()
    world.publisher.unknown_on_edit.add("1929")
    world.run()
    revisions = world.repo.list_amendment_revisions(original.id)
    assert len(revisions) == 1 and revisions[0].status is PublicationStatus.UNKNOWN
    content = world.repo.get_publication_content(revisions[0].id or 0)
    assert content is not None, "the caller records the original content before its effect"
    submitted = len(world.batch.submitted)

    world.command("/amendments 1929 force")
    world.run(track=False)

    assert len(world.batch.submitted) == submitted
    assert world.repo.get_publication_content(revisions[0].id or 0) == content
    assert world.repo.list_amendment_revisions(original.id)[0].status is PublicationStatus.UNKNOWN


def test_successive_revisions_link_confirmed_editions_and_reuse_the_original_bill() -> None:
    world, original = published_world()
    assert original.id is not None and original.delivery is not None, (
        "published_world sends the frozen original"
    )
    for command in ("/amendments 1929", "/amendments 1929 force"):
        world.command(command)
        world.run(track=False)
        world.batch.resolve()
        world.run()
    versions = world.repo.list_content_versions(original.id)

    assert len(versions) == 3
    assert versions[1].previous_version_id == versions[0].content_id
    assert versions[2].previous_version_id == versions[1].content_id
    assert len({version.bill_snapshot_id for version in versions}) == 1
    snapshot = world.repo.get_content_snapshot(versions[0].bill_snapshot_id)
    assert snapshot is not None, "content versions require their immutable semantic snapshots"
    assert Bill.model_validate_json(snapshot.payload_json) == Bill.model_validate_json(
        original.delivery.bill_json
    )
    change = world.repo.get_content_snapshot(versions[-1].change_snapshot_id)
    assert (
        change is not None
        and StatusChange.model_validate_json(change.payload_json).amendments is not None
    )
