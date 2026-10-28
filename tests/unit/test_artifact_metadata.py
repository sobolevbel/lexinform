import datetime as dt
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest
from pydantic import ValidationError

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    ArtifactMetadata,
    DocumentPart,
    DocumentSelection,
    DocumentSelectionRecord,
    SelectionArtifactReference,
    SourceObservation,
    TextDocument,
)

NOW = dt.datetime(2026, 10, 27, tzinfo=dt.UTC)
ROOT, MEMBER = "a" * 64, "b" * 64


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[SqliteBillRepository]:
    repository = SqliteBillRepository(tmp_path / "state.db")
    repository.migrate()
    yield repository
    repository.close()


def metadata(*, sha: str = ROOT, size: int = 20) -> ArtifactMetadata:
    return ArtifactMetadata(sha256=sha, byte_size=size, recorded_at=NOW)


def selection(repo: SqliteBillRepository, *, url: str = "https://source.test/one.zip") -> str:
    part = DocumentPart(url=url, members=("draft.docx",), container_sha256=ROOT, sha256=MEMBER)
    record = DocumentSelectionRecord.from_selection(
        DocumentSelection(parts=(part,), observed_parts=(part,))
    )
    repo.save_source_observation(
        record,
        SourceObservation(
            term=10,
            number="3039",
            purpose="prefilter",
            document=TextDocument(url=url, kind="print"),
            selection_id=record.selection_id,
            recorded_at=NOW,
        ),
    )
    return record.selection_id


def reference(selection_id: str, *, sha: str = ROOT) -> SelectionArtifactReference:
    return SelectionArtifactReference(
        selection_id=selection_id, part_scope="parts", part_index=0, artifact_sha256=sha
    )


def test_metadata_repeated_observation_preserves_first_recording_and_exact_dump(
    repo: SqliteBillRepository,
) -> None:
    first = metadata()
    repo.save_artifact_metadata(first)
    before = repo.dump()

    repo.save_artifact_metadata(
        first.model_copy(update={"recorded_at": NOW + dt.timedelta(days=30)})
    )

    assert repo.get_artifact_metadata(ROOT) == first
    assert repo.dump() == before
    assert "retained" not in first.model_dump_json()


def test_one_complete_body_keeps_distinct_selection_memberships_and_urls(
    repo: SqliteBillRepository, tmp_path: Path
) -> None:
    repo.save_artifact_metadata(metadata())
    first = selection(repo)
    second = selection(repo, url="https://source.test/two.zip")
    a, b = reference(first), reference(second)

    repo.save_selection_artifact(a)
    repo.save_selection_artifact(b)
    repo.save_selection_artifact(a.model_copy(update={"part_scope": "observed_parts"}))
    before = repo.dump()
    repo.save_selection_artifact(a)

    assert repo.dump() == before
    assert repo.list_selection_artifacts(first) == [
        a,
        a.model_copy(update={"part_scope": "observed_parts"}),
    ]
    assert repo.list_selection_artifacts(second) == [b]
    with closing(sqlite3.connect(tmp_path / "state.db")) as db:
        assert db.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM selection_artifacts").fetchone()[0] == 3


def test_same_sha_cannot_silently_change_its_recorded_byte_size(repo: SqliteBillRepository) -> None:
    repo.save_artifact_metadata(metadata())
    before = repo.dump()

    with pytest.raises(ValueError, match="byte size"):
        repo.save_artifact_metadata(metadata(size=21))

    assert repo.dump() == before


@pytest.mark.parametrize(
    "damage", ["member", "absent_part", "unknown_artifact", "unknown_selection"]
)
def test_links_require_the_recorded_outer_source_and_known_complete_body(
    repo: SqliteBillRepository, damage: str
) -> None:
    selected = selection(repo)
    link = reference(selected)
    if damage != "unknown_artifact":
        repo.save_artifact_metadata(metadata())
    if damage == "member":
        repo.save_artifact_metadata(metadata(sha=MEMBER, size=5))
        link = link.model_copy(update={"artifact_sha256": MEMBER})
    elif damage == "absent_part":
        link = link.model_copy(update={"part_index": 1})
    elif damage == "unknown_selection":
        link = link.model_copy(update={"selection_id": "selection-v1:" + "c" * 64})
    before = repo.dump()

    with pytest.raises(ValueError):
        repo.save_selection_artifact(link)

    assert repo.dump() == before


@pytest.mark.parametrize("damage", ["negative_size", "unscoped_digest", "naive_time"])
def test_invalid_unvalidated_metadata_copies_write_nothing(
    repo: SqliteBillRepository, damage: str
) -> None:
    changes: dict[str, object] = {
        "byte_size": -1,
    }
    if damage == "unscoped_digest":
        changes = {"sha256": "text-v2:" + ROOT}
    elif damage == "naive_time":
        changes = {"recorded_at": dt.datetime(2026, 10, 27)}
    before = repo.dump()

    with pytest.raises(ValidationError):
        repo.save_artifact_metadata(metadata().model_copy(update=changes))

    assert repo.dump() == before


def test_caller_transaction_and_sql_restore_preserve_metadata_dependencies(
    repo: SqliteBillRepository,
) -> None:
    before = repo.dump()
    with pytest.raises(RuntimeError, match="checkpoint"), repo.atomic():
        repo.save_artifact_metadata(metadata())
        selected = selection(repo)
        repo.save_selection_artifact(reference(selected))
        raise RuntimeError("checkpoint failed")
    assert repo.dump() == before
    repo.save_artifact_metadata(metadata())
    selected = selection(repo)
    repo.save_selection_artifact(reference(selected))
    populated = repo.dump()

    repo.restore(populated)

    assert repo.dump() == populated
    assert repo.get_artifact_metadata(ROOT) == metadata()
    assert repo.list_selection_artifacts(selected) == [reference(selected)]
