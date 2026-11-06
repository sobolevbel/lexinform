import hashlib

from lexinform.adapters.document_text import DocxTextExtractor
from tests.unit.test_word_note_origins import package


def simple(value: str) -> str:
    return f'<w:fldSimple w:instr="DATE"><w:r><w:t>{value}</w:t></w:r></w:fldSimple>'


def marker(kind: str) -> str:
    return f'<w:r><w:fldChar w:fldCharType="{kind}"/></w:r>'


def test_simple_field_range_binds_to_emitted_paragraph_fragment() -> None:
    data = package(
        "<w:p><w:r><w:t>😀 30 dni </w:t></w:r>"
        + simple("30 dni")
        + "<w:r><w:t> 30 dni</w:t></w:r></w:p>",
        {},
    )
    reader = DocxTextExtractor()

    result = reader.extract_with_field_ranges(data)

    assert result.legacy == reader.extract_blocks(data)
    assert result.state == "checked"
    (field,) = result.ranges
    (paragraph,) = result.legacy.blocks
    assert field.path == (*paragraph.path, 1, 0, 0)
    assert field.paragraph_path == paragraph.path
    assert field.paragraph_sha256 == paragraph.text_sha256
    assert (field.start, field.end) == (9, 15)
    assert paragraph.text[field.start : field.end] == "30 dni"
    assert field.text_sha256 == hashlib.sha256(b"30 dni").hexdigest()


def test_complex_field_across_cell_paragraphs_has_paragraph_local_ranges() -> None:
    data = package(
        "<w:tbl><w:tr><w:tc><w:p>"
        + marker("begin")
        + "<w:r><w:instrText>DATE</w:instrText><w:t>legacy prelude</w:t></w:r>"
        + marker("separate")
        + "</w:p><w:p><w:r><w:t>90 dni</w:t></w:r>"
        + marker("end")
        + "</w:p></w:tc></w:tr></w:tbl>",
        {},
    )

    result = DocxTextExtractor().extract_with_field_ranges(data)

    assert result.state == "checked"
    assert result.legacy.legacy_text == "legacy prelude 90 dni"
    prelude, duration = result.ranges
    assert prelude.group == "before_separator"
    assert duration.group == "after_separator"
    paragraphs = {block.path: block for block in result.legacy.blocks if block.kind == "paragraph"}
    for field, text in ((prelude, "legacy prelude"), (duration, "90 dni")):
        block = paragraphs[field.paragraph_path]
        assert block.block_id not in result.legacy.legacy_blocks
        assert block.text_sha256 == field.paragraph_sha256
        assert (field.start, field.end) == (0, len(text))


def test_complex_field_with_skipped_separator_is_unknown() -> None:
    data = package(
        "<w:p>"
        + marker("begin")
        + "<mc:Fallback>"
        + marker("separate")
        + "</mc:Fallback><w:r><w:t>value</w:t></w:r>"
        + marker("end")
        + "</w:p>",
        {},
    )

    result = DocxTextExtractor().extract_with_field_ranges(data)

    assert result.legacy.legacy_text == "value"
    assert result.state == "unknown"
    assert result.ranges == ()


def test_unbalanced_sequence_does_not_publish_other_ranges() -> None:
    data = package("<w:p>" + simple("value") + marker("begin") + "</w:p>", {})

    result = DocxTextExtractor().extract_with_field_ranges(data)

    assert result.legacy.legacy_text == "value"
    assert result.state == "unknown"
    assert result.ranges == ()


def test_skipped_simple_field_nodes_are_counted_as_unobserved() -> None:
    data = package(
        "<w:p><mc:Fallback>"
        + simple("unvisited")
        + "</mc:Fallback>"
        + simple("visited")
        + "</w:p>",
        {},
    )

    result = DocxTextExtractor().extract_with_field_ranges(data)

    assert result.legacy.legacy_text == "visited"
    assert result.state == "partial"
    assert result.unobserved_nodes == 1
    assert len(result.ranges) == 1
