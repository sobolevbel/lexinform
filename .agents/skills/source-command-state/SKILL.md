---
name: "source-command-state"
description: "Проверить здоровье production state, публикации, ретраи и стоимость прогонов"
---

# source-command-state

Use for the migrated `state` command. Restore the `state`-branch dump into `/tmp` and remain
read-only. Default to the previous 14 days unless the request supplies another period.

Review run/cost summaries, GitHub Actions, bill statuses and errors, publication states, tracking
freshness, open entry-into-force work, and wykaz backlog. Treat stale `pending` as non-retriable,
check failed publication attempts, and distinguish a normal GitHub schedule delay from a missed
day.

Give a brief dated health summary, then ranked anomalies with the affected record and exact repair
command. If clean, say so without reciting counters.
