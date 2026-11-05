# Reproduce the shared text qualification

Run `uv run --frozen pytest -q tests/unit/test_amendment_input_history.py tests/unit/test_sqlite_repo.py`. The v67 fixture contains a populated inline question. Shared-text fixtures verify mixed representations, paid references and atomic restore failures.

The storage recipe uses the two prepared questions from the pinned J253 body representation. Its input path and digest are recorded below. Run with the checkout on `PYTHONPATH` and the frozen virtual environment.

```python
import gzip
import hashlib
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from lexinform.adapters.amendment_inputs import read_amendment_input
from lexinform.json_storage import canonical_json, read_json

source = Path(
    "/private/tmp/lexinform-j253-amendment-off-baseline/out/qualification/off/body-representation.db"
)
expected = "c952273be9455e61e73d2a5318b458ee013ac49fd4ee713bf796827418be160e"
assert hashlib.sha256(source.read_bytes()).hexdigest() == expected
with closing(sqlite3.connect(f"{source.as_uri()}?mode=ro&immutable=1", uri=True)) as db:
    rows = db.execute("SELECT * FROM amendment_input_snapshots ORDER BY snapshot_id").fetchall()
    schema = db.execute(
        "SELECT sql FROM sqlite_master WHERE name='amendment_input_snapshots'"
    ).fetchone()[0]


def sizes(db: sqlite3.Connection, path: Path) -> dict[str, int]:
    dump = ("\n".join(db.iterdump()) + "\n").encode()
    return {
        "sql": len(dump),
        "gzip": len(gzip.compress(dump, mtime=0)),
        "sqlite": path.stat().st_size,
    }


stats = {}
with tempfile.TemporaryDirectory(prefix="lexinform-j260-") as scratch:
    for encoding in ("inline", "shared_context_text"):
        path = Path(scratch) / f"{encoding}.db"
        with closing(sqlite3.connect(path)) as db:
            db.executescript(schema + ";")
            if encoding == "shared_context_text":
                db.executescript("""
                PRAGMA foreign_keys=ON;
                CREATE TABLE amendment_input_texts (
                    text_sha256 TEXT PRIMARY KEY NOT NULL,
                    text TEXT NOT NULL
                );
                ALTER TABLE amendment_input_snapshots ADD COLUMN text_sha256 TEXT
                    REFERENCES amendment_input_texts(text_sha256);
                """)
            stats[encoding] = {"empty": sizes(db, path)}
            for snapshot_id, profile, question_sha256, question_json in rows:
                question = read_json(question_json)
                assert isinstance(question, dict)
                ctx = question["ctx"]
                assert isinstance(ctx, dict)
                if encoding == "inline":
                    db.execute(
                        "INSERT INTO amendment_input_snapshots VALUES (?,?,?,?)",
                        (snapshot_id, profile, question_sha256, question_json),
                    )
                else:
                    body = ctx.pop("text")
                    assert isinstance(body, str)
                    digest = hashlib.sha256(body.encode()).hexdigest()
                    db.execute(
                        "INSERT OR IGNORE INTO amendment_input_texts VALUES (?,?)", (digest, body)
                    )
                    db.execute(
                        "INSERT INTO amendment_input_snapshots VALUES (?,?,?,?,?)",
                        (snapshot_id, profile, question_sha256, canonical_json(question), digest),
                    )
            db.commit()
            stats[encoding]["populated"] = sizes(db, path)
            with closing(sqlite3.connect(":memory:")) as restored:
                restored.executescript("\n".join(db.iterdump()))
                assert restored.execute(
                    "SELECT count(*) FROM amendment_input_snapshots"
                ).fetchone() == (len(rows),)
                for snapshot_id, profile, question_sha256, question_json in rows:
                    snapshot = read_amendment_input(restored, snapshot_id)
                    assert snapshot is not None
                    assert (
                        snapshot.snapshot_id,
                        snapshot.profile,
                        snapshot.question_sha256,
                        snapshot.question_json,
                    ) == (snapshot_id, profile, question_sha256, question_json)
                if encoding == "shared_context_text":
                    bodies = restored.execute(
                        "SELECT count(*) FROM amendment_input_texts"
                    ).fetchone()[0]
assert hashlib.sha256(source.read_bytes()).hexdigest() == expected
print(
    json.dumps(
        {
            "source_sha256": expected,
            "questions": len(rows),
            "shared_bodies": bodies,
            "exact_restore_and_identity": True,
            "production_reader_verified": True,
            "representations": stats,
            "scope": "isolated tables for two prepared questions",
        },
        indent=2,
    )
)
```

