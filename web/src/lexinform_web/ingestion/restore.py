import hashlib
import re
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass

from lexinform.adapters.sqlite_repo import MIGRATIONS, SCHEMA_VERSION

MAX_DUMP_BYTES = 20 * 1024 * 1024
MAX_DATABASE_PAGES = 32768
MAX_SQL_STEPS = 10_000_000


class SnapshotError(ValueError):
    pass


@dataclass(frozen=True)
class RestoredSnapshot:
    connection: sqlite3.Connection
    source_schema: int
    normalized_schema: int
    dump_sha256: str


def statements(script: str) -> Iterator[str]:
    pending: list[str] = []
    for line in script.splitlines(keepends=True):
        pending.append(line)
        if line.rstrip().endswith(";"):
            candidate = "".join(pending).strip()
            if sqlite3.complete_statement(candidate):
                yield candidate
                pending.clear()
    if "".join(pending).strip():
        raise SnapshotError("incomplete SQL statement")


def schema_statements(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0]) + ";"
        for row in connection.execute("SELECT sql FROM sqlite_schema WHERE sql IS NOT NULL")
    }


def insert_authorizer(
    action: int,
    name: str | None,
    column: str | None,
    database: str | None,
    trigger: str | None,
) -> int:
    if action == sqlite3.SQLITE_INSERT and database == "main" and trigger is None:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def load_rows(connection: sqlite3.Connection, sql: list[str]) -> None:
    expected = schema_statements(connection)
    seen: set[str] = set()
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_schema WHERE type = 'table'")
    }
    for table in tables:
        connection.execute(f'DELETE FROM "{table}"')
    connection.commit()
    connection.execute("BEGIN")
    connection.set_authorizer(insert_authorizer)
    try:
        for statement in sql:
            if statement in expected:
                if statement in seen:
                    raise SnapshotError("duplicate schema declaration")
                seen.add(statement)
                continue
            match = re.match(r'INSERT INTO "([a-z_]+)" VALUES\(', statement)
            if match is None or match[1] not in tables:
                raise SnapshotError("SQL statement outside the restore allowlist")
            connection.execute(statement)
    finally:
        connection.set_authorizer(None)
    if seen != expected:
        raise SnapshotError("snapshot schema is incomplete or incompatible")
    connection.commit()


@contextmanager
def restore_snapshot(raw: bytes) -> Iterator[RestoredSnapshot]:
    """Restore only bot-generated SQL into disposable memory; callers must isolate the worker."""
    if not raw or len(raw) > MAX_DUMP_BYTES:
        raise SnapshotError("snapshot size outside allowed bounds")
    try:
        sql = list(statements(raw.decode("utf-8")))
    except UnicodeError as error:
        raise SnapshotError("snapshot is not UTF-8") from error
    source_schema = 1
    if sql and (version := re.fullmatch(r"PRAGMA user_version = ([0-9]+);", sql[-1])):
        source_schema = int(version[1])
        sql.pop()
    if not 1 <= source_schema <= SCHEMA_VERSION:
        raise SnapshotError("unsupported source schema")
    if len(sql) < 2 or sql[0] != "BEGIN TRANSACTION;" or sql[-1] != "COMMIT;":
        raise SnapshotError("snapshot transaction is incomplete")
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute("PRAGMA page_size = 4096")
        connection.execute(f"PRAGMA max_page_count = {MAX_DATABASE_PAGES}")
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_DUMP_BYTES)
        connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, MAX_DUMP_BYTES)
        steps = 0

        def budget_exceeded() -> bool:
            nonlocal steps
            steps += 1000
            return steps > MAX_SQL_STEPS

        connection.set_progress_handler(budget_exceeded, 1000)
        try:
            for migration in MIGRATIONS[:source_schema]:
                connection.executescript(migration)
            load_rows(connection, sql[1:-1])
            for migration in MIGRATIONS[source_schema:]:
                connection.executescript(migration)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise SnapshotError("snapshot integrity check failed")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise SnapshotError("snapshot contains dangling foreign keys")
            connection.execute("PRAGMA query_only = ON")
        except sqlite3.Error as error:
            raise SnapshotError("snapshot restore failed validation") from error
        connection.set_progress_handler(None, 0)
        yield RestoredSnapshot(
            connection=connection,
            source_schema=source_schema,
            normalized_schema=SCHEMA_VERSION,
            dump_sha256=hashlib.sha256(raw).hexdigest(),
        )
