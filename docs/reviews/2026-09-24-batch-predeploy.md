# Secondary Batch predeployment verification

Статус: историческая проверка 24.09.2026. Текущие дефекты — в [реестре](../BUGS.md).

Follow-up: the [architectural audit](2026-09-24-secondary-batch-audit.md) found six additional
edge-case defects. The measurements below remain valid, but are not a blanket readiness verdict.

24 September 2026. Implementation tested: `31936fd`; production-state snapshot:
`e08805afa1d4300c6ef20ea01611e32ed927cc36`. No deployment or Telegram delivery.

## Local checks

- Full offline suite: 1,389 tests, including nine additional failure/recovery cases.
- Strict mypy: 193 files; Ruff lint and formatting pass.
- Coverage run: 92% combined line/branch coverage, 1,389 passed, nine integration tests
  deselected. It also reports 573 resource warnings about unclosed SQLite test connections;
  this fixture-cleanup issue was already visible before the new tests and remains unresolved.
- Additional scenarios exercise workers 1/4, two documents completing in different batches,
  configuration rollback, provider mismatch, failed memo/consumption and delivery checkpoints,
  dump/restore recovery, budget reservation release, and wrong result types.
- Existing scenarios cover timeout and late answers, urgent/manual/dry-run bypass, scanned
  supplements, amendments, watermark movement, and exactly-once publication/cost accounting.

## Read-only source integrations

Six live tests passed against Sejm, Orka and the government register. One old test initially
failed: it treated `599-s.pdf` as bill text. The API returns a government position; the model
already deliberately excludes it from `main_pdf`. The test now checks both that exclusion and
the actual attachment download. No production parsing behavior was changed.

## Production-copy dry runs

Restored the state dump into an isolated temporary SQLite database: schema v32, 261 bills,
`PRAGMA integrity_check = ok`. Both runs used `--dry-run --max-analyze 0`, an empty command inbox,
disabled Telegram log delivery, and batch kinds `analysis,reanalysis,joint,supplement`.

| Run | Tracked | New bills | Analyses | Updates/posts | Errors | LLM cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Incremental | 5 | 0 | 0 | 0 | 0 | $0 |
| `--full-track` | 23 | 0 | 0 | 0 | 0 | $0 |

The full run took 33 seconds; discovery inspected 782 government-register entries and 34 RCL
entries. There were **no would-be reader messages**. After both runs, the repository SQL dump
matched a separately restored and migrated baseline exactly.

This proves migration, wiring, live fetching and the unchanged-data path on this snapshot. It
does not exercise newly arrived secondary documents: this snapshot had no new changes and no
stored batches. Deferred delivery is exercised by the scenario tests below the live adapters.

## Real provider Batch requests

Submitted six small synthetic requests, one per secondary kind and provider, then collected
them through the real adapters. Every batch ended successfully with exactly one matching
custom ID, a typed answer, nonzero token usage and no item error. Models: Anthropic
`claude-opus-5-5`; OpenAI `gpt-5.1`. Total cost calculated from returned usage and repository
prices: **$0.026898375**; the pre-submission conservative reservation was $0.1352.

| Provider | Kind | Batch ID |
| --- | --- | --- |
| Anthropic | joint | `msgbatch_013yBpDEjNtuZW1dGN4cK8BJ` |
| Anthropic | amendments | `msgbatch_01FWDMchKkARRJxesHrPWt48` |
| Anthropic | supplement | `msgbatch_01RdS7rg4Ume4mntJjDU1t3V` |
| OpenAI | joint | `batch_6ab480f2d36081909734a7002025a9c9` |
| OpenAI | amendments | `batch_6ab480f4ace081909c3db01939f08c48` |
| OpenAI | supplement | `batch_6ab480f59a588190ad5817d8c8720428` |

Inputs described a fictional residence-application deadline changing from 14 to 30 days.
Answers were inspected, not only parsed. The joint fixture contained a conflicting old
`key_changes` field; Anthropic explicitly flagged the inconsistency. This smoke demonstrates
transport/schema compatibility, not a controlled evaluation of editorial quality. Live scan
payloads were not submitted; scan serialization and memo recovery have offline coverage.

Local raw evidence: `/tmp/lexinform-predeploy-fdaXx7/` contains `dry-run.log`, `full-track.log`,
`pytest.log`, `batch_smoke.py` and the resumable `batch-smoke.json` manifest with all answers.
These temporary files are not needed by production.

## Remaining deployment checks

- These local tests do not verify GitHub runner connectivity, installed VPS code or its timer,
  or actual Telegram delivery. No live inbox write test was run.
- The production snapshot contains zero completed batches. Six synthetic adapter tests do not
  satisfy the plan's production gate: at least ten completed batches, p90 collection latency
  at most two hours, and a healthy poller. Amendments therefore remain disabled in the workflow.
- Timeout fallback executes on the next pipeline run after the deadline; it is not a timer
  that independently guarantees delivery at exactly six hours.

No functional regression was found in the new implementation by these checks. Passing them
supports deploying the enabled kinds, with production latency/poller observation still required.
