"""Text of Word documents, and the extractor that picks PDF or Word by the file's magic bytes.

RCL publishes bills as .docx/.docm about as often as PDF, and about a tenth of its files as
legacy .doc (see `doc_text.py`).
"""

import io
import logging
import re
import zipfile
from collections.abc import Iterator
from xml.etree import ElementTree as ET

from lexinform.ports import TextExtractor
from lexinform.sections import PAGE_BREAK

log = logging.getLogger(__name__)

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_PAGE_BREAKS = (f"{_W}lastRenderedPageBreak",)
_TIDY_PAGE_BREAK = re.compile(rf"\n?{PAGE_BREAK}\n?")  # a break at a paragraph edge, once


class DocxTextExtractor:
    """Paragraphs of `word/document.xml`, tables as tab-separated rows, `\\f` at page breaks."""

    def extract(self, data: bytes) -> str:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("word/document.xml")
        body = ET.fromstring(xml).find(f"{_W}body")
        if body is None:
            return ""
        text = "\n".join(_blocks(body))
        return _TIDY_PAGE_BREAK.sub(PAGE_BREAK, text)


def _blocks(node: ET.Element) -> Iterator[str]:
    for child in node:
        if child.tag == f"{_W}p":
            yield _paragraph(child)
        elif child.tag == f"{_W}tbl":
            for row in child.iter(f"{_W}tr"):
                cells = [
                    " ".join(_paragraph(p) for p in cell.iter(f"{_W}p"))
                    for cell in row.findall(f"{_W}tc")
                ]
                yield "\t".join(cells)
        elif child.tag in (f"{_W}sdt", f"{_W}sdtContent"):
            yield from _blocks(child)  # content controls wrap ordinary paragraphs


def _paragraph(p: ET.Element) -> str:
    parts: list[str] = []
    for el in p.iter():
        if el.tag == f"{_W}t":
            parts.append(el.text or "")
        elif el.tag == f"{_W}tab":
            parts.append("\t")
        elif el.tag == f"{_W}br":
            parts.append(PAGE_BREAK if el.get(f"{_W}type") == "page" else "\n")
        elif el.tag == f"{_W}cr":
            parts.append("\n")
        elif el.tag in _PAGE_BREAKS:
            parts.append(PAGE_BREAK)
    return "".join(parts)


_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # OLE2 container: legacy .doc (also .xls, .ppt)


class DocumentTextExtractor:
    """Routes a file to the PDF, the Word (.docx) or the legacy Word (.doc) extractor by its magic
    bytes; anything else is empty."""

    def __init__(self, pdf: TextExtractor, docx: TextExtractor, doc: TextExtractor) -> None:
        self._pdf = pdf
        self._docx = docx
        self._doc = doc

    def extract(self, data: bytes) -> str:
        head = data[:1024]
        if head.startswith(b"%PDF") or b"%PDF-" in head:
            return self._pdf.extract(data)
        if head.startswith(b"PK\x03\x04"):
            return self._docx.extract(data)
        if head.startswith(_OLE_MAGIC):
            return self._doc.extract(data)
        log.warning("document of unknown format (starts with %r); no text", data[:8])
        return ""
