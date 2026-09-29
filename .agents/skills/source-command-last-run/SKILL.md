---
name: "source-command-last-run"
description: "Аудировать последние прогоны на копии production state и найти потерянные события"
---

# source-command-last-run

Use for the migrated `last-run` command. Restore the `state` dump into `/tmp` and inspect it
read-only; never run `lexinform run` against that database.

Take the number of runs from the arguments, only the latest one by default. Compare each
selected run's report with rows created or changed in `bills`, `publications`, and
`runs`. Reconcile discovery, prefilter, analysis, publication, cost, and phase timing counters.
Investigate every permanent skip, exhausted retry, stale pending publication, and score-qualified
analysis lacking a sent card. Render affected `show`/`preview` output and verify the reader's
action, next step, dates, stage, and links against the source.

Read the run's own log (`gh run view <id> --log`), not only its report, for what does not look
like an ordinary day: "new" objects dated years back, a block of uniform rows from one applicant
or with one modification time, phase volumes far from the neighbouring runs, the same warning
every run, a warning about a bill no counter mentions, a phase that did nothing when it had work.
Carry each one to a verdict: the source behaves this way and the bot was right, or a scenario in
which it ends in a false card or a lost bill — which is a finding.

Lead with what readers actually received. Report findings by severity with bill number, evidence,
code location where relevant, whether data is irrecoverable, and the exact repair command. Do not
make fixes.
