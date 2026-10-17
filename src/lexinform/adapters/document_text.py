"""Text of Word and OpenDocument files, of zip packages of them, and the extractor that picks the
right reader by the file's magic bytes.

On RCL the "Projekt" folders hold PDF 40%, DOCX/DOCM 43%, ZIP 7%, legacy DOC 6%; ODT is rare and
RTF, XLSX, MSG and .xades are not bill texts.

Both XML readers walk the tree themselves instead of `Element.iter()`: a .docx gives text boxes
twice, a nested table would be read twice, and in ODT tail text belongs after the children.
"""

import hashlib
import io
import logging
import re
import struct
import zipfile
import zlib
from collections.abc import Iterator
from xml.etree import ElementTree as ET
from zipfile import ZipInfo

from inflate64 import Inflater

from lexinform.document_versions import bill_subject, draft_version, regulation_support
from lexinform.law_digest import law_digest
from lexinform.models.documents import DocumentPart, DocumentSelection, ExtractedDocument
from lexinform.models.rcl import READABLE_EXTENSIONS, TextRole, text_rank, text_role
from lexinform.ports import TextExtractor
from lexinform.sections import APPENDIX_KINDS, PAGE_BREAK, Kind, document_kind, trim_print

log = logging.getLogger(__name__)

_WORDML_NAMESPACES = (
    "http://schemas.openxmlformats.org/wordprocessingml/2006/main",  # transitional: Word's default
    "http://purl.oclc.org/ooxml/wordprocessingml/main",  # "Strict Open XML Document"
    # Word 2003's single-file XML, still what some ministries save a draft as. Its element names
    # are the ones above — `w:body`, `w:p`, `w:r`, `w:t`, `w:tbl` — so the same walk reads it;
    # only the namespace and the root's name differ.
    "http://schemas.microsoft.com/office/word/2003/wordml",
)
_WORDML_ROOTS = ("document", "wordDocument")
_MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
# Word 2003 XML wraps the paragraphs of the body in its own layout hints — one `wx:sect` holding
# all 755 paragraphs of dokument 677202 — so a walk that only knows `w:p` and `w:tbl` finds an
# empty document. They carry nothing themselves and are looked through, like a content control.
_WX = "{http://schemas.microsoft.com/office/word/2003/auxHint}"
_TRANSPARENT = (f"{_WX}sect", f"{_WX}sub-section")
_TIDY_PAGE_BREAK = re.compile(rf"\n?{PAGE_BREAK}\n?")  # a break at a paragraph edge, once
_ODT_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_ODT_TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
_ODT_OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
_ODT_ROW_GROUPS = tuple(
    f"{_ODT_TABLE}{name}" for name in ("table-header-rows", "table-rows", "table-row-group")
)
_ODT_CELLS = (f"{_ODT_TABLE}table-cell", f"{_ODT_TABLE}covered-table-cell")
_ODT_CONTAINERS = (f"{_ODT_TEXT}section", f"{_ODT_TEXT}list", f"{_ODT_TEXT}list-item")
_MAX_ARCHIVE_DEPTH = 4
_MAX_ARCHIVE_BYTES = 128_000_000
_MAX_ARCHIVE_MEMBERS = 2048
_MAX_XML_BYTES = 32_000_000
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
            xml = _read_xml(archive, "word/document.xml")
            notes: dict[str, str] = {}
            for name, tag in (("footnotes", "footnote"), ("endnotes", "endnote")):
                path = f"word/{name}.xml"
                if path not in archive.namelist():
                    continue
                content = _read_xml(archive, path)
                if _declares_entities(content):
                    return ""
                root = ET.fromstring(content)
                namespace, _ = _split_tag(root.tag)
                reader = _WordMl(namespace)
                for note in root:
                    identity = note.get(reader.tag("id"))
                    if identity is not None:
                        notes[f"{tag}/{identity}"] = "\n".join(reader.blocks(note))
        if _declares_entities(xml):
            return ""
        return _wordml_text(ET.fromstring(xml), notes=notes)


def _wordml_text(root: ET.Element, *, notes: dict[str, str] | None = None) -> str:
    """Text of a `w:document` element, wherever it came from: `word/document.xml` inside a .docx
    or the same element inlined in a Flat OPC file."""
    namespace, local = _split_tag(root.tag)
    if local not in _WORDML_ROOTS or namespace not in _WORDML_NAMESPACES:
        log.warning("not a WordprocessingML document (root %s); no text", root.tag)
        return ""
    reader = _WordMl(namespace, notes=notes)
    body = root.find(reader.tag("body"))
    if body is None:
        log.warning("Word document without a body; no text")
        return ""
    text = "\n".join(reader.blocks(body))
    return _TIDY_PAGE_BREAK.sub(PAGE_BREAK, text)


