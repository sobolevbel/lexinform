import datetime as dt
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    ContentSnapshot,
    ContentVersion,
    DeliveryPlan,
    Publication,
    PublicationKind,
    PublicationStatus,
)
from lexinform.models.content import editorial_bill_snapshot

NOW = dt.datetime(2026, 10, 27, tzinfo=dt.UTC)


def test_editorial_bill_preserves_frozen_facts_without_copying_unfinished_work() -> None:
    facts = {"summary": {"title": "original"}, "unknown_future_field": "preserve"}
    raw = json.dumps(
        facts
        | {
            "review_work": [{"context": "BODY" * 100000}],
            "ready_analysis": {"text": "BODY" * 100000},
        }
    )

    snapshot = editorial_bill_snapshot(raw)

    assert json.loads(snapshot.payload_json) == facts
    assert len(snapshot.payload_json) < 100
    assert editorial_bill_snapshot(snapshot.payload_json) == snapshot
    unchanged = '{"summary": {"title":"original"}, "review_work": []}'
    assert editorial_bill_snapshot(unchanged).payload_json == unchanged


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[SqliteBillRepository]:
    repository = SqliteBillRepository(tmp_path / "content.db")
    repository.migrate()
    yield repository
    repository.close()


def snapshots(
    *, root: int | None = None
) -> tuple[ContentSnapshot, ContentSnapshot, ContentSnapshot]:
    return (
        ContentSnapshot(kind="bill", payload_json='{"historical":"bill"}'),
        ContentSnapshot(kind="change", payload_json='{"historical":"change"}'),
        ContentSnapshot(
            kind="plan",
            payload_json=DeliveryPlan(
                bill_json="",
                today=dt.date(2026, 9, 1),
                revision_of=root,
                edit_message_id=10 if root else None,
            ).model_dump_json(),
        ),
    )


def version(
    *, root: int = 1, previous: str | None = None, text: str = "original"
) -> ContentVersion:
    bill, change, plan = snapshots(root=root if previous else None)
    return ContentVersion(
        root_publication_id=root,
        previous_version_id=previous,
        reason="amendments_revision" if previous else "original",
        language="ru",
        bill_snapshot_id=bill.snapshot_id,
        change_snapshot_id=change.snapshot_id,
        plan_snapshot_id=plan.snapshot_id,
        rendered_text=text,
        recorded_at=NOW,
    )


def record(repo: SqliteBillRepository, value: ContentVersion) -> None:
    for snapshot in snapshots(
        root=value.root_publication_id if value.previous_version_id else None
    ):
        repo.save_content_snapshot(snapshot)
    repo.save_content_version(value)


def publication(
    repo: SqliteBillRepository, *, original: int | None = None, text: str = "original"
) -> int:
    return repo.create_publication(
        Publication(
            term=10,
            number="1",
            channel_id="chan",
            created_at=NOW,
            kind=PublicationKind.AMENDMENTS_REVISION if original else PublicationKind.NEW_BILL,
            ref="revision" if original else None,
            status=PublicationStatus.SENT,
            sent_at=NOW,
            message_id=10,
            delivery=DeliveryPlan(
                bill_json=snapshots()[0].payload_json,
                change_json=snapshots()[1].payload_json,
                today=dt.date(2026, 9, 1),
                rendered_text=text,
                revision_of=original,
                edit_message_id=10 if original else None,
            ),
        )
    )


def test_exact_snapshot_identity_preserves_unknown_json_and_does_not_normalize_history() -> None:
    first = ContentSnapshot(kind="bill", payload_json='{"unknown": 1}')
    differently_serialized = ContentSnapshot(kind="bill", payload_json='{"unknown":1}')

    assert first.snapshot_id != differently_serialized.snapshot_id
    assert first.snapshot_id != first.model_copy(update={"kind": "change"}).snapshot_id
    assert first.payload_json == '{"unknown": 1}'


@pytest.mark.parametrize("payload", ["bad", "[]", "null", "1"])
def test_snapshots_require_json_objects(payload: str) -> None:
    with pytest.raises(ValueError):
        ContentSnapshot(kind="bill", payload_json=payload)


def test_version_identity_excludes_first_recording_time_and_requires_a_predecessor() -> None:
    first = version()
    assert (
        first.content_id
        == first.model_copy(update={"recorded_at": NOW + dt.timedelta(days=1)}).content_id
    )
    with pytest.raises(ValidationError):
        ContentVersion.model_validate(first.model_dump() | {"reason": "amendments_revision"})
    with pytest.raises(ValidationError):
        ContentVersion.model_validate(
            first.model_dump() | {"recorded_at": NOW.replace(tzinfo=None)}
        )


def test_history_deduplicates_snapshots_and_keeps_first_recording_time(
    repo: SqliteBillRepository,
) -> None:
    first = version()
    record(repo, first)
    before = repo.dump()

    record(repo, first.model_copy(update={"recorded_at": NOW + dt.timedelta(days=1)}))

    assert repo.dump() == before
    assert repo.get_content_version(first.content_id) == first
    assert repo.get_content_snapshot(snapshots()[0].snapshot_id) == snapshots()[0]
    assert repo.get_content_snapshot("absent") is None
    assert repo.get_content_version("absent") is None


