import io
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from tools.replay_word_blocks import snapshot
from tools.word_table_grid import WordGridCell, WordGridTable, word_table_grids

from lexinform.adapters.document_text import DocxTextExtractor
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
