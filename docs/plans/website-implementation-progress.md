# Website implementation progress

This log tracks completed, verified slices of the lexinform.pl implementation plan. The task
breakdown follows [section 25.15](../website/delivery.md#2515-порядок-реализации-и-границы-небольших-изменений); a task is marked complete only
when its acceptance result and checks are recorded here.

## Current position

- Started: 2026-09-16
- Active task: WEB-07a.1 — observation mode and freshness in the bot
- Next task: WEB-07a.2 — watchers without a Telegram thread
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
| WEB-02b | Complete | Editorial revision/translation, staff MFA and real PostgreSQL worker recovery verified |
| WEB-02b.1 | Complete | Allauth email login, mandatory TOTP/recovery, protected Wagtail and PostgreSQL scenarios |
| WEB-02b.2 | Complete | Transactional enqueue, SIGTERM/SIGKILL, fenced explicit probe recovery, retry and retention |
| WEB-01r | Complete | Pinned v34 state, 265 validated bills, 113 candidates; v2 fixture and reproducible audit |
| WEB-03 | Complete | Permanent identities and versioned import contract implemented |
| WEB-03.1 | Complete | UUIDs, scoped natural keys, relations, audited merge/split and stable URL resolution |
| WEB-03.2 | Complete | Explicit ImportDocumentV1 allowlist, pure state projection, graph validation and legacy/pending fixtures |
| WEB-04a | Not started | — |
| WEB-04b | Not started | — |
| WEB-05a | Not started | — |
| WEB-05b | Not started | — |
| WEB-05c | Not started | — |
| WEB-06 | In progress | Restore, acquisition, projection, activation and maintenance implemented; the deployment sandbox and timer remain (WEB-05a) |
| WEB-06.3 | Complete | `import_state` command, `ImportRun` report, 20% corpus guard, reconciliation queue, retention and audited rebaseline |
| WEB-07a | Not started | — |
| WEB-07b | Not started | — |
| WEB-08 | Not started | — |
| WEB-09 | Not started | — |
| WEB-10 | Not started | — |
| WEB-11a | Not started | — |
| WEB-11b | Not started | — |

## Work log

### 2026-09-27 — WEB-06.3: import maintenance

Added `ingestion.maintenance.run_import` and the `import_state` management command. Every call
writes an `ImportRun` (status, generation, error kind, pruned count), failures included, and the
command prints the report as JSON and exits non-zero on a refused snapshot.

`ImportIssue` is the operator's reconciliation queue: one open issue per key (partial unique
index). A refused snapshot opens one per kind — history, corpus drop, reconciliation, invalid —
and repeats only refresh it; the next accepted snapshot closes them. A row missing from the dump
opens an `absent` issue for its identity and keeps its matter and last facts; the issue closes
when the row returns. `resolve_import_issue` lists and closes issues with an operator and reason.

The corpus guard refuses an activation whose analysed candidates fell by more than 20%; it runs
at the end of the activation transaction, so structural errors still report as such. An operator
with `matters.reconcile_matter` accepts the drop with `--accept-drop` and a reason. Rewritten
history waits for `--rebaseline SHA`, which skips the ancestry check for that pinned commit only
and still refuses a branch that has moved on. Retention keeps the active generation, the newest
ten and anything younger than seven days; older generations lose snapshots and facts
(`pruned_at`), while the generation row and every event revision stay.

Test builders moved to `web/tests/import_builders.py` (`tests` is on the pytest and mypy paths).
Tests cover the guard and its override, a small drop, absent rows opened once and closed on
return, retention, issue recording on refusal, override permissions, a rewritten local Git
history with a wrong and then an audited rebaseline, and the two commands end to end. Full web
gate passed (205 tests, strict mypy, Ruff, Django checks and migration drift). Not done: the
five-minute timer and the network/filesystem sandbox of the deployment (WEB-05a).

### 2026-09-27 — WEB-06.2: import generations and atomic activation

Added the `ingestion` Django app with `ImportGeneration`, the singleton `ActiveImport`,
per-generation `SourceSnapshot` and `MatterFacts`, and permanent `MatterEvent` with
append-only `EventRevision`. `activation.activate` writes a whole generation in one
transaction under the identity-graph lock and a row lock on the pointer: the same SHA/hash
returns `unchanged`, a pointer other than the expected previous one raises
`StaleActivationError`, and nothing is written. A generation row exists only if its
activation committed, so a failed generation is never visible.

