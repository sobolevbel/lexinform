# Bounded committee-report source generation

The retained term10 process-detail archive supplies 417 distinct committee-report URLs under
existing amendment-stage policy, but no corresponding originals were established at the checked
print/supplement resume paths. This is a bounded path check, not a global byte census. A new
collector freezes the first12 unique URL hashes in ascending order before any request, retaining
the exact official owner/stage observations and input hashes.

Collection uses the common strict public HTTPS fetcher: complete PDF bodies up to8MiB,64MiB
observed-body budget with one-chunk reserve, sequential requests, no automatic retries or failed-
case substitution. Source starts are bounded to180seconds; each fetch retains its existing60second
limit and10second socket timeout. New create-only generations stay outside the corpus. URLs,
HTTP receipts, body hashes and errors are recorded; cancellation leaves an incomplete generation.
These are freshly fetched bytes, not asserted historical snapshot bytes or independent legal labels.

The actual generation retains11of12 PDFs /2,619,560bytes. Planned1028-A has a transport error;
it remains excluded rather than being replaced. The manifest is verified; original INDEX2030ab1d
and all read inputs remain exact. Root reproduces the frozen plan from current retained metadata
and checks artifacts without another network run. No production processing, provider calls,
corpus installation, INDEX changes, raw backend or SQL body storage is introduced.

Six new unit cases cover deterministic plans, tampering, public-host redirects, limits,
create-only generations and installer compatibility; existing source-collection tests also run.
Root additionally makes the older RPW/supplement installer reject this different report profile
before decoding its rows or creating an install run. Existing filing generations remain unchanged.
The collector is part of the default strict mypy gate.

The later amendment writer uses a separately frozen producer-OFF public preparation baseline
on these11 originals:22 ordinary/correction paths,14text/8scan and no model calls. That baseline
and future writer acceptance remain distinct from source collection. Raw PDFs stay in the local
collection generation; committed evidence contains metadata and hashes only.

```bash
uv run --frozen python -m tools.collect_committee_reports --corpus /path/to/corpus plan \
  --index-sha256 INDEX_SHA256 --output /absolute/new-plan.json
uv run --frozen python -m tools.collect_committee_reports --corpus /path/to/corpus collect \
  --plan /absolute/new-plan.json --plan-sha256 PLAN_SHA256 \
  --output /absolute/new-runs --id unique-run
```

[Qualification](qualification.json), [collection proof](collection-proof.json),
[frozen plan](plan.json), [manifest](collection-manifest.json),
[collection result](collection-result.json.gz), [source pins](source-pins.json),
[independent review](peer-review.json).

Previous reader release: [exact CI and relay verification](previous-release.json).

Actual collector release: [exact CI and relay verification](release.json), commit069cb6f4.