_PKG = "{http://schemas.microsoft.com/office/2006/xmlPackage}"
_WORD_XML_MARKERS = (b"xmlPackage", b'progid="Word.Document"', b"wordDocument")


def _is_word_xml(head: bytes) -> bool:
    return head.lstrip()[:6] == b"<?xml " and any(m in head for m in _WORD_XML_MARKERS)


class WordXmlTextExtractor:
    """A Word document saved as one XML file, in either of the two shapes Word writes.

    Of the 53 XML members of RCL packages, 52 are **Flat OPC**, which inlines the OOXML package as
    `pkg:part` elements so the document is `/word/document.xml`. The other shape is **Word 2003
    XML**, whose root *is* the document with OOXML element names, so the same walk reads it —
    which is why "no `/word/document.xml` part" is not the same finding as "no text".
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
            log.warning("Word XML file not parsed (%s); no text", exc)
            return ""
        namespace, local = _split_tag(root.tag)
        if local in _WORDML_ROOTS and namespace in _WORDML_NAMESPACES:
            return _wordml_text(root)  # Word 2003 XML: the root is the document
        notes: dict[str, str] = {}
        for part in root.iter(f"{_PKG}part"):
            if part.get(f"{_PKG}name") not in ("/word/footnotes.xml", "/word/endnotes.xml"):
                continue
            payload = part.find(f"{_PKG}xmlData")
            if payload is None or not len(payload):
                continue
            namespace, _ = _split_tag(payload[0].tag)
            reader = _WordMl(namespace)
            for note in payload[0]:
                _, local = _split_tag(note.tag)
                identity = note.get(reader.tag("id"))
                if identity is not None:
                    notes[f"{local}/{identity}"] = "\n".join(reader.blocks(note))
        for part in root.iter(f"{_PKG}part"):
            if part.get(f"{_PKG}name") != "/word/document.xml":
                continue
            payload = part.find(f"{_PKG}xmlData")
            if payload is None or len(payload) == 0:
                log.warning("Flat OPC document part carries no XML; no text")
                return ""
            return _wordml_text(payload[0], notes=notes)
        log.warning("Word XML file with neither a document root nor a document part; no text")
        return ""


def _split_tag(tag: str) -> tuple[str, str]:
    """`{namespace}local` -> (namespace, local)."""
    if not tag.startswith("{"):
        return "", tag
    namespace, _, local = tag[1:].partition("}")
    return namespace, local


class _WordMl:
    """Block and inline structure of WordprocessingML in one namespace (transitional or strict)."""

    def __init__(self, namespace: str, *, notes: dict[str, str] | None = None) -> None:
        self._ns = f"{{{namespace}}}"
        self._notes = notes or {}

    def tag(self, name: str) -> str:
        return f"{self._ns}{name}"

    def blocks(self, node: ET.Element) -> Iterator[str]:
        """Paragraphs and table rows of a body or a table cell, in order."""
        for child in node:
            if child.tag == self.tag("p"):
                yield self.paragraph(child)
            elif child.tag == self.tag("tbl"):
                yield from self._table(child)
            elif child.tag in (self.tag("sdt"), self.tag("sdtContent"), *_TRANSPARENT):
                yield from self.blocks(child)  # content controls and Word 2003's layout hints

    def _table(self, table: ET.Element) -> Iterator[str]:
        for row in self._direct(table, "tr"):
            cells = [" ".join(self.blocks(cell)) for cell in self._direct(row, "tc")]
            yield "\t".join(cells)

    def _direct(self, node: ET.Element, name: str) -> Iterator[ET.Element]:
        """Direct children `name`, looking through the content controls that may wrap them."""
        for child in node:
            if child.tag == self.tag(name):
                yield child
            elif child.tag in (self.tag("sdt"), self.tag("sdtContent"), *_TRANSPARENT):
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
            elif el.tag in (self.tag("footnoteReference"), self.tag("endnoteReference")):
                local = "footnote" if el.tag == self.tag("footnoteReference") else "endnote"
                identity = el.get(self.tag("id"), "")
                note = self._notes.get(f"{local}/{identity}")
                if note:
                    parts.append(f" [przypis {identity}: {note}] ")
                else:
                    parts.append(f" [nieodczytany przypis {identity}] ")
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
            xml = _read_xml(archive, "content.xml")
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
        self._word_xml = WordXmlTextExtractor()
        self._max_member_bytes = max_member_bytes
        self._max_part_chars = max_part_chars

    def extract(self, data: bytes) -> str:
        return self.extract_document(data).text

    def extract_document(self, data: bytes) -> ExtractedDocument:
        parts: list[DocumentPart] = []
        rejected: list[str] = []
        self._collect(data, (), hashlib.sha256(data).hexdigest(), parts, rejected, [0, 0])
        if len(parts) == 1 and not parts[0].members:
            part = parts[0]
            return ExtractedDocument(
                text=part.text,
                selection=DocumentSelection(parts=(part,), rejected=tuple(rejected)),
            )
        return self._select_parts(parts, rejected)

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
        if _is_word_xml(head):
            return self._word_xml.extract(data)
        if b"%PDF-" in head:  # a PDF behind a preamble; checked after the containers, whose
            return self._pdf.extract(data)  # stored members may hold a PDF near the start
        log.warning("document of unknown format (starts with %r); no text", data[:8])
        return ""

    def _zip(self, data: bytes, *, depth: int) -> str:
        return self.extract_document(data).text

    def _collect(
        self,
        data: bytes,
        members: tuple[str, ...],
        container: str,
        parts: list[DocumentPart],
        rejected: list[str],
        usage: list[int],
    ) -> None:
        if data.startswith(_ZIP_MAGIC):
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    names = set(archive.namelist())
                    if "word/document.xml" in names:
                        text = self._docx.extract(data)
                    elif "content.xml" in names and "mimetype" in names:
                        text = self._odt.extract(data)
                    else:
                        if len(members) >= _MAX_ARCHIVE_DEPTH:
                            rejected.append(f"{members}: archive depth limit")
                            return
                        for info in sorted(archive.infolist(), key=lambda i: i.filename):
                            if not _is_readable(info.filename):
                                continue
                            usage[0] += info.file_size
                            usage[1] += 1
                            limit = self._max_member_bytes or 32_000_000
                            if (
                                info.file_size > limit
                                or usage[0] > _MAX_ARCHIVE_BYTES
                                or usage[1] > _MAX_ARCHIVE_MEMBERS
                            ):
                                rejected.append(f"{(*members, info.filename)}: unpacking limit")
                                continue
                            try:
                                payload = _read_member(archive, info, data, limit)
                                self._collect(
                                    payload,
                                    (*members, info.filename),
                                    container,
                                    parts,
                                    rejected,
                                    usage,
                                )
                            except Exception as exc:
                                rejected.append(
                                    f"{(*members, info.filename)}: {type(exc).__name__}: {exc}"
                                )
                        return
            except (zipfile.BadZipFile, ET.ParseError, ValueError) as exc:
                rejected.append(f"{members}: {type(exc).__name__}")
                return
        else:
            text = self._extract(data, depth=len(members))
        kind = document_kind(text) if text.strip() else "unknown"
        role = _ROLE_OF_KIND.get(kind, "unknown")
        pages = self.pages(data)
        losses = tuple(
            f"page {index}: no extracted text"
            for index, page in enumerate(text.split(PAGE_BREAK), 1)
            if pages and not page.strip()
        ) + tuple(re.findall(r"\[nieodczytany przypis [^\]]+\]", text))
        if pages and len(text.split(PAGE_BREAK)) != pages:
            losses += ("text page coverage differs from the PDF page count",)
        parts.append(
            DocumentPart(
                members=members,
                container_sha256=container,
                sha256=hashlib.sha256(data).hexdigest(),
                role=role,
                text=text,
                text_end=len(text),
                text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                pages=pages,
                page_start=1 if pages else None,
                page_end=pages or None,
                losses=losses if text.strip() else ("no extracted text",),
            )
        )

    def _select_parts(self, parts: list[DocumentPart], rejected: list[str]) -> ExtractedDocument:
        candidates: list[tuple[tuple[int, int, int, int], DocumentPart]] = []
        supporting: list[DocumentPart] = []
        annexes: list[DocumentPart] = []
        for part in parts:
            name = part.members[-1]
            base = name.rsplit("/", 1)[-1]
            kind = document_kind(part.text) if part.text.strip() else "unknown"
            if kind != "bill" and _normative_annex(base, part.text):
                annexes.append(part.model_copy(update={"role": "normative_annex"}))
                continue
            if kind in _NOT_THE_BILL or not part.text.strip():
                rejected.append(f"{part.members}: {kind}")
                continue
            role = _ROLE_OF_KIND.get(kind) or (_member_role(base) if kind == "unknown" else None)
            if role in ("justification", "osr"):
                if regulation_support(part.text, name=base):
                    rejected.append(f"{part.members}: supporting draft regulation")
                    continue
                supporting.append(part.model_copy(update={"role": role}))
                continue
            if role != "bill":
                continue
            if kind == "unknown" and self._max_part_chars and len(part.text) > self._max_part_chars:
                rejected.append(f"{part.members}: unrecognised long document")
                continue
            rank = (
                0 if kind == "bill" else 1,
                len(part.members),
                *text_rank(base, base.rsplit(".", 1)[-1].lower()),
            )
            candidates.append((rank, part.model_copy(update={"role": "bill"})))
        if not candidates:
            return ExtractedDocument(
                text=PAGE_BREAK.join(p.text for p in supporting),
                selection=DocumentSelection(
                    state="unsupported", parts=tuple(supporting), rejected=tuple(rejected)
                ),
            )
        candidates.sort(key=lambda item: (item[0], item[1].members))
        rank, main = candidates[0]
        peers = [p for r, p in candidates if r[0] == rank[0]]
        confirmed = [p for p in peers if document_kind(p.text) == "bill"]
        if len(confirmed) > 1:
            subjects = {bill_subject(p.text) for p in confirmed}
            dates = [draft_version(p.text) for p in confirmed]
            if len(subjects) == 1 and "" not in subjects and all(dates):
                newest = max(date for date in dates if date is not None)
                confirmed = [p for p in confirmed if draft_version(p.text) == newest]
                main = confirmed[0]
            identities = {law_digest(trim_print(p.text).text) or p.text.strip() for p in confirmed}
            if len(identities) > 1:
                return ExtractedDocument(
                    selection=DocumentSelection(
                        state="ambiguous", parts=tuple(confirmed), rejected=tuple(rejected)
                    )
                )
        family = main.members[:-1]
        kept = [main]
        kept.extend(p for p in annexes if p.members[:-1] == family)
        for role in ("justification", "osr"):
            matches = [p for p in supporting if p.role == role and p.members[:-1] == family]
            if matches:
                kept.append(
                    min(
                        matches,
                        key=lambda p: (
                            text_rank(p.members[-1], p.members[-1].rsplit(".", 1)[-1].lower()),
                            p.members,
                        ),
                    )
                )
        losses = tuple(rejected)
        return ExtractedDocument(
            text=PAGE_BREAK.join(p.text for p in kept),
            selection=DocumentSelection(
                parts=tuple(kept),
                rejected=losses,
                role=("bill heading",) if document_kind(main.text) == "bill" else (),
                completeness=()
                if losses or any(p.losses for p in kept)
                else ("all selected members extracted",),
            ),
        )


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


def _normative_annex(name: str, text: str) -> bool:
    heading = text[:1200].lower() if document_kind(text) == "annex" else ""
    label = name.lower()
    return bool(re.search(r"za[łl][aą]cznik.*do (?:niniejszej )?ustawy", heading + "\n" + label))


def _read_xml(archive: zipfile.ZipFile, name: str) -> bytes:
    if archive.getinfo(name).file_size > _MAX_XML_BYTES:
        raise ValueError("document XML size limit")
    with archive.open(name) as stream:
        content = stream.read(_MAX_XML_BYTES + 1)
    if len(content) > _MAX_XML_BYTES:
        raise ValueError("document XML size limit")
    return content


def _read_member(archive: zipfile.ZipFile, info: ZipInfo, data: bytes, limit: int) -> bytes:
    if info.compress_type != 9:
        with archive.open(info) as stream:
            result = stream.read(limit + 1)
    else:
        if info.flag_bits & 1:
            raise ValueError("encrypted Deflate64 member")
        offset = info.header_offset
        header = data[offset : offset + 30]
        if len(header) != 30 or header[:4] != b"PK\x03\x04":
            raise zipfile.BadZipFile("invalid local header")
        name_size, extra_size = struct.unpack_from("<HH", header, 26)
        start = offset + 30 + name_size + extra_size
        compressed = data[start : start + info.compress_size]
        inflater = Inflater()
        result = inflater.inflate(compressed, min(limit, info.file_size) + 1)
        if not inflater.eof:
            raise zipfile.BadZipFile("incomplete or oversized Deflate64 stream")
    if len(result) > limit or len(result) != info.file_size:
        raise zipfile.BadZipFile("unpacked member size mismatch")
    if zlib.crc32(result) != info.CRC:
        raise zipfile.BadZipFile("member CRC mismatch")
    return result
