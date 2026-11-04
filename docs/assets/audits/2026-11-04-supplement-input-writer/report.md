# Supplement text input capture

Prospective single-loose-file supplement requests now carry a bounded receipt when the
actual captured source selection, artifact and extraction unambiguously corroborate it.
The existing request is prepared once. Optional enrichment follows ordinary intent
validation and precedes its existing save. Capture failures keep the original request;
cancellation still propagates. Unsupported origins, scans, multipart inputs and changed
provider text remain unstamped. No extra source IO or provider call is introduced.

Primary and supplement builders share the same bounds, source checks and range logic.
Existing primary serialization remains protected by the fixed baseline checksum. Reader
A was actually deployed before this writer: see its [exact release proof](../2026-11-04-supplement-receipt-reader/release.json).
Reader A or a compatible successor is the minimum safe rollback after new work is created.

## Qualification and limits

Root replay compares the actual deployed reader source with this writer using one exact
versioned script. All 26 text/context/frozen OpenAI payload/custom-ID/memo/projection cases
are identical. Receipts change from zero to 26. There are 16 natural cases: eight eligible
official-title OSR/government-position PDFs, each at full and 12,000-character loader budgets.
Two natural budget cases include generated separators. Ten synthetic controls alter dates,
amounts, negations, scope or references. All 21 pinned corpus inputs remain unchanged.

Three official-title-ineligible cases are explicitly excluded. Scanned/unreadable files
and all-purpose provenance are not accepted by this sample. Prior analysis is controlled,
not reconstructed paid history. Local actual OpenAI payload preparation refuses client
construction and submission. The loader budget does not establish provider-fitting coverage;
a separate unit preserves changed prepared text and safely omits its uncorroborated map.

Independent queued fixtures add 1,245–1,608 SQL bytes, or 207–615 gzip bytes each. These are
local receipt-only deltas, not a production growth forecast. No raw archive, backend,
migration, prompt, model output schema or reader payload changes. DA5 remains partial.

## Recheck

On reader commit a266030dd1a05d0afc75e876fd5f5176ed40d5db, copy this exact replay tool into
an isolated archive and run it with `PYTHONPATH=src:.` and the project environment. Then run
the same command on the candidate with `--baseline` pointing at the first report:

```sh
python tools/replay_supplement_inputs.py \
  --corpus /path/to/lexinform-corpus --output /new/output/path \
  --index-sha256 2030ab1d13f961fcb09bd58247cbcf97aba3a3575725eed166940620f4c60666
```

Outputs must be new and outside the corpus. The guard blocks network and corpus writes.
[Qualification](qualification.json), [peer review](peer-review.json),
[reader baseline](root-baseline.json.gz), [candidate replay](root-replay.json.gz).

[Actual writer CI and relay release](release.json).
