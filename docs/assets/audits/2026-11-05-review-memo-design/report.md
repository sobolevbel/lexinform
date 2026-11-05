# J246 qualification design: review memo compaction

Design only, 5 November 2026. Writer stays **OFF**. No runtime, retention, body backend, production processing, plan checkbox or source-data changes occur. This packet contains metadata only; no source text, raw context, answer or database is copied.

## Proven collision boundary

`ReviewContext.memo_key` sorts a set of `(role, normalized_sha256)` and omits raw text SHA, URL, identity and document ordering. On the existing typed four-document commission context, URL-only change, reversed order and trailing whitespace all preserve memo_key but change exact ReviewInputReference. These are technical controls, not natural legal labels.

ReviewInputReference already retains every ReviewContext field and11of12 ReviewDocument fields; only text is absent. Original ordered descriptors plus caller text corroborated by original raw SHA reconstruct the exact old returned context for URL/order controls (4of4 bodies). Original IDs/URLs/metadata/order and citation addressing can stay unchanged. The whitespace control exposes only3of4 original raw bodies; one original remains unavailable. Normalized identity does not supply its exact old bytes.

## Why bounded reversible layout cannot cover all cases

Native text-v2 removes decorated numeric/dash lines, collapses Unicode whitespace and strips edges. It is not injective: removed digit/dash tokens, whitespace code points, lengths and positions cannot be recovered from the digest. A lossless residual may grow with the whole original document; no fixed marker budget guarantees it. Duplicate documents collapse in the memo-key set. Original descriptors preserve cardinality/order, but a caller may retain only one of several raw variants sharing the same normalized identity.

Pinned b9f86131…fc485 contains3legacy review contexts/6documents,465475UTF8 text bytes. Canonical forms total458900B: net6575B removed/replaced,8034noncanonical whitespace runs and3decorated numeric lines/21B. All6digests match known text-v2;0of6 original texts are already canonical and0contexts satisfy the canonical-original profile. All3memo rows lack retained computation parents, so natural eligible compaction remains0. Source DB SHA stays unchanged.

## Minimum sufficient optional subset

A body-free forward-safe subset exists when **every original body is already exactly canonical under a pinned known normalization profile**. For a valid same-key caller, recompute canonical bodies and demand their SHA256 equal the original raw SHA. Restore original descriptors/order, then recheck the entire recorded input and memo policy key. Identical raw duplicates can reuse one corroborated body while preserving their saved multiplicity; no guessed whitespace or source identity is admitted. Unknown profiles/sentinels, missing original bytes, noncanonical originals or unsupported projections must remain inline at their original save. This subset currently has no positive natural case here and does not justify generic emission.

Canonical rehydration should use a future explicit reader profile/version, not silently broaden version1's frozen exact-caller semantics. It also needs a pinned hash of the original input reference: record hash alone does not explicitly bind all metadata that would be restored. Reader deployment/qualification must precede any producer. These are technical prerequisites for a future producer.

Generic writer remains blocked. Preserve existing inline rows and first-record upsert behavior; do not turn a known pointer error into an extra paid call. DA6.3 stays open. A different independent integrity task can proceed without forcing new storage.

[Evidence](evidence.json); [exact code and fixture sources](source-pins.json).


## Local rerun

Use the pinned isolated source checkout and exact checkpointed b9 DB. The recipe checks all source pins, opens SQLite with mode=ro&immutable=1, emits metadata only and compares every measured leaf with evidence.json. No model/provider/network calls or source writes occur.

```bash
PYTHONPATH=/private/tmp/lexinform-j245-review-memo-reader/src:/private/tmp/lexinform-j245-review-memo-reader \
/Users/sobaleu/.codex/worktrees/doc-note-links/lexinform/.venv/bin/python \
out/corpus/j246-review-memo-design/recipe.py \
  --source-root /private/tmp/lexinform-j245-review-memo-reader \
  --database /Users/sobaleu/.codex/worktrees/doc-note-links/lexinform/out/corpus/j240-timing-writer/root-qa/final/fresh-copy.db
```

Published [qualification recipe](recipe.md) and [rerun proof](rerun.json); no raw context or database is included.
