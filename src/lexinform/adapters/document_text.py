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

from lexinform.models.rcl import READABLE_EXTENSIONS, TextRole, text_rank, text_role
from lexinform.ports import TextExtractor
from lexinform.sections import APPENDIX_KINDS, PAGE_BREAK, Kind, document_kind

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
_MAX_SPACES = 4096  # `<text:s text:c="N"/>`: the count is the file's to choose
# The order the pieces of a bill package are read in: the bill, its uzasadnienie, the OSR (the
# trimming in `sections` then drops what the analysis does not need).
_ROLE_ORDER: dict[TextRole, int] = {"bill": 0, "justification": 1, "osr": 2}


class DocxTextExtractor:
    """Paragraphs of `word/document.xml`, tables as tab-separated rows, `\\f` at page breaks."""

    def pages(self, data: bytes) -> int:
        """Word paginates when it renders; the file does not say how many pages it has."""
        return 0

    def select_pages(self, data: bytes, *, first: int, count: int) -> bytes:
        return data

    def extract(self, data: bytes) -> str:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("word/document.xml")
        if _declares_entities(xml):
            return ""
        return _wordml_text(ET.fromstring(xml))


def _wordml_text(root: ET.Element) -> str:
    """Text of a `w:document` element, wherever it came from: `word/document.xml` inside a .docx
    or the same element inlined in a Flat OPC file."""
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


_PKG = "{http://schemas.microsoft.com/office/2006/xmlPackage}"
_FLAT_OPC_MARKERS = (b"xmlPackage", b'progid="Word.Document"')


def _is_flat_opc(head: bytes) -> bool:
    return head.lstrip()[:6] == b"<?xml " and any(m in head for m in _FLAT_OPC_MARKERS)


class FlatOpcTextExtractor:
    """Word's "Word XML Document" (Flat OPC): the whole OOXML package inlined in one XML file.

    Ministries save a draft this way and file it as `projekt ustawy.xml`, and the router saw an
    opening `<?xml` it had no reader for. Measured over the corpus (14 Sept 2026): **52 of the 53
    XML members of RCL packages are Flat OPC**, and they are the bill, its uzasadnienie and its
    OSR — `2020.10.26_UC44_projekt ustawy.xml`, `Uzasadnienie.xml`, `OSR.xml`. Three packages
    yielded no text at all because of it, among them the bill of the kooperatywy mieszkaniowe
    project, whose only other member is the letter that transmits it.

    The parts are `pkg:part` elements keyed by their path in the package, so the document is the
    one named `/word/document.xml` and its content is a `w:document` element like any other.
    """

    def pages(self, data: bytes) -> int:
        return 0

    def select_pages(self, data: bytes, *, first: int, count: int) -> bytes:
        return data

    def extract(self, data: bytes) -> str:
        if _declares_entities(data):
            return ""
        try:
            root = ET.fromstring(data)
        except ET.ParseError as exc:
            log.warning("Flat OPC file not parsed (%s); no text", exc)
            return ""
        for part in root.iter(f"{_PKG}part"):
            if part.get(f"{_PKG}name") != "/word/document.xml":
                continue
            payload = part.find(f"{_PKG}xmlData")
            if payload is None or len(payload) == 0:
                log.warning("Flat OPC document part carries no XML; no text")
                return ""
            return _wordml_text(payload[0])
        log.warning("Flat OPC file without a /word/document.xml part; no text")
        return ""


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

    def pages(self, data: bytes) -> int:
        return 0

    def select_pages(self, data: bytes, *, first: int, count: int) -> bytes:
        return data

    def extract(self, data: bytes) -> str:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("content.xml")
        if _declares_entities(xml):
            return ""
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


def _space_count(value: str | None) -> int:
    try:
        count = int(value) if value else 1
    except ValueError:
        return 1
    return max(0, min(count, _MAX_SPACES))


