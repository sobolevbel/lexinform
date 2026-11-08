import pytest
from tools.replay_multipart_claim_input import (
    WordParagraphCitationPin,
    WordTableRowCitationPin,
    verify_word_paragraph_citation,
    verify_word_table_row_citation,
)

from lexinform.adapters.document_text import DocxTextExtractor
from tests.unit.test_word_note_origins import call, entry, note_xml, package


def test_word_quote_binds_plain_runs_before_generated_note_text() -> None:
    raw = package(
        "<w:p><w:r><w:t>Header</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Art. 1. </w:t></w:r>"
        "<w:proofErr w:type='spellStart'/><w:r><w:t>Termin 30 dni.</w:t></w:r>"
        + call("2")
        + "<w:r><w:t> Dalszy tekst.</w:t></w:r></w:p>",
        {"word/footnotes.xml": note_xml("footnote", entry("2", "Text from a different XML part."))},
    )
    extracted = DocxTextExtractor().extract_blocks(raw)
    path = (0, 1)
    block = next(item for item in extracted.blocks if item.path == path)
    plain = "Art. 1. Termin 30 dni."
    pin = WordParagraphCitationPin(
        xml_sha256=extracted.xml_sha256,
        manifest_sha256=extracted.manifest_sha256,
        block_sha256=block.text_sha256,
        path=path,
        start=0,
        end=len(plain),
    )
    begin = len("Header\n")
    span = (begin, begin + len(plain))
    assert extracted.legacy_text[slice(*span)] == plain
    assert verify_word_paragraph_citation(raw, pin, text=extracted.legacy_text, quote_span=span)[
        "block_span"
    ] == [0, len(plain)]
    with pytest.raises(ValueError, match="plain XML run prefix"):
        verify_word_paragraph_citation(
            raw,
            pin.model_copy(update={"end": pin.end + 2}),
            text=extracted.legacy_text,
            quote_span=(span[0], span[1] + 2),
        )
    for invalid in (
        pin.model_copy(update={"block_sha256": "0" * 64}),
        pin.model_copy(update={"manifest_sha256": "0" * 64}),
        pin.model_copy(update={"xml_sha256": "0" * 64}),
        pin.model_copy(update={"path": (0, 0)}),
    ):
        with pytest.raises(ValueError, match="differs|differ"):
            verify_word_paragraph_citation(
                raw, invalid, text=extracted.legacy_text, quote_span=span
            )
    with pytest.raises(ValueError, match="coordinates differ"):
        verify_word_paragraph_citation(
            raw, pin, text=extracted.legacy_text, quote_span=(begin - 1, span[1])
        )


def test_word_row_quote_requires_the_exact_xml_row_and_all_cells() -> None:
    row = (
        "<w:tr><w:tc><w:p><w:r><w:t>15</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>Termin 30 dni.</w:t></w:r></w:p></w:tc></w:tr>"
    )
    raw = package(f"<w:tbl>{row}{row}</w:tbl>", {})
    extracted = DocxTextExtractor().extract_blocks(raw)
    text = extracted.legacy_text
    first, second = (block for block in extracted.blocks if block.kind == "table_row")
    pin = WordTableRowCitationPin(
        xml_sha256=extracted.xml_sha256,
        manifest_sha256=extracted.manifest_sha256,
        row_sha256=first.text_sha256,
        path=(0, 0, 0),
    )
    end = len(first.text)
    assert verify_word_table_row_citation(raw, pin, text=text, quote_span=(0, end))[
        "cell_text_spans"
    ] == [[0, 2], [3, end]]
    assert first.text == second.text
    with pytest.raises(ValueError, match="cell span differs"):
        verify_word_table_row_citation(raw, pin, text=text, quote_span=(end + 1, len(text)))
    with pytest.raises(ValueError, match="entire selected row"):
        verify_word_table_row_citation(raw, pin, text=text, quote_span=(3, end))
