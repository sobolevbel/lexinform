---
name: "source-command-last-run"
description: "Аудировать последние прогоны на копии production state и найти потерянные события"
---

# source-command-last-run

Use for the migrated `last-run` command. Restore the `state` dump into `/tmp` and inspect it
read-only; never run `lexinform run` against that database.

Compare each selected run's report with rows created or changed in `bills`, `publications`, and
`runs`. Reconcile discovery, prefilter, analysis, publication, cost, and phase timing counters.
Investigate every permanent skip, exhausted retry, stale pending publication, and score-qualified
analysis lacking a sent card. Render affected `show`/`preview` output and verify the reader's
action, next step, dates, stage, and links against the source.

Lead with what readers actually received. Report findings by severity with bill number, evidence,
code location where relevant, whether data is irrecoverable, and the exact repair command. Do not
make fixes.
