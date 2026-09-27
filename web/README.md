# lexinform web

The website is a separate package in the repository's uv workspace. It may import the bot's pure
models and helpers; the bot never imports Django or this package.

## Local bootstrap

No `.env` file is required for the development defaults. Start PostgreSQL, install the web package
and apply migrations:

```bash
docker compose -f web/compose.local.yml up -d --wait
uv sync --package lexinform-web
uv run --package lexinform-web python web/manage.py migrate
uv run --package lexinform-web python web/manage.py web_doctor
```

Run the development server with:

```bash
uv run --package lexinform-web python web/manage.py runserver
```

The local database is exposed on loopback only. Production settings have no secret-key or database
password fallback.

## Shortcuts from the repository root

```bash
make web-sync                 # install all workspace packages and tools, as in CI
make web-serve                # development server; ARGS="127.0.0.1:8002" changes the address
make web-test                 # tests; ARGS="-k import -vv" narrows the selection
make web-lint                 # Ruff and formatting check
make web-typecheck            # strict mypy
make web-format               # apply formatting
make web-check                # lint, types, Django checks, migration drift and tests
```

Start PostgreSQL and apply local migrations with the bootstrap commands above before running
the server or full web check. The shortcuts do not start Docker or apply database migrations.

## Staff access

Run `uv run --package lexinform-web python web/manage.py createsuperuser` for the first
administrator. Open `/accounts/login/`, use the account's email, verify that email, and
enroll TOTP before entering `/admin/`. Development verification/reset mail is printed to
the console through Django 6.1 `MAILERS`; test mail stays in memory.

Create editors with `is_staff` and the required Wagtail group/page permissions. Being staff
or completing MFA does not grant publication, user management or upload permissions.
Reader signup is closed. Save recovery codes securely: each can be used once in place of TOTP.
Password reset preserves the authenticator. Admin access requires another MFA check after
12 hours; disabling TOTP through the web UI is not allowed.

If both the device and recovery codes are lost, an administrator must verify the person's
identity outside the login flow before removing that account's authenticators through a
trusted management session. This must be recorded in the operational incident log; do not
disable the middleware or remove MFA on the basis of an email reset alone. The next login
requires enrollment again.

Production delivery configuration, shared rate-limit/replay cache and operational recovery
rehearsal belong to WEB-05. Console mail and a per-process cache are development defaults,
not the production deployment configuration.

## Database worker and recovery

After `migrate`, start one worker with
`uv run --package lexinform-web python web/manage.py web_worker`.
`--batch` drains ready tasks and exits. This wraps `django-tasks-db`'s worker with a
single-host lock and disables autoreload. Always use this wrapper rather than `db_worker`
directly: the wrapper and recovery must acquire the same lock.

The backend writes enqueue in the caller's database transaction. SIGTERM finishes the
current task before stopping. SIGKILL leaves its result `RUNNING`; neither a restart nor
an old `started_at` makes that task safe to retry. The backend has no expiring lease.

For a reproducible, isolated failure rehearsal, run:

```bash
uv run --all-packages --all-groups pytest -c web/pyproject.toml web/tests/test_worker.py -v
```

These tests create a test database and real worker subprocesses, kill them only after the
probe starts, check that a second worker/recovery cannot take live work, and confirm that
explicit recovery succeeds after exit. They also cover rollback/uncommitted enqueue,
duplicate execution, a failed attempt, retry limits and pruning without deleting unfinished work.

Only the idempotent `worker_probe` task is currently eligible for manual retry:

```bash
uv run --package lexinform-web python web/manage.py recover_worker_probe TASK_UUID \
  --worker-id LAST_WORKER_UUID --reason "Confirmed process exit; retry diagnostic probe"
uv run --package lexinform-web python web/manage.py web_worker --batch
uv run --package lexinform-web python web/manage.py prune_db_task_results --min-age-days 14
```

