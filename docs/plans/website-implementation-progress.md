# Website implementation progress

This log tracks completed, verified slices of the lexinform.pl implementation plan. The task
breakdown follows [section 25.15](../website/delivery.md#2515-порядок-реализации-и-границы-небольших-изменений); a task is marked complete only
when its acceptance result and checks are recorded here.

## Current position

- Started: 2026-09-16
- Active task: WEB-02b — complex dependency spike
- Next task: WEB-02b.2 — database worker and recovery
- Release target: A — public library in five languages
- Last code review: 2026-09-26, local commit `6cd680d`; no production verification
- Remaining implementation slices: [task list](../website/tasks.md)

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
| WEB-02b | In progress | Editorial revision/translation and staff MFA complete; task worker remains |
| WEB-02b.1 | Complete | Allauth email login, mandatory TOTP/recovery, protected Wagtail and PostgreSQL scenarios |
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

### 2026-09-26 — WEB-02b.1: staff login and MFA

Implemented on `feat/website-implementation`. Staff use one allauth email/password flow;
reader signup is closed and email verification is mandatory. Wagtail's legacy login,
logout and password-reset routes redirect to allauth. The whole admin prefix, including
preview, chooser and uploads, requires an active staff account and a recent MFA record
for an authenticator still belonging to that user. The proof expires after 12 hours.
An existing password-only session must enroll TOTP or reauthenticate. Self-service TOTP
removal is disabled; recovery codes remain available and are single-use. Password reset
does not remove MFA or automatically log the user in. Auth/admin responses use `no-store`.

Local and test mail use Django 6.1 `MAILERS`; no deprecated `EMAIL_BACKEND` setting was
added. Upstream allauth boundaries have narrow stubs; strict mypy remains enabled.
PostgreSQL tests cover first enrollment, invalid code, recovery reuse, password reset,
expired proof, removed device, nonstaff, superuser, editor permissions, CSRF and unsafe
redirects. Real Wagtail preview/chooser/upload forms are reachable only after MFA.

Checks: 38 web tests pass, strict web mypy passes. The commit gate also runs Django
checks/migration drift, web lint, bot tests/types/lint/format and the documentation checker.
This is local verification. Production SMTP, shared authentication rate-limit/replay cache,
TLS and secret management remain part of WEB-05; no production authentication was deployed.

### 2026-09-26 — plan revalidation against the local repository

The source still contains the WEB-02a skeleton and the editorial portion of WEB-02b. The
dependency list and settings have neither allauth MFA nor a database task backend; no identity,
ingestion, translation, search or feedback application exists yet. The two editorial tests cover
publication and translation/draft/preview; they do not establish staff MFA or worker recovery.
The web CI job exists, but deployment images, web release workflow and recovery runbooks do not.
This review inspected code and test definitions; the historical PostgreSQL test results below
were not rerun for this documentation review.

The root Makefile now supplies web sync/serve/test/lint/typecheck/format/check commands. The plan
retains just as the target command interface, with an explicit repository-wide migration task
(WEB-DX) instead of a second independent command catalog. Five configured language codes are
not five translated public versions: locale routes, UI catalogs and the content pipeline remain.

WEB-01 remains complete as a historical audit of its pinned snapshot. Its v1 fixture must remain
unchanged; a new audit of a freshly pinned state copy is required before finalizing WEB-03 and
again before launch. The current bot already has `ObservedProcess`, `BillPlan` and durable
delivery checkpoints, while `list_tracked` still requires a sent Telegram card. WEB-07a must
extend those boundaries for independent observation, not implement a second tracking pipeline.

The [remaining tasks](../website/tasks.md) clarify dependencies and acceptance criteria. No
implementation stage was completed by this review; release A remains the full five-language
library, with B/C and the bot's PostgreSQL migration deferred.

Checks for the documentation update: strict portal build and links/anchors/content validation,
default bot pytest suite, strict mypy, Ruff check/format and `git diff --check` passed. No browser
was available for visual navigation review; PostgreSQL web scenarios and production checks were
not rerun.

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

### 2026-09-16 — WEB-02b editorial prototype

Added the first two planned editorial types rather than disposable spike models. `GuidePage` is a
Wagtail page with a constrained StreamField; `Topic` is a translatable, revisioned, draft-aware and
previewable snippet. The topic model will be reused by catalog filtering later, while the guide
page grows into the full block set in WEB-08.

The scenario tests prove that structured guide content survives publication, a second locale keeps
the same `translation_key`, an unpublished manual edit does not change the live row, preview renders
the draft, and publishing that revision updates the live content. Preview carries `noindex` from
the first implementation.

Strict mypy remains enabled. Wagtail 8 does not publish PEP 561 metadata, so only the concrete
Wagtail boundaries used by the project have local stubs; the one Django-plugin error caused by
Wagtail's runtime reverse Page relations is disabled only for the two model modules and the
Wagtail model boundary.

Checks performed:

- Editorial migration generated and applied on PostgreSQL 17: passed.
- `makemigrations --check --dry-run`: no changes.
- Guide publication and topic translation/draft/preview scenarios: 2 passed.
- Web strict mypy: 27 files, no issues.
- Ruff check and format check: passed.

WEB-02b remains active: staff MFA and the database task worker failure/restart spike are not yet
implemented.