Lifecycle chains (RCL/RPW → druk) resolve to the Matter any member already has; a chain
spanning two matters raises `ReconciliationRequiredError` without partial writes. Joint and
alternative prints stay separate matters with relations. Facts are written for the canonical
matter, so editorial merges and visibility survive imports. `content_updated_at` carries over
while the bill's semantic hash (which ignores the analysis check time) is unchanged. An event
gets a new revision only when its content changes; `selectors.events_at` reads the newest
revision up to a pinned generation. A row missing from the new dump is counted in
`absent_identities`; its matter and older facts remain. `import_state` wires acquisition,
bounded projection and activation, passing the pointer read under the import lock as the
expected previous value.

Tests cover repeat, freshness vs content, stale/old SHA, failure before commit and retry after
commit, lifecycle linking, joint prints, editorial merge/visibility, a chain across two matters,
event revisions per generation, a missing row, two concurrent activations and an end-to-end
import from a local Git remote. Full web gate passed (196 tests, strict mypy, Ruff, Django
checks and migration drift). Not done here: the management command, report, corpus-drop guard,
reconciliation queue and retention (WEB-06.3); HTTP views that pin the generation (WEB-07b).

### 2026-09-27 — WEB-06.1d: restored rows and bounded contract projection

Added a read-only snapshot adapter for the existing ImportDocumentV1 projection. It checks
embedded bill and intent identities against SQLite keys and rejects invalid model data or
dangling graph references without exposing raw row contents in errors. History and applied
analysis survive; staged analysis remains a presence flag, and unconsumed batch work stays
separate. Consumed items do not reappear as pending. Telegram references require an explicit
public username and a sent message; operator payloads and errors are not exported.

`project_in_process` now performs restore, row decoding and projection in the resource-bounded
child. It returns only the validated contract, with a 20 MiB output limit and parent-side checks
of pinned SHA and source-byte hash. Temporary results are cleaned on success and failure.
There are no PostgreSQL writes, production fetches or bot pipeline calls. The deployment
filesystem/network sandbox remains open in WEB-06.1; generation activation remains WEB-06.2.

Moved the source-choice enum out of the ORM model module so the contract can load without
Django settings or app initialization. Existing model choices and migrations are unchanged.
Full web gate passed (179 tests, strict mypy, Ruff, Django checks and migration drift).
Five additional parent-result rejection cases then passed in the focused 13-test reader suite;
strict web mypy passed again. The default bot suite, bot mypy/Ruff/format, lock consistency,
strict documentation build and link/content checks passed. PostgreSQL checks used the local
test database with sandbox network access enabled; production was not accessed.

### 2026-09-27 — WEB-06.1c: pinned acquisition and import lock

Added a dedicated bare Git cache which fetches only `state`, pins its commit and reads
`lexinform.sql` by that SHA. Blob size is checked before reading (20 MiB maximum), and a
SHA-256 accompanies the bytes. An exact accepted SHA/hash returns `unchanged`; advancing
history requires ancestry from the accepted commit. Rollback, divergence, missing history,
shallow repositories, missing blobs and hash disagreement fail without returning old data.
Acquisition does not mark a snapshot accepted; that belongs to successful generation activation.

The `acquire_snapshot` context takes a nonblocking PostgreSQL session advisory lock before
reading the accepted pointer or fetching. A separate autocommit connection holds it across
the caller's work; contention returns `already_running` without touching Git. Closing the
session releases the lock on caller/fetch failure. No database transaction spans network work.
WEB-06.2 must still compare the previous active pointer atomically during activation, including
protection against a lost lock session; this lock alone does not replace that final check.

Git has a clean environment, no global/system config, interactive credentials or hooks, and
uses only credential-free HTTPS (explicit local Paths support fixtures). Each Git operation
has a 60-second default deadline; timeout kills the process group including transport helpers.
The 20 MiB limit covers the dump blob, not the complete Git object cache: cache disk quotas,
retention and the network/filesystem deployment sandbox remain operational prerequisites.

Real temporary Git repositories cover repeat, forward history, rollback/divergence, moving
remote after pinning, absent/oversized dumps, missing accepted commits and helper timeout.
PostgreSQL tests verify contention before fetch, lock scope and release on failures.
Full web gate passed (171 tests, strict mypy, Ruff, Django checks and migration drift).
The default bot suite, bot mypy/Ruff/format, lock consistency, strict documentation build
and link/content checks passed. No production fetch or site mutation was performed.

### 2026-09-27 — WEB-06.1b: resource-bounded restore process

