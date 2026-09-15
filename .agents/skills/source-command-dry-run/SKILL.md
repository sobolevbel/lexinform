---
name: "source-command-dry-run"
description: "Безопасно выполнить полный dry run на копии production state и оценить сообщения читателю"
---

# source-command-dry-run

Use for the migrated `dry-run` command. Fetch and restore the `state` dump into `/tmp` with an
explicit temporary `LEXINFORM_DB_PATH`; never touch the configured database. Stop if restore
fails.

Run `lexinform run --dry-run --max-analyze 0` by default. Dry runs make real model calls, so lift
that limit only when the request explicitly asks to analyse, then cap it and report cost. Honour
requested `--since`, `--full-track`, and `--no-rcl` options. Treat RCL outages as findings, not a
reason to silently retry or alter code.

Report phase counters, costs, errors, and every would-be reader message verbatim. Rank reader
impact anomalies (wrong action, wrong next step, source outage, stuck record), finish with the
run's health, and do not modify code.