@pytest.mark.parametrize("damage", ["missing", "wrong_kind", "missing_predecessor", "other_root"])
def test_version_requires_its_exact_snapshots_and_own_history(
    repo: SqliteBillRepository, damage: str
) -> None:
    first = version()
    record(repo, first)
    changed = version(previous=first.content_id, text="revision")
    if damage == "missing":
        changed = changed.model_copy(update={"bill_snapshot_id": "content-snapshot-v1:" + "f" * 64})
    elif damage == "wrong_kind":
        changed = changed.model_copy(update={"bill_snapshot_id": first.change_snapshot_id})
    elif damage == "missing_predecessor":
        changed = changed.model_copy(update={"previous_version_id": "content-v1:" + "f" * 64})
    else:
        changed = changed.model_copy(update={"root_publication_id": 2})
    before = repo.dump()

    with pytest.raises(ValueError):
        repo.save_content_version(changed)

    assert repo.dump() == before


def test_content_and_binding_survive_restore_retention_and_explicit_publication_deletion(
    repo: SqliteBillRepository,
) -> None:
    root = publication(repo)
    first = version(root=root)
    record(repo, first)
    repo.bind_publication_content(root, first.content_id)
    revision_id = publication(repo, original=root, text="revision")
    revision = version(root=root, previous=first.content_id, text="revision")
    record(repo, revision)
    repo.bind_publication_content(revision_id, revision.content_id)
    before = repo.dump()

    repo.restore(before)
    assert repo.dump() == before
    repo.prune_history(
        before=NOW + dt.timedelta(days=181), memo_before=NOW + dt.timedelta(days=181)
    )

    assert repo.get_publication_content(root) == first
    assert repo.get_publication_content(revision_id) == revision
    assert repo.list_content_versions(root) == [first, revision]
    assert repo.delete_publication(10, "1", PublicationKind.NEW_BILL, "chan") == 1
    assert repo.get_publication_content(root) == first
    assert repo.get_content_snapshot(first.bill_snapshot_id) is not None


def test_binding_requires_the_frozen_publication_and_never_replaces_an_edition(
    repo: SqliteBillRepository,
) -> None:
    root = publication(repo)
    first = version(root=root)
    record(repo, first)
    repo.bind_publication_content(root, first.content_id)
    before = repo.dump()
    repo.bind_publication_content(root, first.content_id)
    assert repo.dump() == before
    wrong = version(root=root, text="different")
    record(repo, wrong)

    with pytest.raises(ValueError, match="frozen"):
        repo.bind_publication_content(root, wrong.content_id)
    with pytest.raises(ValueError, match="publication"):
        repo.bind_publication_content(999, first.content_id)
    assert repo.get_publication_content(root) == first
    assert repo.get_publication_content(999) is None


def test_content_write_is_part_of_the_calling_transaction(repo: SqliteBillRepository) -> None:
    before = repo.dump()
    with pytest.raises(RuntimeError), repo.atomic():
        record(repo, version())
        raise RuntimeError("rollback")
    assert repo.dump() == before


def test_reader_rejects_mismatched_sql_identity_columns(
    repo: SqliteBillRepository, tmp_path: Path
) -> None:
    value = version()
    record(repo, value)
    with sqlite3.connect(tmp_path / "content.db") as connection:
        connection.execute("UPDATE content_versions SET root_publication_id=2")
    with pytest.raises(ValueError, match="columns"):
        repo.get_content_version(value.content_id)


@pytest.mark.parametrize(
    "field,value", [("root_publication_id", 0), ("rendered_text", ""), ("application_ids", (0,))]
)
def test_copied_content_is_revalidated_before_any_write(
    repo: SqliteBillRepository,
    field: str,
    value: object,
) -> None:
    first = version()
    record(repo, first)
    before = repo.dump()
    with pytest.raises(ValidationError):
        repo.save_content_version(first.model_copy(update={field: value}))
    assert repo.dump() == before


def test_binding_is_immutable_even_when_another_version_has_the_same_payload(
    repo: SqliteBillRepository,
) -> None:
    root = publication(repo)
    first = version(root=root)
    record(repo, first)
    repo.bind_publication_content(root, first.content_id)
    another = first.model_copy(update={"formatter_version": "different"})
    repo.save_content_version(another)
    with pytest.raises(ValueError, match="immutable"):
        repo.bind_publication_content(root, another.content_id)
    assert repo.get_publication_content(root) == first


def test_a_matching_rendered_text_cannot_bind_a_different_historical_bill(
    repo: SqliteBillRepository,
) -> None:
    root = publication(repo)
    first = version(root=root)
    record(repo, first)
    wrong = ContentSnapshot(kind="bill", payload_json='{"historical":"another bill"}')
    repo.save_content_snapshot(wrong)
    corrupted = first.model_copy(update={"bill_snapshot_id": wrong.snapshot_id})
    repo.save_content_version(corrupted)

    with pytest.raises(ValueError, match="semantic snapshot"):
        repo.bind_publication_content(root, corrupted.content_id)

    assert repo.get_publication_content(root) is None


def test_delivery_defaults_do_not_rewrite_or_invalidate_an_older_content_snapshot(
    repo: SqliteBillRepository,
) -> None:
    root = publication(repo)
    first = version(root=root)
    record(repo, first)
    legacy = ContentSnapshot(kind="plan", payload_json='{"bill_json":"","today":"2026-09-01"}')
    repo.save_content_snapshot(legacy)
    older = first.model_copy(update={"plan_snapshot_id": legacy.snapshot_id})
    repo.save_content_version(older)

    repo.bind_publication_content(root, older.content_id)

    assert repo.get_publication_content(root) == older
    assert repo.get_content_snapshot(legacy.snapshot_id) == legacy


@pytest.mark.parametrize("field", ["bill_json", "change_json", "rendered_text"])
def test_plan_snapshot_never_duplicates_separate_bodies(field: str) -> None:
    with pytest.raises(ValueError, match="separately"):
        ContentSnapshot(kind="plan", payload_json=json.dumps({field: "body"}))
