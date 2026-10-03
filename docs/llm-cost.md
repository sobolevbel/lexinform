# What the model costs and what bounds it

Extracted from `CLAUDE.md` on 2026-09-14: the price of a run, the guard rails and the measurements
behind them. Read it when changing `AnalysisService`, `pricing.py`,
`sections.excerpts`/`TextBudget` or any cost setting.

## LLM cost model (Sept 2026)

Opus 5 is $5/M input; output is ~1% of the bill. Since 2026-09-23 the analysis runs on Opus 5.5
($4/M input, $20/M output); the figures below were measured at Opus 5's price, so read them 20% lower. A government print is bill + uzasadnienie + OSR
(13-point form) + appendices (consultation report, tabela zgodności, draft regulations with their
own uzasadnienie/OSR), and the appendices are 55–80% of the text. `sections.trim_print` keeps the
bill, uzasadnienie and OSR points 1–4 (pages are separated by `\f` by the extractor and the kept
pages rejoined with it: the page is the unit every rule in `sections` works in). Long texts (≥
`triage_min_chars`) first get a triage on `sections.excerpts` (heads + windows around keyword hits)
by `llm_triage_model`; a confident "no" is stored as a non-relevant analysis with
`text_source="excerpts"`. Real numbers: druk 2695 (564k chars, irrelevant) cost $1.45 in full,
~$0.01 with the triage. The triage call runs without extended thinking (Haiku 4.5 rejects
`thinking: adaptive`; a classification does not need it). Synchronous Claude system prompts carry `cache_control`;
the analysis prompt alone is ~850 tokens, under the 1024-token minimum of a cache entry, but the
structured-output schema is part of the cached prefix, so the entry is ~2k tokens and does get read
(state dump of 2026-09-09: 23k cache-read tokens over 11 analyses). The run report's "cache read"
figure and `lexinform cost` show it; `lexinform runs` lists the recorded runs.

### Prompt caching policy — 25 September 2026

New Anthropic batch requests omit `cache_control`. Requests run concurrently with no guaranteed
cache hit: the two-request batch for 1039/1040 wrote 5,304 tokens and read none, adding $0.002652
over ordinary batch input. A one-hour write costs twice ordinary input, so even one write plus
one read costs more than two uncached prefixes. For our small, intermittent batches we use
ordinary batch input; synchronous Claude calls retain their five-minute cache. Existing saved
batch payloads keep their original parameters and reservations.

GPT-5.1 uses automatic caching without a cache-write surcharge. Both synchronous and batch
requests use a stable key derived from the system prompt and output schema name, shared across
bills, and request `prompt_cache_retention="24h"`. This improves opportunities for reuse without
guaranteeing a cache hit. Other models keep their provider-default retention.
OpenAI's `input_tokens` includes cached input; the adapter subtracts cached tokens before storing
the uncached category. Batch pricing halves both uncached and cached rates. Historical reports
are not rewritten. New batch reservations assume no paid cache writes and no cache hits.

