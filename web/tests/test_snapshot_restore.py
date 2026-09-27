import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from lexinform.adapters.sqlite_repo import MIGRATIONS, SCHEMA_VERSION, SqliteBillRepository
from lexinform.models import Bill
from lexinform_web.ingestion.restore import MAX_DUMP_BYTES, SnapshotError, restore_snapshot


def dump(version: int = SCHEMA_VERSION, *, marker: str = "") -> bytes:
    with closing(sqlite3.connect(":memory:")) as connection:
        for migration in MIGRATIONS[:version]:
            connection.executescript(migration)
        if marker:
            connection.execute(
                "INSERT INTO analysis_memo (key, record_json) VALUES (?, ?)", ("test", marker)
            )
            connection.commit()
        script = "\n".join(connection.iterdump())
    if version > 1:
        script += f"\nPRAGMA user_version = {version};\n"
    return script.encode()


@pytest.mark.parametrize("version", range(1, SCHEMA_VERSION + 1))
def test_restore_supported_schemas_into_read_only_current_schema(version: int) -> None:
    raw = dump(version)

    with restore_snapshot(raw) as snapshot:
        assert snapshot.source_schema == version
        assert snapshot.normalized_schema == SCHEMA_VERSION
        assert snapshot.dump_sha256 == hashlib.sha256(raw).hexdigest()
        assert snapshot.connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        assert snapshot.connection.execute("SELECT * FROM publication_sequence").fetchall() == [
            (1, 0)
        ]
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            snapshot.connection.execute("DELETE FROM bills")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        snapshot.connection.execute("SELECT 1")


def test_sql_looking_multiline_text_remains_data() -> None:
    marker = "строка;\nCOMMIT;\nATTACH DATABASE '/tmp/should-not-exist' AS other;\n'quote'"

    with restore_snapshot(dump(marker=marker)) as snapshot:
        assert snapshot.connection.execute("SELECT record_json FROM analysis_memo").fetchone() == (
            marker,
        )


def test_repository_dump_preserves_populated_bill_and_analysis() -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures/import_legacy.json").read_text())
    bill = Bill.model_validate(fixture["bill"])
    assert bill.analysis is not None
    repository = SqliteBillRepository(":memory:")
    try:
        repository.migrate()
        repository.upsert_summary(bill.summary, now=bill.analysis.created_at)
        repository.save_analysis(bill.term, bill.number, bill.analysis)
        raw = repository.dump().encode()
    finally:
        repository.close()

    with restore_snapshot(raw) as snapshot:
        term, number, analysis = snapshot.connection.execute(
            "SELECT term, number, analysis_json FROM bills"
        ).fetchone()
        assert (term, number) == (bill.term, bill.number)
        assert json.loads(analysis) == bill.analysis.model_dump(mode="json")


def test_reject_oversized_snapshot() -> None:
    with pytest.raises(SnapshotError, match="size"), restore_snapshot(b" " * (MAX_DUMP_BYTES + 1)):
        pytest.fail("oversized snapshot accepted")


def test_reject_dangling_foreign_key() -> None:
    raw = dump().replace(
        b"COMMIT;",
        b'INSERT INTO "llm_batch_items" VALUES'
        b"('missing', 'item', 'analysis', 10, '3039', '{}', NULL, NULL, NULL);\nCOMMIT;",
    )

    with pytest.raises(SnapshotError, match="dangling foreign keys"), restore_snapshot(raw):
        pytest.fail("dangling foreign key accepted")


@pytest.mark.parametrize(
    "statement",
    [
        "ATTACH DATABASE ':memory:' AS other;",
        "PRAGMA writable_schema = ON;",
        "SELECT load_extension('anything');",
        "CREATE VIEW dangerous AS SELECT 1;",
        "CREATE TRIGGER dangerous AFTER INSERT ON bills BEGIN DELETE FROM bills; END;",
        "CREATE VIRTUAL TABLE dangerous USING fts5(value);",
        "DELETE FROM bills;",
        "INSERT INTO \"analysis_memo\" VALUES('key',load_extension('anything'),NULL,NULL,NULL);",
        "INSERT INTO \"analysis_memo\" VALUES('key',(SELECT 'secret'),NULL,NULL,NULL);",
        "INSERT INTO \"analysis_memo\" VALUES('key',randomblob(1000000000),NULL,NULL,NULL);",
        "INSERT INTO \"analysis_memo\" VALUES('key','value',NULL,NULL,NULL); SELECT 1;",
    ],
)
def test_reject_sql_outside_restore_allowlist(statement: str) -> None:
    raw = dump().replace(b"COMMIT;", statement.encode() + b"\nCOMMIT;")

    with pytest.raises(SnapshotError), restore_snapshot(raw):
        pytest.fail("unsafe snapshot accepted")


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"\xff",
        b"BEGIN TRANSACTION;\nCOMMIT;",
        b"BEGIN TRANSACTION;\nCOMMIT;\nPRAGMA user_version = 0;",
        b"BEGIN TRANSACTION;\nCOMMIT;\nPRAGMA user_version = 999;",
        b"BEGIN TRANSACTION;\nCREATE TABLE bills (id INTEGER);\nCOMMIT;",
    ],
)
def test_reject_empty_invalid_or_incomplete_snapshots(raw: bytes) -> None:
    with pytest.raises(SnapshotError), restore_snapshot(raw):
        pytest.fail("invalid snapshot accepted")


def test_reject_truncated_snapshot() -> None:
    raw = dump().split(b"COMMIT;")[0]

    with pytest.raises(SnapshotError, match="incomplete"), restore_snapshot(raw):
        pytest.fail("truncated snapshot accepted")


def test_reject_modified_schema() -> None:
    raw = dump().replace(b"title TEXT NOT NULL", b"title BLOB NOT NULL", 1)

    with pytest.raises(SnapshotError), restore_snapshot(raw):
        pytest.fail("modified schema accepted")
