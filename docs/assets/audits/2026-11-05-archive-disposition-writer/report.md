# Native archive member decision writer

The native loader records compact reasons for actual archive-member selection and rejection,
joined to the exact invocation's physical root, original ordinal path and extracted slot.
A private cache-entry sidecar preserves that association on reuse after intervening reads.
Public read overrides remain effective; metadata construction, capture and enrichment failures
preserve ordinary selection. Cancellation and source failures retain their existing contracts.

Only metadata is retained: no document bodies, new raw storage or backend decision. The section
is limited to 1 KiB, eight rows and four nested ordinals; the complete candidate trace remains
limited to 4 KiB. Canonical retention priority v1 is selected main, selected annexes, selected
support, remaining policy decisions, then physical walk. Each group preserves original slot
order. This priority changes only new diagnostic metadata, not extraction or selection order.
The count and digest cover the entire validated stream, including rows omitted from storage.

Capture and validation bookkeeping each have a 128 KiB canonical-byte budget; these are not
heap-memory ceilings. The per-selection accumulator retains primitive rows rather than losing
candidates' physical packets. Exceeding the validation budget produces explicit unknown
coverage without a count or digest certifying an unchecked tail. Failed roots remain walk-only.

The frozen producer-OFF raw driver and baseline stay byte-exact. A separately versioned
qualification decoder handles Deflate64 ordinal payloads now exposed by retained policy rows.
It checks actual member bytes independently of production selection. Raw replay compares old
selection, text, parts, source capture, IO/cache order and non-recipe physical metadata;
code-origin recipe identities are pinned separately rather than falsely claimed equal.

The raw cohort contains 16 ZIPs (14,377,628 bytes), 17 natural requests and four synthetic roots.
It excludes 1,787 other indexed ZIPs. Cached RCL replay covers 4,276 stage cases as of
2026-10-17, with 7,599 read inputs and 1,383 missing text inputs; population coverage is false.
Missing historical bytes are not replaced by current downloads or guessed from nearby files.
Neither replay supplies independent legal gold labels for policy reasons or omitted rows.

The storage qualification uses public source capture and synthetic ownership, deduplicates
repeated writes, restores the SQL dump and runs the doctor. Its byte delta is a technical
fixture measurement, not a production growth forecast. Existing eight legacy traces remain
byte/identity compatible on the pinned historical state; no provenance is backfilled.

Excluded paths remain explicit: pre-observation singleton failures, failures before an
addressable member, missing or partial physical facts, auxiliary roots outside the primary
outer candidate indices, legacy/custom/persisted-cache results, and internal Word/ODT ZIPs.
Unknown bytes never become an inferred unsupported-format verdict. DA4.1 and DA4.4 remain
partial; this stage adds prospective native diagnostics, not complete decision provenance.

The final root replay checks 78 retained rows, including 66 policy rows, with no unknown
associations among retained rows. Natural outcomes retain 24 main, 10 annex, 18 support,
four non-bill and two unknown-role reasons; these describe executed policy, not legal truth.
Both natural and synthetic legacy cores match. The storage fixture adds 30,511 SQL bytes and
6,142 gzip bytes. All pre-existing source pins from cached replay match the final freeze;
only the independent qualification tool, its units and its mypy target were added afterward.

Recheck using the cohort and baseline linked in J222:

```bash
uv run --frozen python -m tools.replay_archive_priority \
  --corpus /path/to/lexinform-corpus \
  --cohort docs/assets/audits/2026-11-04-archive-replay-baseline/cohort.json \
  --baseline /path/to/uncompressed/J222/baseline.json \
  --output /tmp/new-archive-replay.json
uv run --frozen python -m tools.qualify_archive_storage \
  --corpus /path/to/lexinform-corpus \
  --cohort docs/assets/audits/2026-11-04-archive-replay-baseline/cohort.json \
  --output /tmp/new-archive-storage.json
```

[Qualification](qualification.json), [corpus summary](corpus-proof.json),
[raw replay](raw-replay.json.gz), [storage](storage.json.gz),
[legacy proof](legacy-proof.json), [source pins](source-pins.json),
[bounded mechanism measurements](bounds.json), [independent review](peer-review.json),
[previous reader release](previous-release.json).

Actual writer release: [exact CI and relay verification](release.json), commit3a7d0667.
