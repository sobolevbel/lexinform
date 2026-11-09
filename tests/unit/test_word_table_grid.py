import io
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from tools.replay_word_blocks import snapshot

from lexinform.adapters import document_text
from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.adapters.word_horizontal_source import enrich_word_horizontal_source
from lexinform.models.extractions import ExtractionResult
from lexinform.models.word_grid_merges import WordVerticalMerge
from lexinform.word_table_grid import WordGridCell, WordGridTable, word_table_grids
from tests.unit.test_word_blocks import paragraph, word

GRID = "<w:tblGrid>" + "<w:gridCol/>" * 4 + "</w:tblGrid>"
RESTART = '<w:vMerge w:val="restart"/>'
CONTINUE = "<w:vMerge/>"


def row(merge: str, *, span: int = 2, before: int = 1, after: int = 1, text: str = "") -> str:
    return (
        f'<w:tr><w:trPr><w:gridBefore w:val="{before}"/><w:gridAfter w:val="{after}"/></w:trPr>'
        f'<w:tc><w:tcPr><w:gridSpan w:val="{span}"/>{merge}</w:tcPr>{paragraph(text)}</w:tc>'
        "</w:tr>"
    )


def layout(body: str) -> tuple[WordGridTable, ...]:
    raw = word(f"<w:tbl>{GRID}{body}</w:tbl>")
    with ZipFile(io.BytesIO(raw)) as package:
        xml = package.read("word/document.xml")
    return word_table_grids(ET.fromstring(xml), DocxTextExtractor().extract_blocks(raw).blocks)


def test_vertical_continuations_keep_their_first_physical_cell_and_grid_interval() -> None:
    anchor, first, second = ((0, 0, row_index, 1) for row_index in (1, 2, 3))
    body = row(RESTART, text="200 zł") + row(CONTINUE) + row('<w:vMerge w:val="continue"/>')
    assert layout(body) == (
        WordGridTable(
            (0, 0),
            4,
            3,
            (
                WordGridCell(anchor, 0, (1, 3), anchor),
                WordGridCell(first, 1, (1, 3), anchor),
                WordGridCell(second, 2, (1, 3), anchor),
            ),
        ),
    )
    raw = word(f"<w:tbl>{GRID}{body}</w:tbl>")
    baseline, _ = snapshot(raw, adapter=False)
    candidate, evidence = snapshot(raw, adapter=True, with_table_grid=True)
    assert baseline == candidate
    assert evidence is not None and evidence["table_grid"][0]["rows"] == 3


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (row(CONTINUE), "unmatched_vertical_merge"),
        (row(RESTART) + row(CONTINUE, span=1, after=2), "unmatched_vertical_merge"),
        (row(RESTART) + row("") + row(CONTINUE), "unmatched_vertical_merge"),
        (row(RESTART, after=0), "row_width_mismatch"),
        (row(RESTART, span=4), "cell_outside_grid"),
        (row(RESTART + CONTINUE), "duplicate_grid_property"),
        (row('<w:vMerge w:val="unknown"/>'), "invalid_vertical_merge"),
        (row('<w:hMerge w:val="restart"/>'), "unsupported_horizontal_merge"),
        (row(RESTART + "<w:cellMerge/>"), "grid_revision"),
        (
            "<w:sdt><w:sdtContent>" + row(RESTART) + "</w:sdtContent></w:sdt>",
            "unsupported_table_child",
        ),
    ],
)
def test_unresolved_grid_rejects_the_entire_table(body: str, reason: str) -> None:
    assert layout(body) == (WordGridTable((0, 0), reason=reason),)


def test_logical_grid_area_is_bounded() -> None:
    raw = row(RESTART) + row(CONTINUE) * 4096
    assert layout(raw) == (WordGridTable((0, 0), reason="grid_area_limit"),)


BILL = "USTAWA\nArt. 1. Cudzoziemiec uiszcza 200 zł. " * 15


def extractor(*, capture: bool = False) -> DocumentTextExtractor:
    return DocumentTextExtractor(
        PypdfTextExtractor(),
        DocxTextExtractor(),
        DocTextExtractor(),
        capture_word_grid_merges=capture,
    )


