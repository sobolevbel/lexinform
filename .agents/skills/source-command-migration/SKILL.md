---
name: "source-command-migration"
description: "Добавить и проверить append-only миграцию SQLite для lexinform"
---

# source-command-migration

Use for the migrated `migration` command. First confirm the value cannot be derived from existing
data; derived next-step state is never persisted.

Append exactly one forward-only SQL migration to `MIGRATIONS` in `sqlite_repo.py`; never edit or
reorder deployed entries. Update models and row mappings with defaults compatible with old JSON.
Extend the legacy-dump migration test and add behaviour coverage. Run the full gate, then restore
a fresh `state`-branch dump in `/tmp`, check its schema version, and read a known bill.

Document any enduring invariant in `AGENTS.md` when it affects future changes. Commit the coherent
migration, model, tests, and documentation together; never attempt a downgrade.
