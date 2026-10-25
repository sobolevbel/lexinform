import io
import json
import zipfile
from pathlib import Path

import pytest
from pypdf import PdfWriter
from tools.corpus_artifacts import digest
from tools.corpus_derivatives import run as reextract
from tools.corpus_empty_review import physical_leaves, preview, repair_actions, run

from lexinform.models import PdfPageText, PdfTextExtraction
from tests.unit.test_corpus_derivatives import cohort
from tests.unit.test_physical_extraction import archive, docx


def test_empty_archive_selection_does_not_hide_readable_support_and_unsupported_members() -> None:
    leaves = physical_leaves(
        archive(
            {"raport.docx": docx("Raport z konsultacji publicznych."), "signature.xades": b"<sig/>"}
        )
    )

    assert len(leaves) == 2
    assert leaves[0]["chars"] > 0
    assert leaves[0]["role"] == leaves[1]["role"] == "unknown"
    assert repair_actions(leaves) == [
        "review_readable_unselected_parts",
        "review_unsupported_members",
    ]


def test_pdf_pages_without_text_are_review_candidates_and_never_automatically_scans() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    stream = io.BytesIO()
    writer.write(stream)

    leaves = physical_leaves(archive({"nested.zip": archive({"ustawa.pdf": stream.getvalue()})}))

    assert leaves[0]["members"] == ["nested.zip", "ustawa.pdf"]
    assert leaves[0]["pages_without_text"] == [1]
    assert leaves[0]["pages"][0]["chars"] == 0
    assert repair_actions(leaves) == ["review_original_pages_before_ocr"]
    assert leaves[0]["required_for_bill"] == "unknown"


def test_corrupt_archive_and_bounded_members_remain_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert physical_leaves(b"PKbroken")[0]["state"] == "error"
    monkeypatch.setattr("tools.corpus_empty_review.MAX_BYTES", 10)
    leaves = physical_leaves(archive({"large.docx": docx("USTAWA Art. 1. Termin 30 dni.")}))
    assert leaves[0]["state"] == "limit"
    assert repair_actions(leaves) == ["review_physical_error_or_limit"]


def test_duplicate_archive_member_cannot_render_different_bytes_under_the_first_source_hash(
    tmp_path: Path,
) -> None:
    stream = io.BytesIO()
    first, second = b"%PDF-1.4 first", b"%PDF-1.4 second"
    with zipfile.ZipFile(stream, "w") as package:
        package.writestr("ustawa.pdf", first)
        with pytest.warns(UserWarning, match="Duplicate name"):
            package.writestr("ustawa.pdf", second)
    output = tmp_path / "preview"

    with pytest.raises(ValueError, match="Preview source SHA mismatch"):
        preview(stream.getvalue(), ["ustawa.pdf"], output, expected_sha256=digest(first))

    assert not output.with_suffix(".png").exists()


def test_pdf_page_parser_error_is_explicit_and_does_not_only_request_ocr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed_page(self: object, data: bytes) -> PdfTextExtraction:
        return PdfTextExtraction(
            input_sha256=digest(data),
            pypdf_version="test",
            pages=(PdfPageText(number=1, text="", error_type="PdfReadError"),),
        )

    monkeypatch.setattr("tools.corpus_empty_review.PypdfTextExtractor.extract_pages", failed_page)
    leaves = physical_leaves(b"%PDF-1.4 simulated page failure")

    assert leaves[0]["state"] == "error"
    assert "review_physical_error_or_limit" in repair_actions(leaves)


def test_review_checks_pinned_raw_identity_and_writes_unknown_legal_questions(
    tmp_path: Path,
) -> None:
    root, health, pin = cohort(tmp_path)
    (root / "INDEX.json").write_text('{"entities":{"rcl:1":{"documents":[1,2]}}}')
    generation = tmp_path / "generation.json"
    reextract(root, health, pin, generation)
    baseline = json.loads(generation.read_bytes())
    baseline["observations"]["1"]["state"] = "still_empty"
    generation.write_text(json.dumps(baseline))
    gen_pin = digest(generation.read_bytes())
    target = tmp_path / "review.json"

    run(root, generation, gen_pin, target)

    result = json.loads(target.read_bytes())
    row = result["records"]["1"]
    assert row["group"] == "doc:1"
    assert row["status"] == "pending_review" and row["required_annexes"] == "unknown"
    assert row["actions"] == ["review_readable_unselected_parts"]
    assert target.with_suffix(".html").exists()
    (root / "documents/files/1.zip").write_bytes(b"changed")
    with pytest.raises(ValueError, match="raw source changed"):
        run(root, generation, gen_pin, tmp_path / "bad.json")
    with pytest.raises(ValueError, match="new and outside"):
        run(root, generation, gen_pin, target)