def merged_bill(rows: str) -> bytes:
    return word(paragraph(BILL) + f"<w:tbl>{GRID}{rows}</w:tbl>")


def test_opt_in_grids_share_native_parts_and_keep_extraction_priority() -> None:
    raw = merged_bill(row(RESTART, text="200 zł") + row(CONTINUE))
    baseline = extractor().extract_document_with_provenance(raw)
    current = extractor(capture=True).extract_document_with_provenance(raw)
    assert current.document == baseline.document
    (part,) = current.result.selected_parts
    assert part.word_grid_merges is not None
    (table,) = part.word_grid_merges.tables
    assert table.path == (0, 1)
    assert table.merges == (WordVerticalMerge(row=0, columns=(1, 3), cells=((1, 1), (2, 1))),)
    restored = ExtractionResult.model_validate_json(current.result.model_dump_json())
    assert restored == current.result
    assert current.result.model_copy(update={"selected_parts": baseline.result.selected_parts}) == (
        baseline.result
    )


def test_unresolved_or_failed_grid_does_not_change_legacy_selection() -> None:
    raw = merged_bill(row(CONTINUE))
    baseline = extractor().extract_document_with_provenance(raw)
    enabled = extractor(capture=True)
    unresolved = enabled.extract_document_with_provenance(raw)
    assert unresolved.document == baseline.document
    (part,) = unresolved.result.selected_parts
    assert part.word_grid_merges is not None
    assert part.word_grid_merges.verified_tables == 0
    assert part.word_grid_merges.unresolved_tables == 1 and not part.word_grid_merges.tables
    with patch.object(document_text, "word_table_grids", side_effect=ValueError("failed")):
        assert enabled.extract_document_with_provenance(raw) == baseline


@pytest.mark.parametrize("before", [0, 1])
def test_additive_horizontal_span_keeps_its_grid_interval(before: int) -> None:
    horizontal = f"<w:tbl>{GRID}{row('', before=before, after=2 - before, text='200 zł')}</w:tbl>"
    vertical = f"<w:tbl>{GRID}{row(RESTART, text='100 zł')}{row(CONTINUE)}</w:tbl>"
    raw = word(paragraph(BILL) + horizontal + vertical)
    baseline = extractor(capture=True).extract_document_with_provenance(raw)
    current = enrich_word_horizontal_source(DocxTextExtractor(), raw, baseline)
    assert current.document == baseline.document
    assert current.result.components == baseline.result.components
    (part,) = current.result.selected_parts
    assert part.word_grid_merges == baseline.result.selected_parts[0].word_grid_merges
    spans = part.word_horizontal_spans
    assert spans is not None, "the fixture has one supported horizontal-only table"
    (table,) = spans.tables
    assert table.path == (0, 1)
    (span,) = table.spans
    assert span.row == 0 and span.columns == (before, before + 2) and span.cell == (1, 1)


def test_no_horizontal_evidence_preserves_exact_result() -> None:
    raw = merged_bill(row(RESTART, text="200 zł") + row(CONTINUE))
    baseline = extractor(capture=True).extract_document_with_provenance(raw)
    assert enrich_word_horizontal_source(DocxTextExtractor(), raw, baseline) is baseline


def test_additive_mixed_spans_keep_existing_vertical_chains() -> None:
    raw = merged_bill(row("", text="Zakres") + row(RESTART, text="100 zł") + row(CONTINUE))
    baseline = extractor(capture=True).extract_document_with_provenance(raw)
    current = enrich_word_horizontal_source(DocxTextExtractor(), raw, baseline)
    assert current.document == baseline.document
    (part,) = current.result.selected_parts
    assert part.word_grid_merges == baseline.result.selected_parts[0].word_grid_merges
    assert part.word_horizontal_spans is None
    mixed = part.word_mixed_grid_spans
    assert mixed is not None, "the fixture has one independent horizontal span in a mixed table"
    (table,) = mixed.tables
    (span,) = table.spans
    assert table.path == (0, 1) and table.observed_spans == 1
    assert span.row == 0 and span.columns == (1, 3) and span.cell == (1, 1)
    assert (
        part.model_copy(update={"word_mixed_grid_spans": None}) == baseline.result.selected_parts[0]
    )
