# Website implementation progress

This log tracks completed, verified slices of the lexinform.pl implementation plan. The task
breakdown follows section 25.15 of `website-development-plan.md`; a task is marked complete only
when its acceptance result and checks are recorded here.

## Current position

- Started: 2026-09-16
- Active task: WEB-02b — complex dependency spike
- Next task: WEB-03 — identity model and import contract
- Release target: A — public library in five languages

## Implementation rules

- HTTP views, task functions and management commands remain thin entry points.
- Application services orchestrate use cases; selectors own non-trivial ORM reads.
- Repositories and ports isolate external state or a persistence boundary that tests genuinely
  replace. No generic repository layer is added around straightforward Django ORM operations.
- Domain decisions stay out of templates and framework callbacks. Dependencies point from entry
  points to services and from services to explicit protocols or pure models.
- Prefer small, typed abstractions with one reason to change. Do not add a layer until it removes
  duplication, isolates an external system or makes a meaningful scenario test possible.
- Local and production container names use the `lexinform-web` / `lexinform-db` convention.

## Task status

| Task | Status | Result |
| --- | --- | --- |
| WEB-01 | Complete | Pinned state audited; launch corpus and v1 audit fixture recorded |
| WEB-02a | Complete | Workspace, Django/Wagtail skeleton, PostgreSQL and first User migration |
| WEB-02b | Not started | — |
| WEB-03 | Not started | — |
| WEB-04a | Not started | — |
| WEB-04b | Not started | — |
| WEB-05a | Not started | — |
| WEB-05b | Not started | — |
| WEB-05c | Not started | — |
| WEB-06 | Not started | — |
| WEB-07a | Not started | — |
| WEB-07b | Not started | — |
| WEB-08 | Not started | — |
| WEB-09 | Not started | — |
| WEB-10 | Not started | — |
| WEB-11a | Not started | — |
| WEB-11b | Not started | — |

## Work log

### 2026-09-16 — WEB-01 production-state audit

Audited `origin/state` at commit `b2c4893e83d689a58be1c0c4f666cbcc3e8345bb` without changing
production. The 1,811,553-byte dump has SHA-256
`1e6df1f97e871c45c36e279e3905a4b4c7c0a031971303eefc64d86048c6e48a` and SQLite schema v22.
Restoring it and applying the append-only local migration to v23 succeeds; `integrity_check` is
`ok`, and all 193 bill rows and 97 stored analyses validate against the current Pydantic models.

The launch corpus is 93 primary candidate rows with `status = analyzed`: 55 Sejm prints, 35 RCL
projects and 3 wykaz entries. Of these, 38 are relevant according to the analysis, 55 are explicit
negative explanations, and 20 score at least 3. This confirms that the website corpus must not be
derived from Telegram publications: only 21 candidates have a sent `new_bill` or `joint_bill`
delivery with a message ID.

Identity data cannot be reduced to the 93 candidate rows. Four `linked` rows exist; three are
aliases of candidates (one RCL identity and two RPW identities), while the fourth points to print
3100, which is still `text_prefilter_pending`. Joint consideration forms four groups of 2, 3, 2
and 4 prints. Nine analyzed rows participate in those groups; two additional group members were
skipped by the text prefilter. The importer therefore needs all identity/link rows even when only
analyzed matters are publicly visible.

History and freshness are incomplete by design in the current state. There are 9 observed status
changes across 5 candidate matters. All 93 candidates lack `analysis.source_checked_at`; 67 lack a
normalized-text hash and 53 lack an analysis source URL. `bills.last_checked_at` is not sufficient
to claim source/aspect freshness. WEB-07a remains a launch dependency.

Two analyzed records (prints 1039 and 1040) now have `status = skipped_prefilter` because the
operator silenced them after publication. They are excluded from the 93-candidate corpus, but
their analysis and Telegram references must remain importable so an editorial visibility decision
and historical redirect do not destroy data.

The machine-readable audit fixture is
`tests/fixtures/website/state_audit_v1.json`. It records counts and representative relationship
cases for WEB-03 and WEB-06; it is evidence from one pinned state commit, not a permanent launch
threshold.

Checks performed:

- SQLite `integrity_check`: passed.
- Restore schema v22 and migrate to current v23: passed.
- Parse every bill and stored analysis with current models: passed.
- Candidate, alias, joint-group, history, publication and freshness counts: recorded in the v1
  fixture.

### 2026-09-16 — WEB-02a workspace and web skeleton

Added `web/` as the `lexinform-web` member of the root uv workspace. The lock now fixes Django
6.1.1, Wagtail 8.0.0, psycopg 3.3.5 and django-stubs 6.1.1 while preserving mypy 2.3.1 for the
existing bot gate. The website package depends on the root `lexinform` package through the
workspace boundary; the root package has no dependency on Django.

The initial project includes split local/test/production settings, five configured locales,
Wagtail's PostgreSQL search backend, admin/documents/i18n routes and a production WSGI entry point.
Production has no fallback for its secret key, base URL, database host, database name, database
user or database password.

Created the custom `accounts.User` in the first web migration. Staff emails are normalized and
case-insensitively unique at the database level; interface language is constrained to the same
five-language definition used by Django and Wagtail. Added narrow local typing boundaries for the
untyped Wagtail/modelcluster/treebeard modules reached by the Django mypy plugin instead of
weakening strict mode for the web package.

Added a local PostgreSQL 17.6 Compose service named `lexinform-db`, a `web_doctor` command that
checks framework versions, PostgreSQL major version and pending migrations, and a separate web CI
job backed by PostgreSQL 17. The future application containers will use the `lexinform-web` name.

The initially active Python was the free-threaded `cp314t` build, for which psycopg-binary has no
macOS wheel. The supported project environment now uses standard CPython 3.14.7 (`cp314`); all
locked web dependencies install there. Free-threaded Python is not a deployment target.

Checks performed:

- Full Wagtail and project migrations on PostgreSQL 17: passed.
- `web_doctor`: Django 6.1.1, Wagtail 8.0.0, PostgreSQL 17; no pending migrations.
- Web tests: 2 passed on PostgreSQL.
- Web strict mypy: 20 files, no issues.
- Ruff check and format check for web source, tests and stubs: passed.
- Existing bot tests: passed; existing strict mypy and Ruff gates: passed.
- `uv lock --check`: passed.
