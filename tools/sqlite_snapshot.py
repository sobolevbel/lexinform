"""Open checkpointed SQLite copies without writes and detect concurrent changes."""

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

from tools.corpus_artifacts import digest, read_artifact


def live_journal(path: Path) -> bool:
    return any(
        sidecar.exists() and sidecar.stat().st_size > 0
        for sidecar in (Path(str(path) + suffix) for suffix in ("-wal", "-journal"))
    )


@dataclass(frozen=True)
class SqliteSnapshot:
    path: Path
    connection: sqlite3.Connection
    sha256: str

    def unchanged(self) -> bool:
        return digest(read_artifact(self.path)) == self.sha256 and not live_journal(self.path)


@contextmanager
def open_snapshot(database: Path) -> Iterator[SqliteSnapshot]:
    path = database.resolve(strict=True)
    if live_journal(path):
        raise ValueError("Use a checkpointed SQLite snapshot without a live WAL/journal")
    before = digest(read_artifact(path))
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        yield SqliteSnapshot(path, connection, before)
