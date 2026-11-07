import hashlib
from xml.etree import ElementTree as ET

import pytest

from lexinform.adapters import document_text
from lexinform.adapters.native_block_copies import NativeBlockCopies, pdf_block_copies
from lexinform.input_receipts import primary_input_receipt
from lexinform.models import ArtifactIdentity, DocumentSelectionRecord, TextDocument
from lexinform.models.block_input import BlockCopyWitness
from lexinform.models.documents import OdtTextExtraction, PdfPageText, PdfTextExtraction
from lexinform.pdf_projection import legacy_pdf_text, pdf_copy_spans, raw_pdf_page_span
from lexinform.services.document_history import DocumentSourceRead
from lexinform.services.input_receipts import with_block_companion
from lexinform.text_projection import SourcePiece, project_pieces
from tests.unit.test_input_receipts import request
from tests.unit.test_odt_blocks import odt_document, router
from tests.unit.test_pdf_physical_parts import PdfWithPages, extractor


def test_pdf_native_copies_bind_raw_pages_and_one_existing_page_read() -> None:
    pdf = PdfWithPages("  USTAWA\r\nArt. 1. 100 zł.  \nNie uchyla się.\xa0\n\n\nKoniec  ")
    result = extractor(pdf).extract_document_with_provenance(b"%PDF fixture")
    witness = result.result.selected_parts[0].block_copy_witness
    assert witness is not None, "native PDF page records retain unchanged exact copied runs"
    assert pdf.page_reads == 1
    assert pdf.string_reads == 0
    assert witness.coordinates == "pdf-pages-v1"
    assert witness.text_view == "pdf-whitespace-v1"
    assert all(row.block_id == "pdf-page:2:0" for row in witness.runs)
    for row in witness.runs:
        assert (
            result.document.text[row.source_start : row.source_end]
            == pdf.second_page[row.block_start : row.block_end]
        )
        assert row.block_text_sha256 == hashlib.sha256(pdf.second_page.encode()).hexdigest()


def test_pdf_projection_does_not_invent_copy_for_normalized_characters() -> None:
    pages = ("  A\r\nB\xa0C  \n\n\nD ", "", "E")
    text = legacy_pdf_text(pages)
    runs = pdf_copy_spans(pages)
    assert runs
    for start, end, page, left, right in runs:
        assert text[start:end] == pages[page][left:right]
        assert "\r" not in text[start:end] and "\xa0" not in text[start:end]
    assert not any(start <= text.index("B") + 1 < end for start, end, *_ in runs)
    assert pdf_copy_spans(("x" * 1_000_001,)) == ()


@pytest.mark.parametrize("changed", ["12.10.2026", "200 zł", "nie uchyla"])
def test_pdf_date_amount_negation_changes_are_not_hidden(changed: str) -> None:
    baseline = "Art. 1. 11.10.2026 100 zł uchyla"
    modified = baseline.replace(
        "11.10.2026"
        if changed.startswith("12")
        else "100 zł"
        if changed.startswith("200")
        else "uchyla",
        changed,
    )
    witnesses = []
    for text in (baseline, modified):
        body = PdfTextExtraction(
            input_sha256="a" * 64,
            pypdf_version="test",
            pages=(PdfPageText(number=1, text=text),),
        )
        witness = pdf_block_copies(body)
        assert witness is not None, "unchanged native page text is an exact whole block copy"
        assert raw_pdf_page_span(text, 0, len(text)) == (0, len(text))
        witnesses.append(witness)
    assert witnesses[0].runs[0].text_sha256 != witnesses[1].runs[0].text_sha256
    assert witnesses[0].witness_id != witnesses[1].witness_id


def test_odt_copy_is_same_parse_and_preserves_body_manifests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = odt_document(
        "<text:p>USTAWA 100 zł</text:p><text:p>Termin <text:span>11.10.2026</text:span>.</text:p>"
    )
    reader = router()
    before = reader.extract_parts(raw)
    original = ET.fromstring
    calls = 0

    def parsed(text: bytes) -> ET.Element:
        nonlocal calls
        calls += 1
        return original(text)

    monkeypatch.setattr(ET, "fromstring", parsed)
    after = reader.extract_document_with_provenance(raw)
    witness = after.result.selected_parts[0].block_copy_witness
    assert witness is not None, "ordinary ODT paragraph extraction has known native bounds"
    assert calls == 1
    assert after.document.text == before.parts[0].text
    assert witness.manifest_sha256 == before.odt_blocks[0].extraction.manifest_sha256
    assert [row.block_id for row in witness.runs] == [
        "odt-xml:content.xml:body:0",
        "odt-xml:content.xml:body:1",
    ]
    assert all(row.block_start == 0 and row.block_end == row.block_chars for row in witness.runs)