Run reports show cache reads and writes across synchronous and batch calls, including zero reads
when a run only wrote cache entries. Sources:
[Anthropic batch caching](https://platform.claude.com/docs/en/build-with-claude/batch-processing#using-prompt-caching-with-message-batches),
[Anthropic cache pricing](https://platform.claude.com/docs/en/build-with-claude/prompt-caching),
[OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching).

**Every model call is written down** (`RunReport.llm_calls`: bill, kind — analysis, reanalysis,
triage, amendments, supplement, joint — model and tokens, recorded by `AnalysisService._charge`, which
every phase that asks the model goes through). One figure per run could not be accounted for
afterwards: the run of 2026-09-13 billed 316,767 input tokens with nothing to say which call made
them, and the report now names the three costliest.

Guard rails (`LEXINFORM_MAX_ANALYSIS_COST_USD`, default $2 per first analysis, estimated from the
text length at 2 chars/token before the call — verified against the real tokenizer on 13 Sept 2026,
five documents, 1.96–2.08; `LEXINFORM_MAX_RUN_COST_USD`, default $15 per run): **a text over the
per-bill limit is cut down to it, not refused.** The triage has already said the bill matters, and
`skipped_cost` left the reader with nothing and the operator with a `reset` to run by hand.
`_fit_to_budget` counts the real tokens, scales the characters by the overshoot the tokenizer
measured (so it lands inside the limit whatever the text tokenizes at, one re-count to confirm) and
rebuilds the text with `sections.excerpts` over the keyword hits — head of the bill, head of the
uzasadnienie, a window around every hit — marking it `truncated`. Under `_FIT_MIN_CHARS` (20k)
there is no document left and `skipped_cost` stands. A **scan** is still refused rather than cut
(its pages are substance from the first to the last), and for a re-analysis not even that: a bill
whose new text we decline to read must not keep a card describing the old one. **Every refusal
turns on `first`, and a re-analysis is never one** — not the scan, not a text with nothing left to
cut, and not an excerpt that counts over the limit the whole document was scaled to fit (the live
case: `excerpts` keeps keyword-dense provisions, which tokenize worse than the ratio the whole text
measured). `reanalyze_bill` is called from the tracking loop, whose per-bill `except` has nowhere
to put a refusal: the bill would fail on the same text every run, with no `skipped_cost` row to
`reset` and no `/unskip` to undo.

`TextBudget` is the outer cap only, and cuts the same keyword-aware way; the per-bill limit is what
binds. The cap gives the whole cap: each head takes a **quarter** of it, because `excerpts` keeps
both heads whatever it is asked for — at a half each they filled the budget before the first
keyword window was measured, so no hit was ever kept and a text with no "Uzasadnienie" heading came
back half the length allowed. What the windows leave unspent goes back to the head
(`excerpts(fill_head=True)`, which only a cap asks for: the triage digest is paid for by the
character, and a text whose keywords are few is one the cheap model should read less of, not more).
The analysis phase stops for the run once its spend reaches the per-run limit (a note in the
report, not an error; the rest waits for the next run). Re-analyses get the per-bill guard too, in
the same cut-to-fit shape — they had none until 2026-09-13, and the most expensive single call the
project has made is one (316,767 tokens, $1.58, an RCL package re-read). They count against the
**run's** budget as well and are held back once it is reached (`AnalysisService.start_run` /
`stopped`, a note in the report) — until 2026-09-13 the per-run limit bounded the analysis phase
alone, and the tracking phase's re-analyses, amendment summaries and supplement digests spent on
top of it with nothing watching: one re-analysis of the ETIAS package cost $1.63 in a run that
consulted no limit at all. A held text is not written down, so the next run offers the same
document again; what it costs is that the stage update of that run goes out without its «текст
обновился» note, and the card catches up when the refresher re-renders it.


## What a new text of an analysed bill costs (1 Oct 2026)

Historical measurements below describe the short-context comparator. The
[3 October integration](reviews/2026-10-03-law-comparison.md#внедрение-в-pipeline) versions
fingerprints and supplies both law bodies to change review. Its 60,000-character limit counts
that context; the old median token count and price do not describe the new prompts.

Until 2026-10-01 any difference of `text_sha256` and `law_sha256` from the stored analysis was a
full re-analysis. In three weeks of production that was about thirteen readings at a transition
between sources (RPW → druk, RCL → druk, the text after the third reading), and the model named a
change in one: druk 3150 alone, the 2027 budget at score 1, cost $1.63 + $1.72 on the overflow
model for the same act read twice (B71). Three rules now stand in front of the reading:

- **The druk of the submission already read is that text** (`_same_submission`): all 644
  RPW-to-druk pairs of term 10 were labelled by provenance in the original probe; the actual
  rule applies to BILL submissions only (see the recheck below).
- **A difference only in layout is free**, and **a small one is reviewed** by the triage model
  (`law_diff`, `_changes_settled`): the changed passages of the act and the card's description,
  ~2,000 input tokens at the median and 14k at the most, **≈$0.012 a call** on Sonnet 5 (38
  calls, $0.44, `out/92_review_calibration.json` in the corpus). Over the 468 RCL-to-druk pairs:
  98 equal digests and 9 noise-only (free), **304 reviewed** (≤15% of the act's words changed),
  57 read in full. In the calibration 15 of 25 RCL reviews and 10 of 13 noisy RPW pairs were a
  confident "not material"; every change that touched foreigners or was unclear was read.
- **A relevant bill more than a point under `min_score` with no card is observed without the
  model** (`ObservationBasis.BELOW_BAR`): no new text lifts it to a card.

What is still read in full is a large change, a scan on either side (except the RPW → druk rule),
and a review that is not sure — a wrong "material" costs one reading, a wrong "not material" would
leave the card describing a text that is gone, so the threshold leans the cheap way only when the
model is confident.

### Corpus recheck (3 Oct 2026)

An offline A/B replay compared `f7f994c` with `bcff188` over the exact 1,028 text pairs from
`checks/out/92_transitions.json`. The old implementation reproduced every saved decision,
ratio and diff size. No model calls were made. `law_digest` was unchanged on all 1,698 individual
texts: 1,623 SHA-256 values and 75 absent digests (`None`).

| Source pairs | Equal digest | Noise only | Review | Full: large change | Full: no act |
| --- | ---: | ---: | ---: | ---: | ---: |
| RPW without provenance, before and after | 507 | 7 | 13 | 4 | 29 |
| RCL before | 98 | 9 | 304 | 38 | 19 |
| RCL after | 98 | 6 | 308 | 37 | 19 |

Thus 618 of the previous 621 deterministic matches survived. Three unchanged RCL texts now
need a review: a grammatical `z`, an unfilled appendix-date marker, and table coefficients
misidentified as page numbers. These are recorded as [B79](BUGS.md#b79). Another RCL pair
crossed from full reading to review because removing footnote markers lowered its changed-word
ratio to 14.91%. No previously reviewed or fully read pair became an automatic match.
The old model calibration cannot establish the model's response to the changed prompts.

The corpus has another 84 RPW entries without readable source text and 130 linked entries
without the druk text; they are outside this text comparison. The separate provenance replay
covered all 644 rows labelled `provenance` by the old probe: 625 are BILL, 18 BILL_AMENDMENT,
and one DRAFT_RESOLUTION. Only the 625 BILL rows qualify. Across 10,304 combinations of these
metadata, document kind, text source and source URL, the original method and `SejmTextIdentity`
agreed completely. This checks the refactor; it is not a fresh semantic review of every scan.

## What a jointly considered print costs (14 Sept 2026)

Until 2026-09-14 a print whose group already held a card was not read at all: it got a reply
naming it and nothing more, and the saving was the point — druk 1933 had cost 305,132 input
tokens ($1.53) for an analysis that went out as such a reply. The reply now says how the print
differs from the ones the reader has read about, which is worth a reading, and the price of that
was measured over the whole term before it was decided.

Term 10 has **938 bill processes, 53 of them in 21 groups** of jointly considered prints (16
pairs, three triples, one four and one eight — the eight being the vetoed bills of 2026-03-27).
**Eighteen of the 53 pass the keyword prefilter**, and only **five groups** have more than one
candidate in them: in the other sixteen the partner never reaches the model under either rule.
Reading every non-card candidate of those five groups costs **$4.21** of Opus input over the
term, against the ≈$44 the term costs in full — **+10%**, before the triage, which applies to
these prints like any other except where the group already holds a card — as does the keyword
prefilter, and for the same reason: a print it drops beside one the channel has carded is, in all
**eight** groups of the term where that happens, the same bill by another applicant (druk 1426
the government's Kodeks pracy against the deputies' 1404, druk 316 the President's asystencja
osobista beside 1929 and 1933, druk 2530 the same rynek kryptoaktywów as 2529). Reading all of
them is **$0.71** for the term, so the whole change is ≈**$5** on ≈$44.

The comparison itself is not a second reading. `AnalysisService.compare_joint` sends the
channel's own description of each bill in the group — summary, key changes, whom it affects,
what changes in practice — and no text at all: a few thousand characters, about **a cent** a
reply, some **$0.05** over the term. Sending both texts instead was measured and rejected:
1929 + 1933 together are $1.95, which is inside the $2 per-bill guard only by accident, and the
question a reader asks is how this print differs from the card they have read, not from a
document they have not.


## Secondary batch calls — 23 September 2026 plan

The planning snapshot for 113 production runs on 7–23 September counted 34 analyses ($12.63),
4 reanalyses ($3.14), 61 triages ($1.30), 1 amendment summary ($0.09), 2 joint comparisons
($0.04), and no supplements. These are the plan's historical figures, not a new measurement.
The term-10 corpus estimate was about $13.5 for secondary calls, or $6.7 saved by batching over
one term; financial savings alone do not justify the feature.

The 28 September policy supersedes that plan's latency gate: all supported analysis calls,
including amendments, use batch. Only triage remains synchronous. Each model selects its provider;
GPT-5.1 context overflow is queued separately to Opus 5.5 and priced at its batch rate for the
per-bill guard. Manual and forced commands also queue work. Waiting time and urgency never enable
a synchronous fallback. Uncertain submissions require audited recovery. A confirmed secondary
failure is filed once more (a batch can expire or be cancelled) unless the provider refused its
context; a second failure uses the existing failure reporting path. This policy change provides no new production
latency or poller-health measurement.
