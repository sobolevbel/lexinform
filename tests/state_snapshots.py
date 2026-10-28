import sqlite3
from contextlib import closing

from lexinform.adapters.sqlite_repo import MIGRATIONS


def _identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in connection.execute(f"PRAGMA table_info({_identifier(table)})")]


def dump_at_version(source: sqlite3.Connection, *, version: int) -> str:
    """Project saved data onto a genuine historical schema without carrying later DDL."""
    if not 1 <= version <= len(MIGRATIONS):
        raise ValueError("historical schema version is outside the migration history")
    with closing(sqlite3.connect(":memory:")) as target:
        for migration in MIGRATIONS[:version]:
            target.executescript(migration)
        source_tables = _tables(source)
        target_tables = _tables(target)
        for table in sorted(target_tables - {"sqlite_sequence"}):
            if table not in source_tables:
                raise ValueError(f"saved state has no historical table {table}")
            source_columns = set(_columns(source, table))
            columns = [column for column in _columns(target, table) if column in source_columns]
            names = ", ".join(_identifier(column) for column in columns)
            target.execute(f"DELETE FROM {_identifier(table)}")
            rows = source.execute(f"SELECT {names} FROM {_identifier(table)} ORDER BY rowid")
            placeholders = ", ".join("?" for _ in columns)
            target.executemany(
                f"INSERT INTO {_identifier(table)} ({names}) VALUES ({placeholders})", rows
            )
        if "sqlite_sequence" in target_tables and "sqlite_sequence" in source_tables:
            target.execute("DELETE FROM sqlite_sequence")
            target.executemany(
                "INSERT INTO sqlite_sequence(name, seq) VALUES (?, ?)",
                (
                    row
                    for row in source.execute("SELECT name, seq FROM sqlite_sequence")
                    if row[0] in target_tables
                ),
            )
        if target.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("historical snapshot violates foreign keys")
        target.commit()
        return "\n".join(target.iterdump()) + f"\nPRAGMA user_version = {version};\n"
