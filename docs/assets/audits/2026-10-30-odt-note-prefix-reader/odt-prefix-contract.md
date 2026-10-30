Inert reader: optional omitted-None `ExtractedPartSummary.odt_note_prefix: OdtNotePrefix`; envelope contains existing `OdtNoteOrigins` and bounded `(block_path, kind, original_block_chars)` witnesses. Sum of prefix envelopes ≤4096 ASCII JSON bytes per ExtractionResult, still ≤16384 overall; preserve old parts/Word metadata. Preflight widths/events/paths before serialization; revalidate typed children.

Require actual physical_index/input/body bindings and status. `status.contents=prefix_retained` iff prefix exists; absent keeps old not_retained JSON. Status counts/coverage/retained_manifest_sha256 describe original captured graph. Prefix counts retain original observed totals; its coverage describes stored events and its computed retained_manifest_sha256 hashes the reprojected prefix. Never equate these hashes after truncation.

Retain global event-order prefix, preserving entry-before-embedded-call and captured-ancestor closure. Recompute associations using existing conservative resolver: discarded definition inventory forbids resolved/missing; excluded targets become unverified. Bound emitted ranges by witnessed original block lengths, never trimmed summary chars. No text/raw/DDL/store.

Owned: models/extractions.py, models/__init__.py, targeted reader tests. Writer/projection/actual-source witnesses follow only after deployed reader; no default activation here.
