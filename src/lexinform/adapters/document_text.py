"""Text of Word and OpenDocument files, of zip packages of them, and the extractor that picks the
right reader by the file's magic bytes.

On RCL (40 projects, September 2026) the "Projekt" folders hold PDF 40%, DOCX/DOCM 43%, ZIP 7%
(the whole package in one archive), legacy DOC 6% (see `doc_text.py`); ODT is rare, RTF, XLSX,
MSG and signature files (.xades) are not bill texts.

Both XML readers walk the tree themselves instead of `Element.iter()`: text boxes come twice in
a .docx (`mc:Choice` and `mc:Fallback`), a table nested in a cell would be read once as part of
the cell and once as rows of its own, and in ODT the tail text of an element belongs after its
children, not before.
"""

import io
import logging
import re
import zipfile
from collections.abc import Iterator
from xml.etree import ElementTree as ET
from zipfile import ZipInfo

from lexinform.models.rcl import READABLE_EXTENSIONS, TextRole, text_role
from lexinform.ports import TextExtractor
from lexinform.sections import PAGE_BREAK

log = logging.getLogger(__name__)

_WORDML_NAMESPACES = (
    "http://schemas.openxmlformats.org/wordprocessingml/2006/main",  # transitional: Word's default
    "http://purl.oclc.org/ooxml/wordprocessingml/main",  # "Strict Open XML Document"
)
_MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
_TIDY_PAGE_BREAK = re.compile(rf"\n?{PAGE_BREAK}\n?")  # a break at a paragraph edge, once
_ODT_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_ODT_TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
_ODT_OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
_ODT_ROW_GROUPS = tuple(
    f"{_ODT_TABLE}{name}" for name in ("table-header-rows", "table-rows", "table-row-group")
)
_ODT_CELLS = (f"{_ODT_TABLE}table-cell", f"{_ODT_TABLE}covered-table-cell")
_ODT_CONTAINERS = (f"{_ODT_TEXT}section", f"{_ODT_TEXT}list", f"{_ODT_TEXT}list-item")
_MAX_ARCHIVE_DEPTH = 2  # RCL packages come as "letter.pdf + projekt.zip": one nesting level
# The order the pieces of a bill package are read in: the bill, its uzasadnienie, the OSR (the
# trimming in `sections` then drops what the analysis does not need).
_ROLE_ORDER: dict[TextRole, int] = {"bill": 0, "justification": 1, "osr": 2}


class DocxTextExtractor:
    """Paragraphs of `word/document.xml`, tables as tab-separated rows, `\\f` at page breaks."""

    def extract(self, data: bytes) -> str:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("word/document.xml")
        root = ET.fromstring(xml)
        namespace, local = _split_tag(root.tag)
        if local != "document" or namespace not in _WORDML_NAMESPACES:
            log.warning("not a WordprocessingML document (root %s); no text", root.tag)
            return ""
        reader = _WordMl(namespace)
        body = root.find(reader.tag("body"))
        if body is None:
            log.warning("Word document without a body; no text")
            return ""
        text = "\n".join(reader.blocks(body))
        return _TIDY_PAGE_BREAK.sub(PAGE_BREAK, text)


def _split_tag(tag: str) -> tuple[str, str]:
    """`{namespace}local` -> (namespace, local)."""
    if not tag.startswith("{"):
        return "", tag
    namespace, _, local = tag[1:].partition("}")
    return namespace, local


class _WordMl:
    """Block and inline structure of WordprocessingML in one namespace (transitional or strict)."""

    def __init__(self, namespace: str) -> None:
        self._ns = f"{{{namespace}}}"

    def tag(self, name: str) -> str:
        return f"{self._ns}{name}"

    def blocks(self, node: ET.Element) -> Iterator[str]:
        """Paragraphs and table rows of a body or a table cell, in order."""
        for child in node:
            if child.tag == self.tag("p"):
                yield self.paragraph(child)
            elif child.tag == self.tag("tbl"):
                yield from self._table(child)
            elif child.tag in (self.tag("sdt"), self.tag("sdtContent")):
                yield from self.blocks(child)  # content controls wrap ordinary paragraphs

    def _table(self, table: ET.Element) -> Iterator[str]:
        for row in self._direct(table, "tr"):
            cells = [" ".join(self.blocks(cell)) for cell in self._direct(row, "tc")]
            yield "\t".join(cells)

    def _direct(self, node: ET.Element, name: str) -> Iterator[ET.Element]:
        """Direct children `name`, looking through the content controls that may wrap them."""
        for child in node:
            if child.tag == self.tag(name):
                yield child
            elif child.tag in (self.tag("sdt"), self.tag("sdtContent")):
                yield from self._direct(child, name)

    def paragraph(self, p: ET.Element) -> str:
        parts: list[str] = []
        self._inline(p, parts)
        return "".join(parts)

    def _inline(self, node: ET.Element, parts: list[str]) -> None:
        for el in node:
            if el.tag == _MC_FALLBACK:
                continue  # the same text box again, drawn for old Word versions
            if el.tag == self.tag("t"):
                parts.append(el.text or "")
            elif el.tag == self.tag("tab"):
                parts.append("\t")
            elif el.tag == self.tag("br"):
                parts.append(PAGE_BREAK if el.get(self.tag("type")) == "page" else "\n")
            elif el.tag == self.tag("cr"):
                parts.append("\n")
            elif el.tag == self.tag("lastRenderedPageBreak"):
                parts.append(PAGE_BREAK)
            else:
                self._inline(el, parts)  # runs, hyperlinks, fields, insertions, drawings


