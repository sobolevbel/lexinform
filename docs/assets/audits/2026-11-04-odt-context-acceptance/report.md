# ODT context acceptance and current release gate

The bounded native replay distinguishes original download-root selection from the same
observed ODT bytes processed alone. Six original roots contain eight ODT leaves. One
ODT is selected in its original context, with no notes; four standalone note-positive
leaves retain four notes and four embedded calls. These populations are not interchangeable.

Independent checks validate XML note/call paths, embedded targets, physical SHA bindings
and retained block bounds. The natural cohort contains no separate `text:note-ref`, and
its complex notes yield zero independently verified plaintext emitted spans. These gaps
remain open in DA3.3. The report stores hashes, paths and counts, without document bodies.

The audit rejects missing native sidecars, an empty physical ODT population, changed raw
SHA, escaping corpus paths and existing output directories. It shares corpus guards and
XML verification with existing replay tools; it does not implement another parser.

Reproduce from the repository root with the separately maintained corpus checkout:

```sh
uv run --frozen python -m tools.audit_odt_context \
  --corpus /path/to/lexinform-corpus \
  --manifest docs/assets/audits/2026-10-28-odt-replay/cases.json \
  --output /path/to/new-odt-context-audit
```

The manifest pins all six roots and the corpus INDEX. A different INDEX or root SHA
fails instead of silently changing the cohort. Output is create-only; the corpus is read-only
and network access is prohibited. Successful replay does not certify legal conclusions.

DA10.5 is accepted for the current release profile. Writer `1d7e44a5` passed its exact
CI and actual relay transition from `d1277f57`, including health and timer checks.
State tip `105de577`, the current allowlisted flags and `daily.yml` were rechecked after
deployment and match the fresh isolated restore qualification. No production processing,
paid experiment, storage backend or future feature activation is part of this acceptance.
Natural production cycles remain separate DA10.6/10.7 gates.

[Qualification](qualification.json), [cohort summary](summary.json),
[bounded native report](report.json), [current release receipt](current-release-acceptance.json),
[writer release proof](../2026-11-04-doc-note-default/release.json).
