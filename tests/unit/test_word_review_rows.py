import io
from zipfile import ZipFile

from lexinform.adapters.document_text import DocxTextExtractor

_WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _row(label: str, response: str) -> str:
    return (
        "<w:tr><w:tc><w:p>" + label + "</w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>" + response + "</w:t></w:r></w:p></w:tc></w:tr>"
    )


def _numbered(level: int) -> str:
    return f'<w:pPr><w:numPr><w:ilvl w:val="{level}"/><w:numId w:val="5"/></w:numPr></w:pPr>'


def test_consultation_rows_keep_source_text_and_list_labels_separate() -> None:
    body = (
        _row("<w:r><w:t>Lp.</w:t></w:r>", "Stanowisko")
        + _row(_numbered(0), "Uwzględniono")
        + _row(_numbered(1), "Zobacz pkt 93 raportu")
        + _row(_numbered(0), "Odrzucono")
        + _row(_numbered(1), "W części")
    )
    numbering = (
        f'<w:numbering xmlns:w="{_WORD}">'
        '<w:abstractNum w:abstractNumId="4">'
        '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/>'
        '<w:lvlText w:val="%1."/></w:lvl>'
        '<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="lowerLetter"/>'
        '<w:lvlText w:val="%2."/></w:lvl></w:abstractNum>'
        '<w:num w:numId="5"><w:abstractNumId w:val="4"/>'
        '<w:lvlOverride w:ilvl="0"><w:startOverride w:val="93"/></w:lvlOverride>'
        "</w:num></w:numbering>"
    )
    output = io.BytesIO()
    with ZipFile(output, "w") as package:
        package.writestr(
            "word/document.xml",
            f'<w:document xmlns:w="{_WORD}"><w:body><w:tbl>{body}</w:tbl></w:body></w:document>',
        )
        package.writestr("word/numbering.xml", numbering)
    extractor = DocxTextExtractor()
    data = output.getvalue()

    rows = extractor.extract_consultation_rows(data)

    assert [label for _, label in rows] == [None, "93", "93/a", "94", "94/a"]
    assert len({row.block_id for row, _ in rows}) == 5
    assert rows[2][0].text == "\tZobacz pkt 93 raportu"
    assert extractor.extract(data).splitlines()[2] == rows[2][0].text
