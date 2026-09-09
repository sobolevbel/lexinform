"""Text of Word and OpenDocument files, of zip packages of them, and the extractor that picks the
right reader by the file's magic bytes.

On RCL (40 projects, September 2026) the "Projekt" folders hold PDF 40%, DOCX/DOCM 43%, ZIP 7%
(the whole package in one archive), legacy DOC 6% (see `doc_text.py`); ODT is rare, RTF, XLSX,
MSG and signature files (.xades) are not bill texts.
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
_ODT_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_ODT_TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
_ODT_OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
_ARCHIVE_MEMBER_EXTENSIONS = ("pdf", "docx", "docm", "doc", "odt", "zip")
_MAX_ARCHIVE_DEPTH = 2  # RCL packages come as "letter.pdf + projekt.zip": one nesting level
# The order the pieces of a bill package are read in: the bill, its uzasadnienie, the OSR, the
# rest (the trimming in `sections` then drops what the analysis does not need). Matched in the
# order listed: "OSR do projektu" is the OSR, not the bill.
_MEMBER_ORDER = ((1, ("uzasad",)), (2, ("osr", "ocena skutk")), (0, ("projekt", "ustaw")))


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


class OdtTextExtractor:
    """Paragraphs and headings of `content.xml`, tables as tab-separated rows."""

    def extract(self, data: bytes) -> str:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("content.xml")
        body = ET.fromstring(xml).find(f"{_ODT_OFFICE}body/{_ODT_OFFICE}text")
        if body is None:
            return ""
        return "\n".join(_odt_blocks(body))


def _odt_blocks(node: ET.Element) -> Iterator[str]:
    for child in node:
        if child.tag in (f"{_ODT_TEXT}p", f"{_ODT_TEXT}h"):
            yield _odt_paragraph(child)
        elif child.tag == f"{_ODT_TABLE}table":
            for row in child.iter(f"{_ODT_TABLE}table-row"):
                cells = [
                    " ".join(_odt_paragraph(p) for p in cell.iter(f"{_ODT_TEXT}p"))
                    for cell in row.findall(f"{_ODT_TABLE}table-cell")
                ]
                yield "\t".join(cells)
        elif child.tag in (f"{_ODT_TEXT}section", f"{_ODT_TEXT}list", f"{_ODT_TEXT}list-item"):
            yield from _odt_blocks(child)


def _odt_paragraph(p: ET.Element) -> str:
    parts: list[str] = []
    if p.text:
        parts.append(p.text)
    for el in p.iter():
        if el is p:
            continue
        if el.tag == f"{_ODT_TEXT}tab":
            parts.append("\t")
        elif el.tag == f"{_ODT_TEXT}line-break":
            parts.append("\n")
        elif el.tag == f"{_ODT_TEXT}s":
            parts.append(" " * int(el.get(f"{_ODT_TEXT}c", "1")))
        elif el.tag == f"{_ODT_TEXT}soft-page-break":
            parts.append(PAGE_BREAK)
        elif el.text:
            parts.append(el.text)
        if el.tail:
            parts.append(el.tail)
    return "".join(parts)


_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # OLE2 container: legacy .doc (also .xls, .ppt)
_ZIP_MAGIC = b"PK\x03\x04"  # .docx, .docm, .odt and plain archives all start like this


class DocumentTextExtractor:
    """Routes a file by its magic bytes: PDF, Word (.docx/.docm), legacy Word (.doc), OpenDocument
    (.odt) or a zip archive of such files (read member by member, bill first); anything else is
    empty."""

    def __init__(
        self,
        pdf: TextExtractor,
        docx: TextExtractor,
        doc: TextExtractor,
        odt: TextExtractor | None = None,
    ) -> None:
        self._pdf = pdf
        self._docx = docx
        self._doc = doc
        self._odt = odt or OdtTextExtractor()

    def extract(self, data: bytes) -> str:
        return self._extract(data, depth=0)

    def _extract(self, data: bytes, *, depth: int) -> str:
        head = data[:1024]
        if head.startswith(b"%PDF"):
            return self._pdf.extract(data)
        if head.startswith(_OLE_MAGIC):
            return self._doc.extract(data)
        if head.startswith(_ZIP_MAGIC):
            return self._zip(data, depth=depth)
        if b"%PDF-" in head:  # a PDF behind a preamble; checked after the containers, whose
            return self._pdf.extract(data)  # stored members may hold a PDF near the start
        log.warning("document of unknown format (starts with %r); no text", data[:8])
        return ""

    def _zip(self, data: bytes, *, depth: int) -> str:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
            if "word/document.xml" in names:
                return self._docx.extract(data)
            if "content.xml" in names and "mimetype" in names:
                return self._odt.extract(data)
            if depth >= _MAX_ARCHIVE_DEPTH:
                log.warning("archive nested too deep skipped")
                return ""
            members = sorted(
                (n for n in names if _archive_member(n)), key=lambda n: (_member_rank(n), n)
            )
            texts = [self._extract(archive.read(n), depth=depth + 1) for n in members]
        if not members:
            log.warning("archive holds no readable document; no text")
        return PAGE_BREAK.join(t for t in texts if t.strip())


def _archive_member(name: str) -> bool:
    base = name.rsplit("/", 1)[-1]
    if not base or base.startswith(".") or name.startswith("__MACOSX/"):
        return False
    return base.rsplit(".", 1)[-1].lower() in _ARCHIVE_MEMBER_EXTENSIONS


def _member_rank(name: str) -> int:
    base = name.rsplit("/", 1)[-1].lower()
    for rank, fragments in _MEMBER_ORDER:
        if any(fragment in base for fragment in fragments):
            return rank
    return len(_MEMBER_ORDER)
