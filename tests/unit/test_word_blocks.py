import hashlib
from xml.etree import ElementTree as ET

import pytest
from pydantic import ValidationError

from lexinform.adapters.document_text import DocxTextExtractor
from lexinform.models import WordTextBlock, WordTextExtraction
from tests.unit.test_physical_extraction import archive

NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def word(body: str, *, notes: str | None = None, namespace: str = NS) -> bytes:
    files = {
        "word/document.xml": (
            f'<w:document xmlns:w="{namespace}" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"><w:body>{body}</w:body></w:document>'
        ).encode()
    }
    if notes is not None:
        files["word/footnotes.xml"] = (
            f'<w:footnotes xmlns:w="{namespace}"><w:footnote w:id="2"><w:p><w:r><w:t>{notes}</w:t></w:r></w:p></w:footnote></w:footnotes>'
        ).encode()
    return archive(files)


def paragraph(text: str) -> str:
    return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"


def test_paragraphs_cells_and_rows_keep_xml_paths_before_flattening() -> None:
    body = (
        paragraph("HEAD")
        + "<w:tbl><w:tr><w:tc>"
        + paragraph("A")
        + "</w:tc><w:tc>"
        + paragraph("B")
        + "</w:tc></w:tr></w:tbl>"
        + paragraph("TAIL")
    )
    data = word(body)
    reader = DocxTextExtractor()

    result = reader.extract_blocks(data)

    assert result.legacy_text == reader.extract(data) == "HEAD\nA\tB\nTAIL"
    assert [(block.kind, block.path, block.text) for block in result.blocks] == [
        ("paragraph", (0, 0), "HEAD"),
        ("table_row", (0, 1, 0), "A\tB"),
        ("table_cell", (0, 1, 0, 0), "A"),
        ("paragraph", (0, 1, 0, 0, 0), "A"),
        ("table_cell", (0, 1, 0, 1), "B"),
        ("paragraph", (0, 1, 0, 1, 0), "B"),
        ("paragraph", (0, 2), "TAIL"),
    ]
    assert result.legacy_blocks == (
        result.blocks[0].block_id,
        result.blocks[1].block_id,
        result.blocks[-1].block_id,
    )
    assert result.input_sha256 == hashlib.sha256(data).hexdigest() and reader.pages(data) == 0
    assert WordTextExtraction.model_validate_json(result.model_dump_json()) == result


def test_nested_table_records_keep_cell_boundaries_without_duplicate_legacy_text() -> None:
    body = (
        "<w:tbl><w:tr><w:tc>"
        + paragraph("before")
        + "<w:tbl><w:tr><w:tc>"
        + paragraph("inner")
        + "</w:tc></w:tr></w:tbl>"
        + paragraph("after")
        + "</w:tc><w:tc>"
        + paragraph("second")
        + "</w:tc></w:tr></w:tbl>"
    )

    result = DocxTextExtractor().extract_blocks(word(body))

    assert result.legacy_text == "before inner after\tsecond"
    assert len(result.legacy_blocks) == 1
    assert [block.text for block in result.blocks if block.kind == "table_row"] == [
        "before inner after\tsecond",
        "inner",
    ]


def test_content_controls_keep_their_actual_intermediate_xml_positions() -> None:
    body = (
        "<w:sdt><w:sdtContent>"
        + paragraph("A")
        + "</w:sdtContent></w:sdt><w:tbl><w:sdt><w:sdtContent><w:tr><w:sdt><w:sdtContent><w:tc>"
        + paragraph("B")
        + "</w:tc></w:sdtContent></w:sdt></w:tr></w:sdtContent></w:sdt></w:tbl>"
    )
    data = word(body)
    result = DocxTextExtractor().extract_blocks(data)
    root = ET.fromstring(f'<w:document xmlns:w="{NS}"><w:body>{body}</w:body></w:document>')

    for block in result.blocks:
        node = root
        for index in block.path:
            node = node[index]
        assert (
            node.tag.rsplit("}", 1)[-1]
            == {"paragraph": "p", "table_row": "tr", "table_cell": "tc"}[block.kind]
        )
    assert result.legacy_text == "A\nB"


