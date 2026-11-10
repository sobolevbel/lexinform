import io
from hashlib import sha256
from zipfile import ZipFile

from lexinform.adapters.document_text import DocxTextExtractor
from lexinform.models.documents import DocumentPart

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


def test_consultation_spans_preserve_internal_response_tabs_and_narrative() -> None:
    rejected = (
        "Konfederacja Lewiatan zgłosiła uwagę do art. 16. "
        "Uwaga nie została uwzględniona. Zachowano termin 90 dni."
    )
    accepted = "Rzecznicy zgłosili uwagę do art. 40, która została uwzględniona."
    row = _row(
        "<w:r><w:t>611</w:t></w:r>",
        "Uwaga niezasadna. Termin:</w:t><w:tab/><w:t>12 miesięcy.",
    )
    output = io.BytesIO()
    with ZipFile(output, "w") as package:
        package.writestr(
            "word/document.xml",
            f'<w:document xmlns:w="{_WORD}"><w:body>'
            "<w:p><w:r><w:t>Zakres raportu.</w:t></w:r></w:p>"
            f"<w:p><w:r><w:t>{rejected}</w:t></w:r></w:p>"
            f"<w:tbl>{row}</w:tbl>"
            f"<w:p><w:r><w:t>{accepted}</w:t></w:r></w:p></w:body></w:document>",
        )
    extractor = DocxTextExtractor()
    data = output.getvalue()
    text = extractor.extract(data)
    spans = extractor.consultation_row_spans(data, text)

    assert len(extractor.extract_consultation_rows(data)) == 1
    assert [(span.kind, span.label, span.response_offset) for span in spans] == [
        ("paragraph", None, None),
        ("row", "611", 4),
        ("paragraph", None, None),
    ]
    response = "Uwaga niezasadna. Termin:\t12 miesięcy."
    assert [text[span.start : span.end] for span in spans] == [
        rejected,
        "611\t" + response,
        accepted,
    ]

    archive = io.BytesIO()
    with ZipFile(archive, "w") as package:
        package.writestr("first.docx", data)
        package.writestr("second.docx", data)
    parts = tuple(
        DocumentPart(members=(name,), sha256=sha256(data).hexdigest(), text=text)
        for name in ("first.docx", "second.docx")
    )
    relocated = extractor.consultation_archive_row_spans(archive.getvalue(), parts)
    assert len({span.locator for span in relocated}) == len(relocated) == 6
    second_row = relocated[4]
    assert second_row.kind == "row" and second_row.response_offset == 4
    combined = "\n\n".join(part.text for part in parts)
    assert combined[second_row.start + 4 : second_row.end] == response
