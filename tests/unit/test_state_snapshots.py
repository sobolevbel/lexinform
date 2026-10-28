import sqlite3
from collections.abc import Iterator
from contextlib import closing

import pytest

from lexinform.adapters.sqlite_repo import MIGRATIONS, SCHEMA_VERSION, SqliteBillRepository
from lexinform.models import Publication, PublicationKind, PublicationStatus
from tests import state_snapshots


@pytest.fixture
def source() -> Iterator[sqlite3.Connection]:
    with closing(sqlite3.connect(":memory:")) as connection:
        for migration in MIGRATIONS:
            connection.executescript(migration)
        yield connection


def test_historical_schema_keeps_data_foreign_keys_and_publication_high_water(
    source: sqlite3.Connection,
) -> None:
    source.execute(
        "INSERT INTO status_changes(id, term, number, new_fingerprint, new_stages_json,"
        " detected_at) VALUES (7, 10, '3039', 'original', '[]', '2026-10-28T00:00:00+00:00')"
    )
    source.execute(
        "INSERT INTO publications(id, term, number, kind, status, channel_id,"
        " status_change_id, created_at) VALUES"
        " (11, 10, '3039', 'status_update', 'unknown', 'test', 7, '2026-10-28T00:00:00+00:00')"
    )
    source.execute("UPDATE publication_sequence SET last_id=999")
    source.execute("ALTER TABLE publications ADD COLUMN future_fixture TEXT")
    source.execute("UPDATE publications SET future_fixture='not part of schema 54'")
    source.execute("CREATE TABLE future_fixture(body TEXT)")
    source.execute("INSERT INTO future_fixture VALUES ('later schema')")
    source.commit()
    before = "\n".join(source.iterdump())

    dump = state_snapshots.dump_at_version(source, version=54)

    with closing(sqlite3.connect(":memory:")) as historical:
        historical.executescript(dump)
        assert historical.execute("PRAGMA user_version").fetchone() == (54,)
        names = {row[0] for row in historical.execute("SELECT name FROM sqlite_master")}
        assert not {"content_versions", "extraction_results", "future_fixture"} & names
        columns = {row[1] for row in historical.execute("PRAGMA table_info(publications)")}
        assert "future_fixture" not in columns
        assert historical.execute(
            "SELECT id, status, status_change_id FROM publications"
        ).fetchone() == (11, "unknown", 7)
        assert historical.execute("SELECT last_id FROM publication_sequence").fetchone() == (999,)
        assert not historical.execute("PRAGMA foreign_key_check").fetchall()
    assert "\n".join(source.iterdump()) == before
    repository = SqliteBillRepository(":memory:")
    try:
        repository.restore(dump)
        original = repository.publication_by_id(11)
        assert original is not None and original.status is PublicationStatus.UNKNOWN, (
            "the copied historical publication survives restore with its unresolved state"
        )
        assert (
            repository.create_publication(
                Publication(
                    term=10,
                    number="3040",
                    kind=PublicationKind.NEW_BILL,
                    status=PublicationStatus.QUEUED,
                    channel_id="test",
                    created_at=original.created_at,
                )
            )
            == 1000
        )
    finally:
        repository.close()


def test_historical_projection_preserves_autoincrement_sequence_after_deletion(
    source: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    migration = "CREATE TABLE sequence_fixture(id INTEGER PRIMARY KEY AUTOINCREMENT, value TEXT);"
    source.executescript(migration)
    source.execute("INSERT INTO sequence_fixture(id, value) VALUES (31, 'deleted')")
    source.execute("DELETE FROM sequence_fixture")
    monkeypatch.setattr(state_snapshots, "MIGRATIONS", (*MIGRATIONS, migration))

    dump = state_snapshots.dump_at_version(source, version=SCHEMA_VERSION + 1)

    with closing(sqlite3.connect(":memory:")) as restored:
        restored.executescript(dump)
        restored.execute("INSERT INTO sequence_fixture(value) VALUES ('next')")
        assert restored.execute("SELECT id FROM sequence_fixture").fetchone() == (32,)


@pytest.mark.parametrize("version", [0, SCHEMA_VERSION + 1])
def test_historical_projection_refuses_a_version_outside_known_migrations(
    source: sqlite3.Connection, version: int
) -> None:
    with pytest.raises(ValueError, match="outside the migration history"):
        state_snapshots.dump_at_version(source, version=version)


def test_historical_projection_does_not_hide_broken_foreign_keys(
    source: sqlite3.Connection,
) -> None:
    source.execute(
        "INSERT INTO publications(term, number, kind, status, channel_id, status_change_id,"
        " created_at) VALUES"
        " (10, '3039', 'status_update', 'unknown', 'test', 999, '2026-10-28T00:00:00+00:00')"
    )

    with pytest.raises(ValueError, match="violates foreign keys"):
        state_snapshots.dump_at_version(source, version=54)
