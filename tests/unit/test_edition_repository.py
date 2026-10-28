import json
import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest
from tools.source_history_doctor import audit

from lexinform.adapters.sqlite_repo import MIGRATIONS, SqliteBillRepository
from lexinform.document_editions import candidate_edition
from lexinform.models import (
    DocumentEdition,
    DocumentSelectionRecord,
    SourceObservation,
    TextDocument,
)
from tests.unit.test_document_editions import NOW, URL, source_record


def store(repo: SqliteBillRepository, record: DocumentSelectionRecord) -> DocumentEdition:
    repo.save_source_observation(
        record,
        SourceObservation(
            term=10,
            number="3039",
            purpose="analysis",
            document=TextDocument(url=URL, kind="print"),
            selection_id=record.selection_id,
            recorded_at=NOW,
        ),
    )
    edition = candidate_edition(record, recorded_at=NOW)
    assert edition is not None, "the fixture contains one exact candidate normative set"
    return edition


def test_new_source_edition_survives_restore_cleanup_and_preserves_first_recording(
    repo: SqliteBillRepository,
) -> None:
    record = source_record()
    edition = store(repo, record)
    assert repo.get_selection_edition(record.selection_id) is None

    repo.save_selection_edition(record.selection_id, edition)
    repo.save_selection_edition(
        record.selection_id, edition.model_copy(update={"recorded_at": NOW + timedelta(days=10)})
    )
    repo.restore(repo.dump())
    repo.prune_history(before=NOW + timedelta(days=365), memo_before=NOW + timedelta(days=365))

    assert repo.get_document_edition(edition.edition_id) == edition
    link = repo.get_selection_edition(record.selection_id)
    assert link is not None and link.edition_id == edition.edition_id, (
        "the immutable link remains recorded"
    )


@pytest.mark.parametrize("problem", ["missing_parent", "wrong_membership", "wrong_count"])
def test_edition_writer_requires_the_exact_recorded_parent_without_partial_writes(
    repo: SqliteBillRepository, problem: str
) -> None:
    record = source_record()
    edition = store(repo, record)
    if problem == "wrong_membership":
        edition = edition.model_copy(update={"membership_sha256": "f" * 64})
    if problem == "wrong_count":
        edition = edition.model_copy(update={"member_count": 2})
    identity = "selection-v1:" + "f" * 64 if problem == "missing_parent" else record.selection_id
    before = repo.dump()

    with pytest.raises(ValueError, match="exact candidate membership"):
        repo.save_selection_edition(identity, edition)

    assert repo.dump() == before


@pytest.mark.parametrize("table", ["document_editions", "selection_editions"])
def test_edition_link_failure_rolls_back_the_new_manifest(
    repo: SqliteBillRepository, table: str
) -> None:
    record = source_record()
    edition = store(repo, record)
    repo.restore(
        repo.dump()
        + f"\nCREATE TRIGGER refuse_edition BEFORE INSERT ON {table} BEGIN SELECT RAISE(IGNORE); END;\n"
    )
    before = repo.dump()

    with pytest.raises(ValueError, match="cannot be"):
        repo.save_selection_edition(record.selection_id, edition)

    assert repo.dump() == before


def test_new_links_do_not_rewrite_opaque_parent_json(repo: SqliteBillRepository) -> None:
    record = source_record()
    edition = store(repo, record)
    raw = record.model_dump_json()
    content = json.loads(raw)
    content["selection"]["future_opaque"] = {"keep": [1, 2, 3]}
    extended = json.dumps(content, separators=(",", ":"), ensure_ascii=False)
    quoted, replacement = (
        "'" + raw.replace("'", "''") + "'",
        "'" + extended.replace("'", "''") + "'",
    )
    repo.restore(repo.dump().replace(quoted, replacement))

    repo.save_selection_edition(record.selection_id, edition)

    assert replacement in repo.dump()


def test_schema_56_restore_creates_empty_edition_tables_without_inferred_history(
    repo: SqliteBillRepository,
) -> None:
    with sqlite3.connect(":memory:") as old:
        for migration in MIGRATIONS[:56]:
            old.executescript(migration)
        dump = "\n".join(old.iterdump()) + "\nPRAGMA user_version = 56;\n"

    repo.restore(dump)

    assert repo.schema_version == 57
    assert repo.get_document_edition("edition-v1:" + "f" * 64) is None


def test_source_doctor_checks_edition_membership_without_exposing_bodies(tmp_path: Path) -> None:
    path = tmp_path / "source.db"
    repo = SqliteBillRepository(path)
    repo.migrate()
    record = source_record()
    edition = store(repo, record)
    repo.save_selection_edition(record.selection_id, edition)
    repo.close()

    report = audit(path)

    assert report["summary"]["errors"] == 0
    assert report["summary"]["edition_rows"] == report["summary"]["edition_link_rows"] == 1
    assert URL not in json.dumps(report)


@pytest.mark.parametrize(
    ("statement", "code", "read_link"),
    [
        ("UPDATE document_editions SET recorded_at='bad'", "edition_columns_mismatch", False),
        ("UPDATE document_editions SET edition_json='{}'", "invalid_document_edition", False),
        (
            "UPDATE selection_editions SET edition_id='edition-v1:" + "f" * 64 + "'",
            "edition_parent_unavailable",
            True,
        ),
        (
            "UPDATE selection_editions SET selection_id='selection-v1:" + "f" * 64 + "'",
            "edition_parent_unavailable",
            True,
        ),
    ],
)
def test_corrupt_identity_or_parent_is_rejected_without_repair(
    tmp_path: Path, statement: str, code: str, read_link: bool
) -> None:
    path = tmp_path / "source.db"
    repo = SqliteBillRepository(path)
    repo.migrate()
    record = source_record()
    edition = store(repo, record)
    repo.save_selection_edition(record.selection_id, edition)
    repo.close()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(statement)
    repo = SqliteBillRepository(path)
    before = repo.dump()

    with pytest.raises(ValueError):
        if read_link:
            identity = (
                "selection-v1:" + "f" * 64
                if "SET selection_id" in statement
                else record.selection_id
            )
            repo.get_selection_edition(identity)
        else:
            repo.get_document_edition(edition.edition_id)

    assert repo.dump() == before
    repo.close()
    report = audit(path)
    assert code in {row["code"] for row in report["findings"]}
    assert URL not in json.dumps(report)


def test_a_foreign_but_existing_edition_cannot_replace_the_selection_membership(
    tmp_path: Path,
) -> None:
    path = tmp_path / "source.db"
    repo = SqliteBillRepository(path)
    repo.migrate()
    first = source_record()
    first_edition = store(repo, first)
    other = DocumentSelectionRecord.from_selection(
        first.selection.model_copy(
            update={"parts": (first.selection.parts[0].model_copy(update={"sha256": "b" * 64}),)}
        )
    )
    other_edition = store(repo, other)
    repo.save_selection_edition(first.selection_id, first_edition)
    repo.save_selection_edition(other.selection_id, other_edition)
    repo.close()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "UPDATE selection_editions SET edition_id=? WHERE selection_id=?",
            (other_edition.edition_id, first.selection_id),
        )
    repo = SqliteBillRepository(path)
    before = repo.dump()

    with pytest.raises(ValueError, match="membership"):
        repo.get_selection_edition(first.selection_id)
    with pytest.raises(ValueError, match="membership"):
        repo.save_selection_edition(first.selection_id, first_edition)

    assert repo.dump() == before
    repo.close()
    report = audit(path)
    assert "edition_membership_mismatch" in {row["code"] for row in report["findings"]}
