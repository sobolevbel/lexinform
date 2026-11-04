# Saved LLM queue ages

The existing read-only work doctor reports LLM queue ages when an explicit observation
clock is supplied. Its new `llm_work_ages` sibling keeps the previous owner groups and
nonterminal counts unchanged. No clock means the previous report shape remains unchanged.

Queued/submitting intents use their recorded creation time. Unconsumed physical items
use the saved batch submission time and retain distinct batch/item identities across
retries. Exact intent/item overlap is counted once; conflicting ownership stays visible.
Missing schema, orphan batches and unknown statuses are explicit coverage gaps. Missing,
naive and future timestamps produce unknown ages rather than inferred times.

`waiting_provider` and `waiting_local_consumption` describe the saved current state.
Item ages in both groups are total age since submission. They do not measure how long
local consumption has waited: first provider completion time is not recorded, and later
poll timestamps cannot substitute for it.

```sh
uv run --frozen python tools/work_doctor.py \
  --database /path/to/isolated-state.db \
  --observed-at 2026-11-04T18:21:14.933484+00:00 \
  --output /path/to/new-work-report.json
```

The pinned schema65 snapshot has 57 batches, 122 consumed items and no submission intents.
It therefore proves compatibility and read-only behavior, with zero observed active LLM
phases. Tests cover active queues, physical retries, exact/current and legacy request
representations, conflicting ownership, incomplete schema and unknown clocks.

Independent review caught an initial assumption that current requests serialize a top-level
`call_kind`. Current `BatchRequest` stores `question.kind`; typed model decoding now owns
this compatibility boundary. The interrupted pre-fix local gate is preserved separately. An existing
observed-latency test also required the new opt-in sibling in its whitelist; its default
report and database-byte preservation assertions remain.

This extends DA10.3; queue reasons, hosted checkpoint latency and natural daily growth
remain open. No production processing, model calls, state mutation or scheduler is added.

[Qualification](qualification.json), [isolated state proof](fresh-state-proof.json).