@pytest.mark.parametrize(
    "inline",
    [
        "<text:note><text:note-citation>1</text:note-citation><text:note-body><text:p>note</text:p></text:note-body></text:note>",
        "<text:date>11.10.2026</text:date>",
        "<text:change-start/>",
        "<text:soft-page-break/>",
        "<text:unknown>unsupported</text:unknown>",
    ],
)
def test_odt_notes_fields_changes_and_unknown_inline_are_excluded(inline: str) -> None:
    result = router().extract_document_with_provenance(
        odt_document(f"<text:p>USTAWA {inline} tail</text:p><text:p>Known paragraph</text:p>")
    )
    witness = result.result.selected_parts[0].block_copy_witness
    assert witness is not None, "a later supported paragraph remains independently addressable"
    assert [row.block_id for row in witness.runs] == ["odt-xml:content.xml:body:1"]
    assert result.document.text[witness.runs[0].source_start :] == "Known paragraph"


def test_optional_native_capture_failure_preserves_legacy_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = odt_document("<text:p>USTAWA</text:p>")
    reader = router()
    expected = reader.extract_document(raw)

    def broken(
        self: NativeBlockCopies, body: OdtTextExtraction, supported: set[tuple[int, ...]]
    ) -> None:
        raise ValueError("optional capture unavailable")

    monkeypatch.setattr(NativeBlockCopies, "finish_odt", broken)
    actual = reader.extract_document_with_provenance(raw)
    assert actual.document == expected
    assert actual.result.selected_parts[0].block_copy_witness is None


def test_pdf_optional_witness_failure_preserves_output(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(body: PdfTextExtraction) -> BlockCopyWitness | None:
        raise ValueError("optional capture unavailable")

    monkeypatch.setattr(document_text, "pdf_block_copies", broken)
    actual = extractor(PdfWithPages()).extract_document_with_provenance(b"%PDF fixture")
    assert actual.document.text == "USTAWA"
    assert actual.result.selected_parts[0].block_copy_witness is None


@pytest.mark.parametrize("kind", ["pdf", "odt"])
def test_native_witness_reaches_generic_primary_companion_without_request_change(kind: str) -> None:
    raw = b"%PDF fixture" if kind == "pdf" else odt_document("<text:p>USTAWA 100 zł</text:p>")
    reader = extractor(PdfWithPages("USTAWA 100 zł")) if kind == "pdf" else router()
    result = reader.extract_document_with_provenance(raw)
    selection = result.document.selection
    text = selection.parts[0].text
    frozen = request(text)
    body = ArtifactIdentity(sha256=hashlib.sha256(raw).hexdigest(), byte_size=len(raw))
    record = DocumentSelectionRecord.from_selection(selection)
    source = DocumentSourceRead(
        document=TextDocument(url="https://example.test/ustawa", kind="print"),
        selection=record,
        artifacts=(body,),
        extractions=(result.result,),
    )
    projection = project_pieces(text, [SourcePiece(text, 0)])
    receipt = primary_input_receipt(
        frozen,
        selection,
        projection=projection,
        known_selection=record,
        body=body,
        extraction=result.result,
    )
    enriched = with_block_companion(receipt, source, text)
    assert enriched.block_companion is not None, (
        "the generic primary input preserves native witnesses"
    )
    assert enriched.request_sha256 == receipt.request_sha256 == frozen.payload_sha256
    assert enriched.model_dump(exclude={"block_companion"}) == receipt.model_dump(
        exclude={"block_companion"}
    )
    witness = result.result.selected_parts[0].block_copy_witness
    assert witness is not None, "the native extraction producer supplies its exact witness"
    assert all(row.witness_id == witness.witness_id for row in enriched.block_companion.rows)


@pytest.mark.parametrize(
    "before,after", [("11.10.2026", "12.10.2026"), ("100 zł", "200 zł"), ("uchyla", "nie uchyla")]
)
def test_odt_meaningful_changes_change_the_exact_bound_copy(before: str, after: str) -> None:
    witnesses = []
    for text in (before, after):
        result = router().extract_document_with_provenance(
            odt_document(f"<text:p>USTAWA {text}</text:p>")
        )
        witness = result.result.selected_parts[0].block_copy_witness
        assert witness is not None, "the supported ODT paragraph retains its exact extraction view"
        assert (
            witness.runs[0].text_sha256 == hashlib.sha256(result.document.text.encode()).hexdigest()
        )
        witnesses.append(witness)
    assert witnesses[0].witness_id != witnesses[1].witness_id
    assert witnesses[0].runs[0].text_sha256 != witnesses[1].runs[0].text_sha256


def test_native_copies_respect_row_and_byte_caps() -> None:
    result = router().extract_document_with_provenance(
        odt_document("<text:p>USTAWA</text:p>" * 100)
    )
    witness = result.result.selected_parts[0].block_copy_witness
    assert witness is not None, "the bounded prefix still retains known first paragraphs"
    assert len(witness.runs) <= 32 and len(witness.model_dump_json().encode()) <= 4096
    assert witness.runs[-1].source_end < len(result.document.text)