Added `ingestion.process.restore_in_process`: SQL parsing and migration run in a separate
Python process with isolated Python flags, an empty environment, closed inherited descriptors
and a private temporary working directory. The default 30-second deadline kills and reaps the
child on timeout. The child applies CPU (10 seconds), file-size (128 MiB), descriptor (32) and
core-dump (disabled) limits before reading the dump. Linux additionally limits address space
to 512 MiB; macOS does not claim an OS memory limit.

The child writes the normalized database and a bounded receipt. The parent verifies source
hash, supported schema versions, database size and hash, then opens the result read-only for
the caller. Both files and the connection are removed/closed after success, validation failure,
timeout or a caller exception. Worker stderr is not propagated, so malformed SQL and private
dump contents do not appear in operator-facing exceptions. No shell or pipeline command runs.

This adds process and resource boundaries, not a network/filesystem security sandbox: the
child still runs as the service user. A deployment container without network, secrets or
production mounts remains required before accepting remote dumps. Git fetch, ancestry,
the import lock and projection into ImportDocumentV1 are also still pending in WEB-06.1.
The normalized database remains private input and is never a public/downloadable artifact.

Tests exercise real child processes for restore, SQL rejection, cleanup, environment options,
deadline and resource limits, plus missing, oversized and mismatched result receipts. The
Linux-only memory limit is covered conditionally in the same test, but was not exercised by
the local macOS run. Full web gate: 155 tests passed, strict mypy, Ruff, Django checks and
migration drift passed. The default bot suite, bot mypy/Ruff/format, lock consistency,
strict documentation build and link/content checks also passed.

### 2026-09-27 — WEB-06.1a: bounded SQL restore validation

Added a disposable in-memory restore boundary for bot-generated dumps up to 20 MiB.
The source schema is checked against the exact DDL produced by the append-only migration
ledger; input DDL is never executed. Only inserts into known tables pass to SQLite, with
an authorizer denying functions, subqueries and other operations. Missing schema objects,
duplicate declarations, missing transaction boundaries and unsupported versions fail closed.
Trusted migrations normalize versions 1–34; integrity and foreign keys are checked before
the result becomes query-only. The connection is closed when its context exits. SQLite page
and VM-step budgets bound database growth and execution; the dump hash and both schema
versions accompany the restored connection.

This is a library boundary, not a runnable importer. WEB-06.1 remains active: a restricted
process/container with wall-clock and OS resource limits, Git acquisition by pinned SHA,
ancestry verification and the PostgreSQL lock before fetch still need implementation. The
in-memory restore does not itself provide OS isolation, authenticate provenance, project
rows into ImportDocumentV1 or change website data. Exact DDL matching intentionally rejects
alternative SQL exports even if they appear equivalent to the bot schema.

Regression coverage includes every supported source schema, an actual repository dump with
a bill and applied analysis, multiline SQL-looking text, oversized/truncated input, modified
schema, dangling foreign keys and disallowed SQL. Full web gate passed: 139 tests, strict
mypy, Ruff, Django checks and migration drift. The default bot suite, bot mypy and Ruff
check/format passed. Documentation strict build and link/content validation passed.

### 2026-09-27 — WEB-03.2: versioned import contract

Added frozen, extra-forbidden ImportDocumentV1 models and a pure projection of validated
state models. Raw facts, processed baseline, applied analysis, batch metadata, history,
editorial visibility and explicitly allowed public Telegram links remain separate. Staged
analysis and unconsumed batch answers never become the current explanation. Memo, requests,
prompts, technical channels, raw errors and delivery snapshots do not cross the allowlist.
Source schemas 1–34 require prior isolated migration to normalized v34; unknown schemas and
contract versions fail. Snapshot acquisition/restoration remains WEB-06.1.