@pytest.mark.parametrize("namespace", [NS, "http://purl.oclc.org/ooxml/wordprocessingml/main"])
def test_blank_paragraphs_page_breaks_and_mc_fallback_preserve_the_legacy_view(
    namespace: str,
) -> None:
    body = (
        '<w:p/><w:p><w:r><w:t>A</w:t><w:br w:type="page"/></w:r><mc:Fallback><w:r><w:t>duplicate</w:t></w:r></mc:Fallback></w:p>'
        + paragraph("B")
        + "<w:p/>"
    )
    data = word(body, namespace=namespace)
    reader = DocxTextExtractor()

    result = reader.extract_blocks(data)

    assert [block.text for block in result.blocks] == ["", "A\f", "B", ""]
    assert result.legacy_text == reader.extract(data) == "\nA\fB\n"
    assert "duplicate" not in result.legacy_text


def test_insertions_deleted_text_and_cached_fields_keep_the_existing_view() -> None:
    body = '<w:p><w:ins><w:r><w:t>new</w:t></w:r></w:ins><w:del><w:r><w:delText>old</w:delText></w:r></w:del><w:fldSimple w:instr="DATE"><w:r><w:t>cached</w:t></w:r></w:fldSimple><w:r><w:instrText>instruction</w:instrText></w:r></w:p>'
    data = word(body)

    result = DocxTextExtractor().extract_blocks(data)

    assert result.legacy_text == DocxTextExtractor().extract(data) == "newcached"
    assert result.text_view == "legacy-word-body-v1"


def test_notes_remain_inline_and_change_identity_without_inventing_note_coordinates() -> None:
    body = '<w:p><w:r><w:t>Termin</w:t><w:footnoteReference w:id="2"/></w:r></w:p>'
    first = DocxTextExtractor().extract_blocks(word(body, notes="30 dni"))
    second = DocxTextExtractor().extract_blocks(word(body, notes="60 dni"))

    assert first.legacy_text == "Termin [przypis 2: 30 dni] "
    assert len(first.blocks) == 1 and first.blocks[0].xml_part == "word/document.xml"
    assert first.xml_sha256 == second.xml_sha256
    assert (
        first.input_sha256 != second.input_sha256
        and first.manifest_sha256 != second.manifest_sha256
    )


@pytest.mark.parametrize(
    ("xml", "state"),
    [
        (
            b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>',
            "missing",
        ),
        (b"<other/>", "unsupported"),
        (b'<!DOCTYPE root [<!ENTITY x "text">]><other/>', "unsupported"),
    ],
)
def test_missing_or_unsupported_body_has_no_invented_blocks(xml: bytes, state: str) -> None:
    data = archive({"word/document.xml": xml})
    result = DocxTextExtractor().extract_blocks(data)

    assert result.body_state == state and not result.blocks and not result.legacy_blocks
    assert result.legacy_text == DocxTextExtractor().extract(data) == ""


@pytest.mark.parametrize("paths", [((0,), (0,)), ((1,), (0,)), ((-1,),)])
def test_manifest_rejects_duplicate_reordered_and_negative_xml_paths(
    paths: tuple[tuple[int, ...], ...],
) -> None:
    with pytest.raises(ValidationError, match="Word"):
        WordTextExtraction(
            input_sha256="0" * 64,
            xml_sha256="1" * 64,
            python_version="fixture",
            body_state="read",
            blocks=tuple(WordTextBlock(kind="paragraph", path=path) for path in paths),
        )


def test_projection_cannot_refer_to_a_missing_block_or_a_cell() -> None:
    cell = WordTextBlock(kind="table_cell", path=(0, 0))
    for identity in ("absent", cell.block_id):
        with pytest.raises(ValidationError, match="projection"):
            WordTextExtraction(
                input_sha256="0" * 64,
                xml_sha256="1" * 64,
                python_version="fixture",
                body_state="read",
                blocks=(cell,),
                legacy_blocks=(identity,),
            )
