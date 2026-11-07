import pytest
from tools.replay_multipart_claim_input import (
    WordParagraphCitationPin,
    verify_word_paragraph_citation,
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
