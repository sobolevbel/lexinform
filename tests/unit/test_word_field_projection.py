from dataclasses import replace

from lexinform.adapters.document_text import DocxTextExtractor
from lexinform.adapters.word_field_projection import project_word_field_ranges
from tests.unit.test_word_note_origins import package


def field(value: str) -> str:
    return f'<w:fldSimple w:instr="DATE"><w:r><w:t>{value}</w:t></w:r></w:fldSimple>'


def test_projection_uses_nested_rows_empty_paragraphs_and_page_break_cleanup() -> None:
    data = package(
        '<w:p><w:r><w:t>😀</w:t><w:br w:type="page"/></w:r></w:p>'
        "<w:tbl><w:tr><w:tc><w:p/>"
        "<w:tbl><w:tr><w:tc><w:p>"
        + field("30 dni")
        + "</w:p></w:tc><w:tc><w:p/></w:tc></w:tr></w:tbl>"
        "<w:p><w:r><w:t>30 dni</w:t></w:r></w:p>"
        "</w:tc><w:tc><w:p>" + field("90 dni") + "</w:p></w:tc></w:tr></w:tbl>",
        {},
    )
    extraction = DocxTextExtractor().extract_with_field_ranges(data)

    projected = project_word_field_ranges(extraction)

    assert projected is not None, "The native table hierarchy has exact legacy joins"
    assert extraction.legacy.legacy_text == "😀\f 30 dni\t 30 dni\t90 dni"
    assert tuple((span.start, span.end) for span in projected) == ((3, 9), (18, 24))
    assert tuple(span.source for span in projected) == extraction.ranges


def test_projection_does_not_emit_range_if_cleanup_removes_its_character() -> None:
    data = package("<w:p>" + field("30 dni\n") + '<w:r><w:br w:type="page"/></w:r></w:p>', {})
    extraction = DocxTextExtractor().extract_with_field_ranges(data)

    assert extraction.state == "checked"
    assert project_word_field_ranges(extraction) is None


def test_projection_requires_exact_cell_parent_even_when_global_text_is_unchanged() -> None:
    data = package(
        "<w:tbl><w:tr><w:tc><w:p>" + field("30 dni") + "</w:p></w:tc></w:tr></w:tbl>", {}
    )
    extraction = DocxTextExtractor().extract_with_field_ranges(data)
    legacy = extraction.legacy
    blocks = tuple(
        block.model_copy(update={"text": "90 dni"}) if block.kind == "table_cell" else block
        for block in legacy.blocks
    )
    damaged = replace(extraction, legacy=legacy.model_copy(update={"blocks": blocks}))

    assert damaged.legacy.legacy_text == legacy.legacy_text
    assert project_word_field_ranges(damaged) is None


def test_projection_requires_exact_source_range_and_hash() -> None:
    extraction = DocxTextExtractor().extract_with_field_ranges(
        package("<w:p>" + field("30 dni") + "</w:p>", {})
    )
    (span,) = extraction.ranges

    assert project_word_field_ranges(replace(extraction, ranges=(replace(span, end=5),))) is None
    assert project_word_field_ranges(replace(extraction, state="partial")) is None
