import pytest

from lexinform.adapters.document_text import OdtTextExtractor
from lexinform.adapters.odt_soft_row_source import enrich_odt_soft_rows
from lexinform.models.odt_tables import OdtSoftRowBreak
from tests.unit.test_odt_blocks import odt_document
from tests.unit.test_physical_extraction import extractor


@pytest.mark.parametrize(
    ("before", "unsupported"),
    [("Termin", ""), ("Nowy termin", ""), ("Termin", "<text:bookmark text:name='x'/>")],
)
def test_additive_soft_row_markers_preserve_the_native_view(before: str, unsupported: str) -> None:
    raw = odt_document(
        "<text:p>" + "USTAWA Art. 1. Cudzoziemiec. " * 20 + "</text:p>"
        "<table:table><table:table-row><table:table-cell table:number-columns-spanned='2'>"
        f"<text:p>{before}<text:soft-page-break/>{unsupported} podatku</text:p>"
        "</table:table-cell><table:covered-table-cell/></table:table-row>"
        "<table:table-row><table:table-cell><text:p>Stawka</text:p></table:table-cell>"
        "<table:table-cell><text:p>200 zł</text:p></table:table-cell></table:table-row></table:table>"
    )
    baseline = extractor().extract_document_with_provenance(raw)
    candidate = enrich_odt_soft_rows(OdtTextExtractor(), raw, baseline)
    assert candidate.document == baseline.document
    if unsupported:
        assert candidate is baseline
        return
    (part,) = candidate.result.selected_parts
    assert part.model_copy(update={"odt_soft_rows": None}) == baseline.result.selected_parts[0]
    assert candidate.result.components == baseline.result.components
    assert part.odt_soft_rows is not None, (
        "the original ODT row contains an explicit soft page break"
    )
    (row,) = part.odt_soft_rows.rows
    assert row.path == (1, 0)
    assert row.breaks == (OdtSoftRowBreak(path=(0, 0, 0), offset=len(before)),)
    assert candidate.document.text[slice(*row.span)] == before + "\x0c podatku\t"
