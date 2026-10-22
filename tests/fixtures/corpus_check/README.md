# Offline corpus regression slice

Run from the repository root, with a new output file outside this fixture directory:

```sh
make corpus-check CORPUS=tests/fixtures/corpus_check CORPUS_OUTPUT=/tmp/corpus-check-new.json
```

Profiles are `extraction,selection,comparison,delivery`; choose with `CORPUS_PROFILES`.
The input manifest pins every source SHA, official identity and relation, coverage, initiative,
dataset split, expectation and its basis. Missing manifests, profiles or inputs report `not_run`
and return a nonzero exit status. Results are never replaced. A consumed holdout is recorded as
regression in the result; promote it in a later reviewed input-manifest change before reuse.
Machine baselines are labeled non-independent and cannot constitute a holdout.
Results include the code HEAD, dirty-state flag and hashes of Python sources and `uv.lock`,
so an uncommitted execution is distinguishable from the code at HEAD.

`rcl_779502.docx` is the original official RCL document, SHA-256
`1cd93dcd5a30a4f4655fb69372c18f36e3222a5697f35305f2ad5b5a9f6f54e8`, copied from
`lexinform-corpus/documents/files/779502.docx`; source:
<https://legislacja.rcl.gov.pl/docs//2/12410906/13207357/13207358/dokument779502.docx>.
The URL is provenance, never fetched by this runner.

`rcl_12410906.json` and `print_8_2864.json` retain the existing reviewed fixtures from
`tests/fixtures/document_selection/`. The RCL fixture retains only stage 4 from source
`corpus/12410906.json`, original SHA-256
`6db5d23a0e1c9169ab9d4cd045ccfaf245701c67199ddfa660a165417beafa5c`.
They are official-source cached metadata rather than freshly parsed complete source pages.
`synthetic_law.txt` and the delivery JSON files are self-authored fixtures and are not Polish law.

All cases are regressions. No holdout accuracy or full external-corpus coverage is claimed.
The manifest's exclusions are copied into every result. Comparison reuses the existing
`probe_law_comparison.decide` and `probe_law_mutations.mutations` replay functions. Delivery
reuses three fixed offline scenarios with fake providers and an in-memory repository.
Network connections are blocked while cases execute; no corpus script or model client is run.
The remaining existing full-corpus replay scripts still require separate release-gate runs.
