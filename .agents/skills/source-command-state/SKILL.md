---
name: "source-command-state"
description: "Проверить здоровье production state или диагностировать сохранённые результаты, очереди и публикации на копии SQLite"
---

# source-command-state

For a health review, inspect an isolated restored copy of the `state`-branch dump read-only. Default to
the previous 14 days unless the request supplies another period.

Review run/cost summaries, GitHub Actions, bill statuses and errors, publication states, tracking
freshness, open entry-into-force work, and wykaz backlog. Treat stale `pending` as non-retriable,
check failed publication attempts, and distinguish a normal GitHub schedule delay from a missed
day.

For a specific saved-record anomaly, choose the matching read-only doctor from
[`tools/README.md`](../../../tools/README.md#choosing-a-state-doctor). Use a checkpointed
copy with its connections closed and a fresh report path. Run
`make state-doctor DOCTOR=<profile> DATABASE=<copy.db> REPORT=<new.json>`. Read the report
status before interpreting findings. An unavailable census leaves the question open. Check
the profile's scope before proposing a repair.

Give a brief dated health summary, then ranked anomalies with the affected record and exact repair
command. If clean, say so without reciting counters.