class OdtTextExtractor:
    """Paragraphs and headings of `content.xml`, tables as tab-separated rows."""

    def extract(self, data: bytes) -> str:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("content.xml")
        body = ET.fromstring(xml).find(f"{_ODT_OFFICE}body/{_ODT_OFFICE}text")
        if body is None:
            log.warning("OpenDocument file without a text body; no text")
            return ""
        return "\n".join(_odt_blocks(body))


def _odt_blocks(node: ET.Element) -> Iterator[str]:
    for child in node:
        if child.tag in (f"{_ODT_TEXT}p", f"{_ODT_TEXT}h"):
            yield _odt_paragraph(child)
        elif child.tag == f"{_ODT_TABLE}table":
            for row in _odt_rows(child):
                cells = [" ".join(_odt_blocks(cell)) for cell in row if cell.tag in _ODT_CELLS]
                yield "\t".join(cells)
        elif child.tag in _ODT_CONTAINERS:
            yield from _odt_blocks(child)


def _odt_rows(table: ET.Element) -> Iterator[ET.Element]:
    """The rows of one table (not of the tables nested in its cells), through the row groups."""
    for child in table:
        if child.tag == f"{_ODT_TABLE}table-row":
            yield child
        elif child.tag in _ODT_ROW_GROUPS:
            yield from _odt_rows(child)


def _odt_paragraph(p: ET.Element) -> str:
    parts: list[str] = [p.text or ""]
    _odt_inline(p, parts)
    return "".join(parts)


def _odt_inline(node: ET.Element, parts: list[str]) -> None:
    for el in node:
        if el.tag == f"{_ODT_TEXT}tab":
            parts.append("\t")
        elif el.tag == f"{_ODT_TEXT}line-break":
            parts.append("\n")
        elif el.tag == f"{_ODT_TEXT}s":
            parts.append(" " * int(el.get(f"{_ODT_TEXT}c", "1")))
        elif el.tag == f"{_ODT_TEXT}soft-page-break":
            parts.append(PAGE_BREAK)
        else:
            if el.text:
                parts.append(el.text)
            _odt_inline(el, parts)  # spans, links, notes: their text, then their children
        if el.tail:
            parts.append(el.tail)


_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # OLE2 container: legacy .doc (also .xls, .ppt)
_ZIP_MAGIC = b"PK\x03\x04"  # .docx, .docm, .odt and plain archives all start like this


class DocumentTextExtractor:
    """Routes a file by its magic bytes: PDF, Word (.docx/.docm), legacy Word (.doc), OpenDocument
    (.odt) or a zip archive of such files (read member by member, bill first; letters, tables and
    appendices skipped by name); anything else is empty.

    `max_member_bytes` caps the *unpacked* size of an archive member: the download limit only
    sees the compressed bytes.
    """

    def __init__(
        self,
        pdf: TextExtractor,
        docx: TextExtractor,
        doc: TextExtractor,
        odt: TextExtractor | None = None,
        *,
        max_member_bytes: int | None = None,
    ) -> None:
        self._pdf = pdf
        self._docx = docx
        self._doc = doc
        self._odt = odt or OdtTextExtractor()
        self._max_member_bytes = max_member_bytes

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
        try:
            return self._zip_members(data, depth=depth)
        except zipfile.BadZipFile as exc:
            log.warning("zip container not read (%s); no text", exc)
            return ""

    def _zip_members(self, data: bytes, *, depth: int) -> str:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
            if "word/document.xml" in names:
                return self._docx.extract(data)
            if "content.xml" in names and "mimetype" in names:
                return self._odt.extract(data)
            if depth >= _MAX_ARCHIVE_DEPTH:
                log.warning("archive nested too deep skipped")
                return ""
            members = _package_members(archive.infolist())
            texts = [self._member_text(archive, m, depth=depth) for m in members]
        if not members:
            log.warning("archive holds no readable document; no text")
        return PAGE_BREAK.join(t for t in texts if t.strip())

    def _member_text(self, archive: zipfile.ZipFile, member: ZipInfo, *, depth: int) -> str:
        if self._max_member_bytes is not None and member.file_size > self._max_member_bytes:
            log.warning(
                "%s unpacks to %d bytes, over the %d limit; skipped",
                member.filename,
                member.file_size,
                self._max_member_bytes,
            )
            return ""
        return self._extract(archive.read(member), depth=depth + 1)


def _package_members(infos: list[ZipInfo]) -> list[ZipInfo]:
    """The members worth reading, the bill first, then its uzasadnienie, then the OSR."""
    ranked = [(role, info) for info in infos if (role := _member_role(info.filename)) is not None]
    ranked.sort(key=lambda pair: (_ROLE_ORDER[pair[0]], pair[1].filename))
    return [info for _, info in ranked]


def _member_role(name: str) -> TextRole | None:
    base = name.rsplit("/", 1)[-1]
    if not base or base.startswith(".") or name.startswith("__MACOSX/"):
        return None
    if base.rsplit(".", 1)[-1].lower() not in READABLE_EXTENSIONS:
        return None
    return text_role(base)