The graph rejects duplicate identities/events/work keys, dangling references, ambiguous
lifecycle targets, cycles and mismatched coverage. Wykaz identity uses the display number,
first publication timestamp and normalized entry URL independently of Sejm term. The evidence
is preserved; changed evidence with a matching display alias requires reconciliation in
WEB-06.2, not an automatic merge. See the [mapping and limitations](../website/data.md#реализованный-контракт-web-032-27-сентября-2026).

Twenty contract cases cover legacy missing provenance, negative explanations, current versus
processed stages, ready results, unknown intents, links, hidden/linked/joint bills, rollover,
missing evidence, schema/field rejection and malformed graphs. Projected all 265 bills from
the pinned WEB-01r dump in a temporary database: 115 applied analyses, 113 analyzed candidates
and 22 lifecycle/joint relations. No pipeline, LLM, delivery or production mutation ran.

Verification: 82 web tests on PostgreSQL 17, 1623 default bot tests, strict mypy for both
packages, Ruff/format, Django checks/migration drift and strict docs build/link checks pass.
The website is not deployed; this closes the contract slice, not the snapshot importer.

### 2026-09-27 — WEB-03.1: permanent identities and editorial reconciliation

Added PostgreSQL Matter, SourceIdentity, MatterRelation, MatterDecision and PublicAlias models.
Sejm identities require a term; RCL stays global and RPW retains the full number/year. Wykaz
display numbers alone are rejected; mapping the state evidence to a stable register entry key
is part of WEB-03.2. Repeating discovery returns the same UUID. An explicit source lifecycle
can attach Wykaz/RCL/RPW/druk identities to one matter; an identity already owned elsewhere
requires an audited merge. Joint/alternative relations preserve separate initiatives and
continuation can link a new submission after rollover.

Merge/split require a separate `reconcile_matter` permission, a reason and a supplied Clock.
They preserve original rows/UUIDs, record actor/time/before/after atomically, flatten merge
targets and allow restoration of an earlier public UUID. PostgreSQL natural-key constraints
and a transaction advisory lock protect concurrent discovery and opposite merges. New matters
and new split targets start as drafts; merging does not publish or overwrite visibility.
Stable `/b/<uuid>` and stored legacy paths resolve through the same selector after merge/split.
Public HTTP routing and localized card rendering remain WEB-07b.1.

Thirteen PostgreSQL scenarios cover the source lifecycle, rollover, joint groups, collisions,
permissions, old paths, rollback, partial-restore rejection, four concurrent discoveries and
two opposite merges. The root package now exposes its existing types through `py.typed`,
allowing web to reuse the shared Clock protocol without duplicate stubs or relaxed mypy.
Verification: all 62 web tests, strict web/bot typing, full default bot tests, Ruff/format,
Django checks/migration drift and strict documentation build/link checks pass locally.

### 2026-09-27 — WEB-01r: current pinned corpus audit

Audited state `beea36293a76c72d4fdaea544b3b0cdd2eb05c8b` in a temporary SQLite copy:
schema v34, integrity OK, no foreign-key violations, 265 Bill and 115 AnalysisRecord
validated. The corpus has 113 candidates, five linked aliases and five joint groups.
Only 27 candidates have Telegram cards; 100 lack analysis source-check timestamps.
The snapshot has no pending batch, so synthetic pending/unknown contract fixtures remain
required. Current 1039/1040 are analyzed again; do not carry v1 visibility as current truth.

Saved `tests/fixtures/website/state_audit_v2.json` and `tools/audit_website_state.py`;
v1 is unchanged. Details and reproduction are in the
[audit report](../reviews/2026-09-27-website-state.md). No pipeline, paid call, delivery
or production mutation occurred. This confirms import inputs, not public-site readiness.

### 2026-09-27 — WEB-02b.2 and reconciliation of the earlier stash

Reviewed the full website stash `cf31589f51270a288ad1fd63cb97fcd572fc122c` against
`76f81eb` (MFA) and the owner's temporary worker commit `101e86e`. Its earlier MFA/worker
implementation duplicates the current work and provides weaker guarantees: no MFA expiry
or authenticator ownership check, and unrestricted task requeue without a live-worker lock.
Those implementations, old dependency/stub versions, whitespace-only Compose changes and
the old completion claim are discarded. Retained the web-specific architecture guidance as
`web/AGENTS.md`, updated its documentation pointers, added bulk-upload MFA coverage and
carried all test database connection settings into worker subprocesses.

The current backend is locked at django-tasks-db 0.13.0. Real PostgreSQL worker processes
prove transactional enqueue (including invisibility before commit), idempotent probe execution,
SIGTERM drain, SIGKILL leaving a RUNNING row, restart without implicit recovery, refusal to
steal old-but-live work, explicit retry with audit, retry limit and safe result retention.
Strict mypy passes without backend stubs. Production settings require a shared persistent
worker lock path. The single-host guarantee and recovery steps are in `web/README.md`.

This closes the dependency spike, not the future translation dispatcher. Manual recovery is
restricted to the idempotent probe; uncertain paid tasks require WEB-09.1's durable requests.
No production worker or scheduler was changed. The temporary commit is preserved in history.
Verification: 49 web tests on PostgreSQL 17, strict web/bot mypy, full default bot suite,
Ruff/format, Django checks and migration drift, strict documentation build/link check all pass.

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
