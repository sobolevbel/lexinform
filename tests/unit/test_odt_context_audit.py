"""ODT context qualification keeps corpus boundaries and distinguishes selected root/leaf evidence."""

import hashlib
import json
import os
from pathlib import Path

import pytest
from tools.audit_odt_context import audit_context, run, verify_retained_tables
from tools.replay_odt_blocks import snapshot, verify_note_origins

from lexinform.adapters.document_text import DocumentTextExtractor
from lexinform.models.odt_notes import OdtNotedParts
from tests.unit.test_odt_blocks import odt_document
from tests.unit.test_odt_note_capture import note, observed
from tests.unit.test_odt_table_history import table_merges
from tests.unit.test_physical_extraction import archive, docx

MARKER = "CORPUS-BODY-MUST-NOT-APPEAR-IN-THE-REPORT"


def replay_input(tmp_path: Path, data: bytes) -> tuple[Path, Path]:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "INDEX.json").write_bytes(b"{}")
    (corpus / "root.zip").write_bytes(data)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "index_sha256": hashlib.sha256(b"{}").hexdigest(),
                "cases": [
                    {
                        "path": "root.zip",
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "official_ids": ["unit:root"],
                        "cohorts": ["unit:odt-context"],
                    }
                ],
                "exclusions": ["Synthetic context control, not natural acceptance"],
            }
        )
    )
    return corpus, manifest


@pytest.mark.parametrize("relative", [False, True])
def test_manifest_cannot_read_paths_outside_the_corpus(tmp_path: Path, relative: bool) -> None:
    corpus, manifest = replay_input(tmp_path, b"unused")
    outside = tmp_path / "outside.zip"
    outside.write_bytes(b"outside input")
    values = json.loads(manifest.read_text())
    values["cases"][0]["path"] = os.path.relpath(outside, corpus) if relative else str(outside)
    values["cases"][0]["sha256"] = hashlib.sha256(outside.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(values))
    output = tmp_path / "result"

    with pytest.raises(ValueError, match="escapes root|corpus-relative"):
        run(corpus, manifest, output)

    assert not output.exists()


def test_wrong_input_hash_cannot_publish_a_passing_report(tmp_path: Path) -> None:
    corpus, manifest = replay_input(tmp_path, b"unused")
    values = json.loads(manifest.read_text())
    values["cases"][0]["sha256"] = "f" * 64
    manifest.write_text(json.dumps(values))
    output = tmp_path / "result"

    with pytest.raises(ValueError, match="pinned raw SHA"):
        run(corpus, manifest, output)

    assert not output.exists()


def test_real_prefix_evidence_in_a_standalone_leaf_does_not_change_root_selection(
    tmp_path: Path,
) -> None:
    odt = odt_document("<text:p>OPINIA " + note(content=MARKER) + "</text:p>")
    data = archive(
        {
            "projekt.docx": docx("USTAWA Art. 1. Cudzoziemiec składa wniosek. " * 20),
            "opinia.odt": odt,
        }
    )
    corpus, manifest = replay_input(tmp_path, data)
    output = tmp_path / "result"

    result = run(corpus, manifest, output)

    assert result["original_selected_note_positive"] == 0
    assert result["standalone_selected_note_positive"] == 1
    assert result["standalone_retained_note_positive"] == 1
    assert result["original_raw_xml_separate_refs"] == 0
    assert result["changed_inputs"] == []
    report = json.loads((output / "report.json").read_text())
    assert MARKER not in json.dumps(report)
    assert report["input_sha256_before"]["root.zip"] == hashlib.sha256(data).hexdigest()
    assert report["standalone_leaf_contexts"][0]["input_sha256"] == hashlib.sha256(odt).hexdigest()
    before = (output / "report.json").read_bytes()

    with pytest.raises(ValueError, match="new directory"):
        run(corpus, manifest, output)

    assert (output / "report.json").read_bytes() == before


@pytest.mark.parametrize("duplicate", [False, True])
def test_missing_or_duplicate_companions_cannot_publish_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, duplicate: bool
) -> None:
    corpus, manifest = replay_input(tmp_path, odt_document("<text:p>" + note() + "</text:p>"))
    output = tmp_path / "result"
    original = DocumentTextExtractor.extract_parts_with_odt_notes

    def corrupt_companions(
        self: DocumentTextExtractor, data: bytes, *, include_pdf_pages: bool = True
    ) -> OdtNotedParts:
        linked = original(self, data, include_pdf_pages=include_pdf_pages)
        companions = linked.odt_notes * 2 if duplicate else ()
        return linked.model_copy(update={"odt_notes": companions})

    monkeypatch.setattr(DocumentTextExtractor, "extract_parts_with_odt_notes", corrupt_companions)

    with pytest.raises(ValueError, match="one distinct note companion"):
        run(corpus, manifest, output)

    assert not output.exists()


def test_a_cohort_without_physical_odt_cannot_publish_acceptance(tmp_path: Path) -> None:
    corpus, manifest = replay_input(tmp_path, docx("USTAWA Art. 1. Cudzoziemiec składa wniosek."))
    output = tmp_path / "result"

    with pytest.raises(ValueError, match="no observed physical ODT"):
        run(corpus, manifest, output)

    assert not output.exists()


@pytest.mark.parametrize(
    ("spacing", "expected"),
    [("<text:s/><text:tab/>", 2), ('<text:s text:c="2"/>', 0), ("<text:line-break/>", 0)],
)
def test_qualified_inline_spacing_is_checked_independently(spacing: str, expected: int) -> None:
    raw = odt_document(f"<text:p>Before {note(content=f'A{spacing}B')} after</text:p>")
    body, origins = observed(raw)

    verified = verify_note_origins(raw, body, origins)
    assert verified.get("verified_inline_space_spans", 0) == expected
    assert verified["verified_plain_spans"] == 0
    if expected:
        entry = origins.entries[0]
        span = entry.emitted
        assert span is not None, "the native observed note records a span in this paragraph"
        shortened = entry.model_copy(
            update={"emitted": span.model_copy(update={"end": span.end - 1})}
        )
        with pytest.raises(ValueError, match="differs from independent XML text"):
            verify_note_origins(raw, body, origins.model_copy(update={"entries": (shortened,)}))


def test_odt_audit_verifies_native_retention_and_rejects_changed_coordinates() -> None:
    raw = odt_document(
        "<table:table><table:table-row><table:table-cell table:number-columns-spanned='2'>"
        "<text:p>" + "USTAWA Art. 1. Cudzoziemiec. " * 20 + "</text:p>"
        "</table:table-cell><table:covered-table-cell/></table:table-row></table:table>"
    )

    audited, _ = audit_context(raw, context="fixture")

    assert audited["odt_leaves"][0]["table_merges"] == {
        "status": "verified",
        "verified_xml_tables": 1,
        "unresolved_xml_tables": 0,
        "retained_tables": 1,
        "retained_merges": 1,
    }
    _, (physical,) = snapshot(raw, "adapter")
    wrong = table_merges().model_copy(update={"verified_tables": 1, "unresolved_tables": 0})
    with pytest.raises(ValueError, match="coordinates differ"):
        verify_retained_tables(wrong, physical["merged_tables"])
