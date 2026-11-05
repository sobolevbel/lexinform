# Observed hosted checkpoints and one original card delivery

Twelve completed scheduled runs exposed GitHub timestamps for both state steps.
Restore took 4–7 seconds with a median of 6. The final dump-and-push step took
6–31 seconds with a median of 8. The step durations combine their command work and
any network activity within the step. In-run checkpoints have a separate scope.
[Run IDs and timestamps](hosted-checkpoints.json).

The last first-parent state checkpoints before eight UTC boundaries measured seven
complete days from 29 October to 5 November. The SQL dump grew from 20,378,124 to
25,815,220 bytes. Gzip grew from 4,112,280 to 5,208,734 bytes using mtime zero.
This is observed net growth of checkpoint contents, including compaction and
maintenance. It does not measure GitHub's billed storage. The same report pins
each state SHA and daily delta.

Of 442 saved finished-run reports, 27 carried typed read metrics and 13 carried
download diagnostics. The available metrics recorded 87 cache hits and 97 completed
download invocations. The diagnostic counters are loader invocations and can include
routing effects. They do not count HTTP wire attempts. [Saved read counters](document-reads.json).

The direct-primary delivery audit verified one original card send for Sejm druk 3281
on the immutable state copy. The retained source observation and selection, request,
paid result, application receipts and frozen SENT delivery agree. The report uses
the original frozen render's hash. Later edits and current Telegram content are outside
the audit. Four controls keep missing links and unrelated publication kinds explicit.
[Recorded cycle](primary-cycle.json), [controls](cycle-controls.json).

```sh
uv run python -m tools.primary_delivery_cycle --database snapshot.db \
  --publication-id 258 --output original-card-cycle.json
```

Restore and dump/push timing uses the named GitHub step metadata for the pinned run IDs.
Checkpoint sizes use the SQL blob at each pinned state SHA and gzip with mtime zero.

Publication INSERTs contributed 3,568,476 bytes to the 5,437,096-byte increase in SQL.
Physical metadata contributed 741,966 bytes. These are UTF-8 SQL statement lengths.
They do not attribute gzip bytes to individual tables. [Table contributions](checkpoint-tables.json).

The exact audit commit passed hosted CI and the relay transition, restart and timer
checks. [Release receipt](release.json).
