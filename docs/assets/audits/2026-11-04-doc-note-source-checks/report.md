# Opt-in DOC source-character checks

Historical qualification · 4 November 2026 · baseline `fa0fb7a7`.

`DocTextExtractor.extract_with_notes` reads one OLE container and renders the legacy text
once, then reuses its streams, CLX and parsed pieces. Its immutable result binds the SHA
of the actual input bytes to the unchanged text and optional note qualification. The
public streams API has no invented OLE identity. Default extracted text is unchanged.

The optional checks validate source CP coverage and CLX shape before checking raw automatic
reference code units and each note's terminal CR. UTF-16 CPs are independent of Python
character offsets. Physical bytes may be reused by different pieces; FC order is not CP
order. Reads ending exactly at cbMac are valid; padding beyond cbMac cannot supply evidence.
Unsupported/malformed note metadata remains unavailable while successful legacy text survives.
Unexpected optional errors and cancellation propagate. CFSpec and emitted offsets stay unknown.

The pinned corpus contains 404 DOC paths/246 unique inputs. All245 OLE inputs qualified,
including69 note-bearing originals/377 footnote links; one non-OLE SHA/two paths remains
unavailable to the DOC reader. Endnotes and nonBMP have synthetic controls; no natural
endnote coverage is claimed. All405 raw/INDEX paths stayed unchanged. The replay CLI checks
one actual OLE open per opt-in OLE input and compares text with the pinned old reader hashes.
An independent driver reconstructs PLC pairs and source-unit positions directly from raw
streams, confirming377 links. Five in-memory derivatives of a real note change a date,
amount, negation, scope and legal reference: each change reaches the text, and old/default/
opt-in outputs remain identical. Derivative payloads and note bodies are not retained.

Independent review also found a replay boundary defect: permitted project files could be
mislabelled as corpus inputs via an absolute manifest path. Explicit corpus membership is
now required before IO, with absolute/relative negative controls. Short byte signatures
use BytesIO because olefile otherwise treats short bytes as filenames. The production
DOC adapter also uses BytesIO at this boundary: both public APIs now reject the short
input without probing any local filename, proven by two deterministic RED/green controls. Pinned reference
SHA, exact populations and text mismatches are checked before publishing a report.

No default note writer, new SQL field/table, or per-note retained row is enabled. Existing
extractor code fingerprints change normally with a code release; historical inputs are
not rewritten. Physical-leaf integration and bounded retained diagnostics remain DA3.3 work.

Reproduce the ordinary replay:

```bash
uv run --frozen python -m tools.replay_doc_notes --source-characters \
  --corpus /path/to/lexinform-corpus \
  --manifest docs/assets/audits/2026-11-04-doc-note-reader/input-manifest.json \
  --reference docs/assets/audits/2026-11-04-doc-note-source-checks/baseline-texts.json \
  --reference-sha256 03c2d9639ef4e5ecfcbdce4c8df21a08ca70ccdefb30cb868edc3bbf5d43cca1 \
  --output /path/to/new-report-directory
```

Reproduce the independent checks and semantic controls:

```bash
git show fa0fb7a7:src/lexinform/adapters/doc_text.py > /tmp/doc-baseline.py
uv run --frozen python -m tools.verify_doc_note_sources \
  --corpus /path/to/lexinform-corpus \
  --manifest docs/assets/audits/2026-11-04-doc-note-reader/input-manifest.json \
  --baseline /tmp/doc-baseline.py --output /path/to/new-proof.json
```

The driver executes only the exact old source with the required SHA. Both qualifications
forbid corpus writes/network and refuse to replace output evidence.

[Summary](summary.json), [full replay](report.json.gz),
[independent source proof](independent-source-proof.json), [qualification](qualification.json).