To recheck the copied state, extract `lexinform.sql` from the `state_sha` recorded in [state qualification](state-proof.json) into a fresh scratch directory. Run the following script with the scratch directory and the state SHA as arguments. It opens only the local repository adapter.

```python
import gzip
import hashlib
import json
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from pathlib import Path

from lexinform.adapters.sqlite_repo import SCHEMA_VERSION, SqliteBillRepository

p = Path(sys.argv[1])
raw = (p / "state.sql").read_bytes()
with closing(sqlite3.connect(":memory:")) as old:
    old.executescript(raw.decode())
    before = old.execute("PRAGMA user_version").fetchone()[0]
    key = old.execute(
        "SELECT term,number FROM bills WHERE number='2699' ORDER BY term DESC LIMIT 1"
    ).fetchone()
    if key is None:
        key = old.execute(
            "SELECT term,number FROM bills ORDER BY term DESC, number LIMIT 1"
        ).fetchone()
    old_count = old.execute("SELECT count(*) FROM bills").fetchone()[0]
    old_bill = old.execute("SELECT * FROM bills WHERE term=? AND number=?", key).fetchone()
with closing(SqliteBillRepository(p / "v68.db")) as repo:
    repo.migrate()
    repo.restore(raw.decode())
    bill = repo.get(*key)
    assert bill is not None
    dump = repo.dump()
    repo.restore(dump)
    assert repo.dump() == dump
with closing(sqlite3.connect(p / "v68.db")) as db:
    version = db.execute("PRAGMA user_version").fetchone()[0]
    assert version == SCHEMA_VERSION == 68
    assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert not db.execute("PRAGMA foreign_key_check").fetchall()
    assert db.execute("SELECT count(*) FROM bills").fetchone() == (old_count,)
    assert db.execute("SELECT * FROM bills WHERE term=? AND number=?", key).fetchone() == old_bill
    assert db.execute("SELECT count(*) FROM amendment_input_texts").fetchone() == (0,)
with closing(sqlite3.connect(":memory:")) as old, closing(sqlite3.connect(p / "v68.db")) as new:
    old.executescript(raw.decode())
    tables = [row[0] for row in old.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    for name in tables:
        table = '"' + name.replace('"', '""') + '"'
        columns = [row[1] for row in old.execute("PRAGMA table_info(" + table + ")")]
        projection = ",".join('"' + col.replace('"', '""') + '"' for col in columns)
        query = "SELECT " + projection + " FROM " + table
        assert Counter(old.execute(query)) == Counter(new.execute(query)), name
encoded = dump.encode()
print(
    json.dumps(
        {
            "state_sha": sys.argv[2],
            "dump_sha256": hashlib.sha256(raw).hexdigest(),
            "before": before,
            "after": version,
            "bills": old_count,
            "known_bill": key,
            "known_bill_row_exact": True,
            "old_tables_rows_exact": len(tables),
            "shared_texts": 0,
            "integrity": "ok",
            "foreign_key_errors": 0,
            "double_restore_exact": True,
            "sql_delta_bytes": len(encoded) - len(raw),
            "gzip_delta_bytes": len(gzip.compress(encoded, mtime=0))
            - len(gzip.compress(raw, mtime=0)),
        },
        indent=2,
    )
)
```
