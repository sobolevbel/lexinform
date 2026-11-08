import pytest
from tools.replay_multipart_claim_input import OdtTableRowCitationPin, verify_odt_table_row_citation

from lexinform.adapters.document_text import OdtTextExtractor
from tests.unit.test_odt_blocks import odt_document


def test_odt_row_citation_requires_its_original_occurrence_and_exact_cells() -> None:
    row = (
        "<table:table-row><table:table-cell><text:p>Art. 1</text:p></table:table-cell>"
        "<table:covered-table-cell/><table:table-cell>"
        "<text:p>Termin<text:s/>30 dni.</text:p></table:table-cell></table:table-row>"
    )
    raw = odt_document(f"<table:table>{row}{row}</table:table>")
    extracted = OdtTextExtractor().extract_odt_blocks(raw)
    first, second = (block for block in extracted.blocks if block.kind == "table_row")
    assert first.text == second.text
    pin = OdtTableRowCitationPin(
        xml_sha256=extracted.xml_sha256,
        manifest_sha256=extracted.manifest_sha256,
        row_sha256=first.text_sha256,
        path=first.path,
    )

    proof = verify_odt_table_row_citation(
        raw, pin, text=extracted.legacy_text, quote_span=(0, len(first.text))
    )

    assert proof["cell_text_spans"] == [[0, 6], [7, 7], [8, len(first.text)]]
    second_start = len(first.text) + 1
    with pytest.raises(ValueError, match="original offset"):
        verify_odt_table_row_citation(
            raw,
            pin,
            text=extracted.legacy_text,
            quote_span=(second_start, second_start + len(second.text)),
        )
