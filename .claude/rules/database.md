---
paths:
  - "src/lexinform/adapters/sqlite_repo.py"
  - "src/lexinform/adapters/state_snapshot.py"
  - "src/lexinform/json_storage.py"
  - "tests/unit/test_sqlite_repo.py"
  - "tests/scenario/test_state_retention.py"
---

# Database versioning and state

Schema, tables and what each version added: `docs/reference/database.md`; retention:
`docs/state-retention.md`.

- `PRAGMA user_version`; source of truth `MIGRATIONS` in `adapters/sqlite_repo.py` (script `i` →
  version `i + 1`, `SCHEMA_VERSION = len(MIGRATIONS)`), each script in its own transaction, so a
  failed script leaves the previous version. **Append-only**: never edit or reorder deployed scripts.
- How state travels: the workflow runs `db init` → `db restore state/lexinform.sql` → `run` → `db
  checkpoint state/lexinform.sql --branch state`. `dump()` = `iterdump()` + trailing `PRAGMA user_version = N;`; `restore()` drops tables,
  replays with foreign keys off, sets the version from that line (1 if absent) and migrates. Every
  durable state commit includes the derived `pending-batches.json`, batch checkpoints included.
- Adding a migration: plain SQL appended (`ALTER TABLE … ADD COLUMN` nullable or defaulted, new
  tables, indexes, `UPDATE` backfills; SQLite cannot alter a column — new table, `INSERT … SELECT`,
  drop, rename); model and row-mapping defaults so old rows and JSON columns load (renaming a JSON
  field is a data migration: `json_set` or a one-off Python step); extend `test_restore_of_a_v1_dump_applies_every_later_migration` in
  `tests/unit/test_sqlite_repo.py`, then run the real dump through it (`db init` + `db restore`,
  `sqlite3 file "PRAGMA user_version"`, `lexinform show 2699`). A backfill must not create
  retroactive news (v24 seeded `observed_closure_date` from `closure_date`).
- No schema downgrade: rollback preserves pending/unknown and reconciles later external effects before restoring an older dump.
- Retention keeps publication identities, batch token accounting and the last discovery watermark;
  it never expires uncertain work or a memo needed by pending analysis, tracking or delivery; legacy
  memos without ownership stay protected.
