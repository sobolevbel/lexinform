# Compatible local timing witness reader

Internal batch metadata and retained computations accept an optional LocalTimingWitness. A known
witness must match the actual frozen request SHA, purpose and original input binding; current
item ownership and later application targets can differ. Existing original witness or None is
immutable on reuse. None stays omitted, so old provider contexts, requests, record bytes, result
IDs and reuse keys remain unchanged. No runtime producer, loader or Clock behavior is enabled.

The finite event kinds describe local bill discovery, actual candidate observation or actual
work request. Completion distinguishes native fresh/cache/mixed observations. An absent paired
kind/time means unknown, including source-free contexts. These fields do not assert upstream
publication time, remote provider completion, whole source coverage or legal relevance. A read
may precede the trigger. Known event clocks cannot exceed their actual enclosing carrier clocks.

Witness clocks preserve aware ISO offsets and precision through fixed-offset normalization and
lossless serialization. Existing pure clock arithmetic handles DST folds and extreme dates without
UTC-conversion overflow. New witnesses reject carrier endpoints whose subminute offsets cannot
roundtrip through existing JSON serializers; old None carriers stay permissive. The real UTC
SystemClock is supported. Future optional capture must omit an unsupported witness, not round it.

Shared strict JSON readers cover known result/input paths, duplicate keys, deep malformed data
and timing-only metadata without requiring an input receipt. This explicitly expands the former
finite reader boundary; valid legacy records remain unchanged. No SQL mutation, schema, source
owner inference or writer API is added. New parse helpers preserve the newer card-history lifecycle
and snapshot identity reader during integration from an older base.

The first full gate exposed diagnostic ordering: a malformed supplement result was rejected by
the new generic result scan before the existing domain validator. Only the first restore preflight
now defers retained-result scanning. Intent/item checks stay in their original phase, existing
domain validators keep exact error ownership, and final full snapshot/result validation is
mandatory. Standalone malformed, duplicate/deep or future-timing records still reject atomically;
no parser, legacy checksum or expected domain error is weakened.

Root reproduces all79 old serialized hashes and13 result IDs across ten purpose carriers and
three compact review schemas. On state620f092, all48 table rows/rowids, full default computation
doctor apart from schema/database headers and double restore remain exact; no clocks are inferred.
The actual same-carrier technical fixture adds3,562SQL/280gzip bytes for ten queued witnesses and
7,124SQL/309gzip for ten submitted items plus ten retained results. Largest witness362JSONbytes;
duplicate saves and restore are exact. These are controlled measurements, not production forecasts.

Runtime capture stays OFF. DA10.4 remains open until actual ten-purpose capture, aggregation,
retention and report semantics are qualified, with natural/technical/unknown denominators separate.

[Qualification](qualification.json), [reader boundary inventory](reader-inventory.json),
[integration proof](integration-proof.json), [old wire/ID proof](none-proof.json),
[old fixture hashes](none-goldens.json.gz), [state proof](state-proof.json),
[storage measurement](storage-proof.json), [base review](peer-review.json),
[ordering review](ordering-review.json).

Previous release: [exact CI and relay verification](previous-release.json).

Final full gate: 8549 tests in 291.896 seconds,92%coverage,mypy739,
Ruff/format green; all808source/config pins exact. [Case-set comparison](new-cases.json)
confirms108new units, zero new scenarios and zero removed cases. No DA10.4 closure claimed.

Actual reader release4f16442: [CI and relay verification](../2026-11-05-local-timing-off-baseline/previous-release.json).
