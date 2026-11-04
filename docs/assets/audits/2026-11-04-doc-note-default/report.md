# Default bounded DOC note metadata

Historical qualification · 4 November 2026 · reader baseline `d1277f57`.

The reader's exact CI and actual relay release were verified before integration. Default
provenance now requests DOC note captures in the existing physical walk, then adds the bounded
summary after selection and the prior Word/ODT metadata. One native OLE read is preserved.
Custom extractors retain their behavior; invalid optional markers retain text and an explicit
unknown source state. New diagnostics are omitted before existing selected rows, within the
4096-byte DOC aggregate and16384-byte whole metadata budgets. There is no new raw store,
note-text column, table, backfill, repository flag or paid feature.

The ON corpus replay pins the exact released J207 compressed report before decompression and
checks its manifest, input paths/hashes and observation population. All four historical
physical/selection/text/manifest digests match across404 paths/246 unique inputs;245 native
captures retain377 links from69 note-bearing inputs. Maximum DOC metadata3200 B, whole6005 B.
All405 raw/INDEX paths remain unchanged. The independent raw-unit check confirms377 links,
five meaningful mutations and the same original natural nested duplicate package. No natural
endnote, CFSpec, emitted/body mapping or fleet adoption claim follows from these results.
Full historical component/version JSON equality is not claimed: code fingerprints change.

One native CFB pipeline control uses the actual World/Container over fake gateways/backend.
Default OFF/ON have identical queued fake requests, real locally rendered user prompts, memo
keys and custom IDs. Receipt extraction IDs differ deliberately; they name different
provenance records. After collection, a same-byte URL alias adds no request. This is now one
scenario regression, alongside the direct native unit coverage; no real provider was called.

A controlled fresh one-note record measures JSON/SQL +757 B and fixed-mtime gzip +245 B.
Its nine input/selection/text/physical/count fields are exact. It is a synthetic developer
benchmark, not a production growth estimate. `tools.measure_doc_note_growth` reuses native
test fixtures and creates only a temporary in-memory repository; output is create-only.

The fresh state pin105de577 (4 November11:33 UTC) restores schema65 with496 typed bills,
65 extraction rows,224 publications and122 batch items. Direct SQL and repository restore
have identical canonical data/schema. Four doctors find zero errors; four existing work
warnings remain. Input SQL and the restored DB remain unchanged after reads. Allowlisted
repository variables were read without secrets or changes: optional paid reviews retain
workflow fallback false, state maintenance off, reader attention on/daily. Daily workflow
and variables are unchanged. This checks current compatibility/flags; it does not infer
new DOC adoption from the state before this writer release.

The release verifier also fixes a real ambiguity: deployment `head_sha` can name the default
branch while its upstream event concerns older CI. Deployments predating the exact successful
CI are excluded before ambiguity selection; malformed timestamps fail closed, two eligible
runs remain ambiguous, and actual relay transition/45-second health/timer plus final-main
checks remain mandatory. Three new controls cover this; no workflow was rerun or forced.

Reproduce ON qualification with existing pinned corpus inputs:

```bash
uv run --frozen python -m tools.replay_doc_notes --source-characters --physical-binding \
  --default-note-writer --corpus /path/to/lexinform-corpus \
  --manifest docs/assets/audits/2026-11-04-doc-note-reader/input-manifest.json \
  --reference docs/assets/audits/2026-11-04-doc-note-source-checks/baseline-texts.json \
  --reference-sha256 03c2d9639ef4e5ecfcbdce4c8df21a08ca70ccdefb30cb868edc3bbf5d43cca1 \
  --physical-reference docs/assets/audits/2026-11-04-doc-note-physical-reader/report.json.gz \
  --physical-reference-sha256 f35b3cc5de2ae890e6871693e9b426281ce451eb3d2b73db97b14b53bdc7a31a \
  --output /path/to/new-on-report
uv run --frozen python -m tools.measure_doc_note_growth /path/to/new-growth.json
```

For the independent raw/package verifier add both `--physical-binding` and
`--default-note-writer` to the preceding stage's command. To measure the reader baseline,
execute the growth tool with an explicit J207 source/test snapshot on PYTHONPATH and the
same developer interpreter. Nine semantic fields must match before comparing sizes.

[Qualification](qualification.json), [summary](summary.json), [full replay](report.json.gz),
[semantic proof](semantic-proof.json), [growth](growth.json),
[native pipeline](native-pipeline-proof.json), [fresh state](fresh-state.json),
[SQL equivalence](fresh-state-equivalence.json), [allowlisted flags](allowlisted-flags.json).
