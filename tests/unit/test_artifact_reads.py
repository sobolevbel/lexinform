import datetime as dt
import hashlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import ArtifactMetadata, ArtifactReadIdentity, ArtifactReadReceipt

NOW = dt.datetime(2026, 10, 27, tzinfo=dt.UTC)
BODY = b"complete response fixture"
SHA = hashlib.sha256(BODY).hexdigest()
URL = "https://source.test/official.pdf"


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[SqliteBillRepository]:
    repository = SqliteBillRepository(tmp_path / "state.db")
    repository.migrate()
    yield repository
    repository.close()


def receipt(*, url: str = URL, body: bytes = BODY) -> ArtifactReadReceipt:
    return ArtifactReadReceipt(
        sha256=hashlib.sha256(body).hexdigest(),
        byte_size=len(body),
        requested_url=url,
        recorded_at=NOW,
    )


def record_body(repo: SqliteBillRepository, *, body: bytes = BODY) -> None:
    repo.save_artifact_metadata(
        ArtifactMetadata(
            sha256=hashlib.sha256(body).hexdigest(),
            byte_size=len(body),
            recorded_at=NOW,
        )
    )


def test_receipt_identity_excludes_recording_time_and_matches_the_worker_identity() -> None:
    first = receipt()
    later = first.model_copy(update={"recorded_at": NOW + dt.timedelta(days=1)})
    identity = ArtifactReadIdentity(sha256=SHA, byte_size=len(BODY), requested_url=URL)

    assert first.read_id == later.read_id == identity.read_id
    assert first.read_id.startswith("artifact-read-v1:")
    assert "recorded_at" not in identity.model_dump()


def test_repeated_receipt_keeps_first_time_and_the_exact_dump(repo: SqliteBillRepository) -> None:
    record_body(repo)
    first = receipt()
    repo.save_artifact_read(first)
    before = repo.dump()

    repo.save_artifact_read(first.model_copy(update={"recorded_at": NOW + dt.timedelta(days=30)}))

    assert repo.dump() == before
    assert repo.get_artifact_read(first.read_id) == first


def test_aliases_and_changed_responses_are_distinct_from_one_physical_body(
    repo: SqliteBillRepository,
) -> None:
    changed = b"changed response fixture"
    record_body(repo)
    record_body(repo, body=changed)
    first, alias, later = receipt(), receipt(url=URL + "?alias=1"), receipt(body=changed)
    for item in (first, alias, later):
        repo.save_artifact_read(item)

    assert len({r.read_id for r in repo.list_artifact_reads()}) == 3
    assert repo.list_artifact_reads(sha256=SHA) == [first, alias]
    assert repo.list_artifact_reads(requested_url=URL) == [first, later]
    assert repo.list_artifact_reads(sha256=SHA, requested_url=URL) == [first]
    assert not repo.list_artifact_reads(requested_url="https://absent.test")


def test_a_missing_body_is_not_created_by_a_declared_receipt(repo: SqliteBillRepository) -> None:
    with pytest.raises(ValueError, match="matching complete-body"):
        repo.save_artifact_read(receipt())

    assert repo.get_artifact_metadata(SHA) is None
    assert not repo.list_artifact_reads()


def test_declared_size_must_match_known_complete_body_metadata(repo: SqliteBillRepository) -> None:
    record_body(repo)
    before = repo.dump()

    with pytest.raises(ValueError, match="matching complete-body"):
        repo.save_artifact_read(receipt().model_copy(update={"byte_size": len(BODY) + 1}))

    assert repo.dump() == before


def test_body_and_receipt_roll_back_together_in_the_calling_transaction(
    repo: SqliteBillRepository,
) -> None:
    before = repo.dump()

    with pytest.raises(RuntimeError, match="rollback"), repo.atomic():
        record_body(repo)
        repo.save_artifact_read(receipt())
        raise RuntimeError("rollback")

    assert repo.dump() == before


def test_receipt_survives_exact_sql_restore_and_contains_no_response_bytes(
    repo: SqliteBillRepository,
) -> None:
    record_body(repo)
    first = receipt()
    repo.save_artifact_read(first)
    sql = repo.dump()

    repo.restore(sql)

    assert repo.dump() == sql and repo.get_artifact_read(first.read_id) == first
    assert BODY.decode() not in sql
    assert repo.get_artifact_read("artifact-read-v1:" + "f" * 64) is None


def test_empty_complete_body_is_distinct_from_no_receipt(repo: SqliteBillRepository) -> None:
    record_body(repo, body=b"")
    empty = receipt(body=b"")
    repo.save_artifact_read(empty)

    assert repo.get_artifact_read(empty.read_id) == empty
    assert empty.byte_size == 0


@pytest.mark.parametrize(
    "field,value", [("sha256", "wrong"), ("byte_size", -1), ("requested_url", "")]
)
def test_save_revalidates_copied_receipt_data(
    repo: SqliteBillRepository, field: str, value: str | int
) -> None:
    record_body(repo)
    before = repo.dump()

    with pytest.raises(ValidationError):
        repo.save_artifact_read(receipt().model_copy(update={field: value}))

    assert repo.dump() == before


def test_naive_recording_time_is_rejected_and_offsets_are_normalized(
    repo: SqliteBillRepository,
) -> None:
    record_body(repo)
    first = receipt()
    with pytest.raises(ValidationError):
        repo.save_artifact_read(first.model_copy(update={"recorded_at": NOW.replace(tzinfo=None)}))
    local = NOW.astimezone(dt.timezone(dt.timedelta(hours=2)))
    repo.save_artifact_read(first.model_copy(update={"recorded_at": local}))

    saved = repo.get_artifact_read(first.read_id)
    assert saved is not None, "the validated insert retains its receipt"
    assert saved.recorded_at == NOW and saved.recorded_at.utcoffset() == dt.timedelta()


def test_corrupt_identity_columns_are_not_accepted(
    repo: SqliteBillRepository, tmp_path: Path
) -> None:
    record_body(repo)
    first = receipt()
    repo.save_artifact_read(first)
    with sqlite3.connect(tmp_path / "state.db") as connection:
        connection.execute("UPDATE artifact_reads SET requested_url=?", (URL + "?changed=1",))

    with pytest.raises(ValueError, match="columns differ"):
        repo.get_artifact_read(first.read_id)