def _declares_entities(xml: bytes) -> bool:
    """Word and LibreOffice never write a DOCTYPE; an XML parser expands what one declares."""
    if b"<!DOCTYPE" not in xml[:4096]:
        return False
    log.warning("document XML declares a DOCTYPE, which no word processor writes; no text")
    return True


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
            parts.append(" " * _space_count(el.get(f"{_ODT_TEXT}c")))
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
    sees the compressed bytes. `max_part_chars` is the length past which a member that does not
    name itself is not taken for a bill text.
    """

    def __init__(
        self,
        pdf: TextExtractor,
        docx: TextExtractor,
        doc: TextExtractor,
        odt: TextExtractor | None = None,
        *,
        max_member_bytes: int | None = None,
        max_part_chars: int | None = None,
    ) -> None:
        self._pdf = pdf
        self._docx = docx
        self._doc = doc
        self._odt = odt or OdtTextExtractor()
        self._flat_opc = FlatOpcTextExtractor()
        self._max_member_bytes = max_member_bytes
        self._max_part_chars = max_part_chars

    def extract(self, data: bytes) -> str:
        return self._extract(data, depth=0)

    def pages(self, data: bytes) -> int:
        """Only a PDF has pages of its own. A Word file paginates when it is rendered, and an
        archive is not a document at all."""
        return self._pdf.pages(data) if self._is_pdf(data) else 0

    def select_pages(self, data: bytes, *, first: int, count: int) -> bytes:
        return (
            self._pdf.select_pages(data, first=first, count=count) if self._is_pdf(data) else data
        )

    @staticmethod
    def _is_pdf(data: bytes) -> bool:
        head = data[:1024]
        return head.startswith(b"%PDF") or (b"%PDF-" in head and not head.startswith(_ZIP_MAGIC))

    def _extract(self, data: bytes, *, depth: int) -> str:
        head = data[:1024]
        if head.startswith(b"%PDF"):
            return self._pdf.extract(data)
        if head.startswith(_OLE_MAGIC):
            return self._doc.extract(data)
        if head.startswith(_ZIP_MAGIC):
            return self._zip(data, depth=depth)
        if _is_flat_opc(head):
            return self._flat_opc.extract(data)
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
        """The bill, its uzasadnienie and its OSR, chosen after reading every member.

        An archive is never handed on as one opaque thing: it is unpacked, each member is read
        and asked what it is, and only what the model needs is kept. A nested archive is opened
        only when the bill is not out here — the three seen on RCL (13 Sept 2026) were bundles
        of draft regulations, whose members are named `projekt.docx`, `uzasadnienie.docx` and
        `OSR.doc` and are told from the bill's own files by their text alone.
        """
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
            if "word/document.xml" in names:
                return self._docx.extract(data)
            if "content.xml" in names and "mimetype" in names:
                return self._odt.extract(data)
            if depth >= _MAX_ARCHIVE_DEPTH:
                log.warning("archive nested too deep skipped")
                return ""
            loose = [i for i in archive.infolist() if _is_readable(i.filename) and not _is_zip(i)]
            parts = self._pick_parts(archive, loose, depth=depth)
            if "bill" not in parts:
                parts = self._from_nested(archive, parts, depth=depth)
        if not parts:
            log.warning("archive holds no readable document; no text")
        return PAGE_BREAK.join(parts[role] for role in _ROLE_ORDER if parts.get(role, "").strip())

    def _pick_parts(
        self, archive: zipfile.ZipFile, infos: list[ZipInfo], *, depth: int
    ) -> dict[TextRole, str]:
        """The best member for each role, its own text deciding what it is.

        Content vetoes always and chooses where it can: a member whose text says it is a draft
        regulation, a compliance table, a consultation report or a letter is never kept, whatever
        its name; among the rest, one whose text names the role beats one that only carries the
        right file name, which is the fallback for a layout `document_kind` does not know.
        """
        best: dict[TextRole, tuple[tuple[int, int, int], str]] = {}
        for info in infos:
            text = self._member_text(archive, info, depth=depth)
            kind = document_kind(text) if text.strip() else "unknown"
            if kind in _NOT_THE_BILL:
                log.info("%s is a %s; not sent", info.filename, kind)
                continue
            base = info.filename.rsplit("/", 1)[-1]
            role = _ROLE_OF_KIND.get(kind) or (_member_role(base) if kind == "unknown" else None)
            if role is None:
                continue
            if kind == "unknown" and self._max_part_chars and len(text) > self._max_part_chars:
                # A bill, its uzasadnienie and its OSR name themselves in their first lines: all
                # eighteen measured on 13 Sept 2026 did. A member this long that names itself as
                # nothing is the runaway case the file name would otherwise wave through — the
                # 954k-character consultation report of UD439 was filed as one.
                log.warning("%s is %d chars and says nothing of itself; not sent", base, len(text))
                continue
            rank = (0 if kind != "unknown" else 1, *_member_rank(info))
            if role not in best or rank < best[role][0]:
                best[role] = (rank, text)
        for role, (_, text) in best.items():
            log.info("package: %s, %d chars", role, len(text))
        return {role: text for role, (_, text) in best.items()}

    def _from_nested(
        self, archive: zipfile.ZipFile, parts: dict[TextRole, str], *, depth: int
    ) -> dict[TextRole, str]:
        """The bill from an archive inside the archive, when it is nowhere outside it."""
        for info in archive.infolist():
            if not _is_zip(info):
                continue
            inner = self._member_text(archive, info, depth=depth)
            if inner.strip():
                log.info("%s holds the bill; read from there", info.filename)
                return {"bill": inner}
        return parts

    def _member_text(self, archive: zipfile.ZipFile, member: ZipInfo, *, depth: int) -> str:
        if self._max_member_bytes is not None and member.file_size > self._max_member_bytes:
            log.warning(
                "%s unpacks to %d bytes, over the %d limit; skipped",
                member.filename,
                member.file_size,
                self._max_member_bytes,
            )
            return ""
        try:
            data = archive.read(member)
        except Exception as exc:
            # Encrypted, AES, a compression method zipfile does not implement, a broken stream:
            # the other members of the package are still worth reading.
            log.warning(
                "%s not unpacked (%s: %s); skipped", member.filename, type(exc).__name__, exc
            )
            return ""
        return self._extract(data, depth=depth + 1)


_ROLE_OF_KIND: dict[Kind, TextRole] = {
    "bill": "bill",
    "justification": "justification",
    "osr": "osr",
}

_NOT_THE_BILL = APPENDIX_KINDS | {"letter"}
"""What a package member may not be. The appendices, and a letter: inside a package the covering
letter is a file of its own, not the opening of the document that follows it."""


def _member_rank(info: ZipInfo) -> tuple[int, int]:
    base = info.filename.rsplit("/", 1)[-1]
    return text_rank(base, base.rsplit(".", 1)[-1].lower())


def _is_zip(info: ZipInfo) -> bool:
    return info.filename.rsplit(".", 1)[-1].lower() == "zip"


def _is_readable(name: str) -> bool:
    base = name.rsplit("/", 1)[-1]
    if not base or base.startswith(".") or name.startswith("__MACOSX/"):
        return False
    return base.rsplit(".", 1)[-1].lower() in READABLE_EXTENSIONS


def _member_role(name: str) -> TextRole | None:
    base = name.rsplit("/", 1)[-1]
    if not base or base.startswith(".") or name.startswith("__MACOSX/"):
        return None
    if base.rsplit(".", 1)[-1].lower() not in READABLE_EXTENSIONS:
        return None
    return text_role(base)
