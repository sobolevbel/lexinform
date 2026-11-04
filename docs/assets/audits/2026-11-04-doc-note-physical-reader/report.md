# DOC note physical binding and compatible retained reader

Historical qualification · 4 November 2026 · baseline `9533c551`.

Pure immutable models bind source-CP captures to the actual leaf SHA, text SHA and physical
index. Native DOC extraction shares the physical walk; custom/overridden extractors retain
their existing behavior. Duplicate leaves at different archive paths keep separate indices.
Body/emitted coordinates remain unlocated and CFSpec stays unknown. No legal labels or raw
retention promises are inferred from the source coordinates.

The optional summary field is omitted when absent and excluded from the existing physical
manifest hash. Full capture and retained prefix have separate hashes; a proper prefix cannot
claim the full hash. At most64 transient sidecars,32 stored links/leaf and4096 aggregate bytes
are admitted within the16384-byte ExtractionResult limit. Trimming is deterministic and never
drops existing selected summaries. The helper is opt-in; default provenance still emits no DOC
note field. Public types are exported through lexinform.models.

Replay covers404 paths/246 unique SHA. The245 native OLE leaves carry377 captured links across
69 note-bearing inputs, and all377 fit the retained profile: maximum3200 DOC bytes/6005 total
summary bytes. Actual physical parts, selection, text and all prior summary fields are exactly
unchanged. The one non-OLE SHA/two paths is routed by its physical format and gains no false DOC
capture. All405 source/INDEX paths remain unchanged. An independent raw-unit driver rechecks
377 links, five meaningful natural-note mutations and a nested archive with duplicate real DOC
leaves. Endnotes remain synthetic coverage only.

Future-row SQL tests exposed a restore gap: ordinary lookup validated ExtractionResult but
scratch restore did not inspect every unreferenced row. One shared adapter reader now checks
JSON, computed identity and stored artifact columns in lookup, RCL parents, scratch restore and
the source doctor. Restore rejects corruption before touching the target; the doctor counts
valid and corrupt rows, continues, and reports only safe identifiers/error codes. No data is
rewritten and no migration or new parent policy is introduced. Checked and unavailable future
summaries roundtrip through SQL; hash corruption leaves the target dump unchanged.

The cached production snapshot `8022b0a59f73` restores with the exact canonical SQL,487 typed
bills,40 legacy extraction rows and unchanged three prior doctor reports. The expanded source
doctor finds no errors. This is a pinned legacy compatibility qualification, not evidence of
new note capture in production. Default activation and paired live-profile growth qualification
remain a separate stage after the reader is deployed.

Reproduce physical qualification with the existing input manifest/reference:

```bash
uv run --frozen python -m tools.replay_doc_notes --source-characters --physical-binding \
  --corpus /path/to/lexinform-corpus \
  --manifest docs/assets/audits/2026-11-04-doc-note-reader/input-manifest.json \
  --reference docs/assets/audits/2026-11-04-doc-note-source-checks/baseline-texts.json \
  --reference-sha256 03c2d9639ef4e5ecfcbdce4c8df21a08ca70ccdefb30cb868edc3bbf5d43cca1 \
  --output /path/to/new-physical-report
```

For independent raw-unit and nested-package checks use the preceding stage's
`tools.verify_doc_note_sources` command with `--physical-binding`. Both tools use the shared
read-only corpus/network guards and refuse to replace output evidence.

[Summary](summary.json), [full replay](report.json.gz), [qualification](qualification.json),
[independent checks](independent-source-proof.json), [cached state](cached-state-proof.json),
[initial physical handoff](author-handoff.json), [shared reader fix](restore-handoff.json).