Recovery checks the last claim, accepts only failed/running probes and allows two retries.
It atomically records the reason, previous state/error and worker ID in `TaskRecovery`.
The audit survives task-result pruning. Unknown task functions cannot be retried here;
translation requests will need their own durable-intent reconciliation in WEB-09.1.

Locally the lock is `web/.worker.lock`. In production, `LEXINFORM_WEB_WORKER_LOCK_PATH`
is required and must point to **one persistent shared file on the single host**, mounted
at the same path in old/new worker and recovery containers. Do not delete/replace the file
or put it inside a release image. This lock does not coordinate multiple hosts; scaling to
multiple hosts requires a different fencing protocol before enabling additional workers.

## Permanent matter identities

`matters.services.identify` persists a normalized `SourceKey` and returns the existing Matter
on repeat. An explicit `continues` UUID adds another lifecycle identity; it does not silently
merge an identity already assigned elsewhere. `relate` records joint, alternative or later
continuation relationships without merging initiatives. Use these services for writes so
all identity-graph mutations share the PostgreSQL transaction advisory lock.

Editorial `merge` and `split` require an active staff user with `matters.reconcile_matter`,
a nonempty reason and a Clock. They retain original UUIDs and append a MatterDecision.
`split(..., restore_id=...)` can undo a mistaken merge while restoring the old public ID.
`canonical_matter` and `matter_for_path` resolve existing IDs/legacy paths; they do not decide
whether a reader may see the result. Public views must still enforce visibility in WEB-07b.

## Snapshot restore boundary

`ingestion.process.restore_in_process(raw_dump)` runs the SQL restore in a child process
and yields a `RestoredSnapshot` with a read-only SQLite connection. Use it as a context manager;
the connection and temporary normalized database are valid only inside that context. The child
receives no inherited environment, runs from a private temporary directory and does not invoke
the bot pipeline. Invalid input, worker failure or timeout raises a sanitized `SnapshotError`.

Limits: 20 MiB input, 128 MiB database/output file, 10 CPU seconds, 30 seconds default wall time,
32 descriptors and no core dump. Linux additionally applies a 512 MiB address-space limit;
macOS relies on SQL/input/output limits and the deadline instead of an OS memory limit.

This is an internal building block, not an import command. It does not restrict the service
user's filesystem or network access. Production still requires a sandbox/container with no
network, secrets or production mounts before remote snapshots are accepted.

`ingestion.process.project_in_process(raw_dump, source_commit=sha, public_channel="@readers")`
restores and projects the snapshot inside the same resource-bounded child, returning a validated
`ImportDocumentV1`. The SHA must come from pinned acquisition; the parent checks it and the input
hash against the returned origin. Output is limited to 20 MiB. Omitting `public_channel` exports
no Telegram links. Only sent messages from that exact public username are included. Malformed
rows, inconsistent embedded identities and dangling graph references reject the whole document.
Applied analysis, staged-analysis presence and pending batch work remain separate. This boundary
does not write PostgreSQL or accept a snapshot; generation activation is not wired yet.

`ingestion.acquisition.acquire_snapshot(source, load_previous)` holds a separate PostgreSQL
session lock before reading the last accepted `SnapshotRef` and fetching. Keep the context
open through restore and activation. It yields `already_running`, `unchanged`, or `ready`
with the pinned reference and raw bytes. Acquisition never updates the accepted pointer;
activation must still compare-and-swap that pointer in its transaction, even if the lock
connection is lost. Always use this context around `GitSnapshotSource.fetch` in the importer.

`GitSnapshotSource` takes a credential-free public HTTPS URL and a dedicated persistent bare
cache path. A `Path` remote is reserved for local fixtures. It rejects rollback/divergence,
shallow history, missing accepted commits and oversized dumps. It runs Git without inherited
configuration, hooks or credentials, with a 60-second deadline per operation. Transport helpers
are killed on timeout. The 20 MiB blob limit does not bound total Git history; deployment must
provide a disk quota and cache maintenance. There is no import timer or production command yet.
