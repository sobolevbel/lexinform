"""Text of Word and OpenDocument files, of zip packages of them, and the extractor that picks the
right reader by the file's magic bytes.

On RCL the "Projekt" folders hold PDF 40%, DOCX/DOCM 43%, ZIP 7%, legacy DOC 6%; ODT is rare and
RTF, XLSX, MSG and .xades are not bill texts.

Both XML readers walk the tree themselves instead of `Element.iter()`: a .docx gives text boxes
twice, a nested table would be read twice, and in ODT tail text belongs after the children.
"""

import hashlib
import importlib.metadata
import inspect
import io
import logging
import re
import struct
import sys
import zipfile
import zlib
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from functools import partial
from typing import IO, Literal
from xml.etree import ElementTree as ET
from zipfile import ZipInfo

from inflate64 import Inflater

import lexinform.physical_assembly_encoding as physical_assembly_encoding
import lexinform.physical_assembly_inputs as physical_assembly_inputs
import lexinform.physical_assembly_integrity as physical_assembly_integrity
import lexinform.physical_header_capture as physical_header_capture
import lexinform.physical_root_assembly as physical_root_assembly
import lexinform.physical_walk_capture as physical_walk_capture
from lexinform.document_candidates import select_document_parts
from lexinform.extraction_records import component_code, extraction_component, summarize_extraction
from lexinform.models.documents import (
    DocumentPart,
    ExtractedDocument,
    ExtractedParts,
    OdtIgnoredNode,
    OdtPartExtraction,
    OdtTextBlock,
    OdtTextExtraction,
    PdfPartExtraction,
    PdfTextExtraction,
    UnlocatedPartExtraction,
    UnlocatedTextExtraction,
    WordBodyState,
    WordDetailedExtraction,
    WordPartExtraction,
    WordPartNoteExtraction,
    WordTextBlock,
    WordTextExtraction,
)
from lexinform.models.extractions import DocumentExtraction, ExtractionOptions
from lexinform.models.physical_inventory import (
    DecodedPath,
    HeaderRow,
    PhysicalCaptureOptions,
    PhysicalCaptureRecipe,
    PhysicalCodeFile,
    PhysicalDependency,
    PhysicalHeaders,
    PhysicalImplementation,
    PhysicalMetadata,
    PhysicalOmission,
    PhysicalOmittedCount,
)
from lexinform.models.rcl import READABLE_EXTENSIONS, text_rank
from lexinform.models.word_notes import (
    NotePartName,
    WordNote,
    WordNoteCall,
    WordNoteExtraction,
    WordNoteXmlPart,
)
from lexinform.physical_header_capture import PhysicalHeaderBuilder
from lexinform.physical_walk_capture import (
    PhysicalWalkCapture,
    current_physical_walk_capture,
    suspend_physical_walk_capture,
)
from lexinform.ports import (
    OdtBlockTextExtractor,
    PdfPageTextExtractor,
    TextExtractor,
    WordBlockTextExtractor,
    WordNoteTextExtractor,
)
from lexinform.sections import (
    PAGE_BREAK,
    reads_through_justification,
)
from lexinform.word_projection import legacy_word_text

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


class _WordNoteOrigins:
    def __init__(self) -> None:
        self.parts: list[WordNoteXmlPart] = []
        self.notes: list[WordNote] = []
        self.calls: list[WordNoteCall] = []
        self.losses: list[str] = []
        self.targets: dict[str, int] = {}
        self.seen: set[str] = set()

    def part(self, name: NotePartName, content: bytes, root: ET.Element | None) -> bool:
        namespace, local = _split_tag(root.tag) if root is not None else ("", "")
        supported = namespace in _WORDML_NAMESPACES and local == name[5:-4]
        self.parts.append(
            WordNoteXmlPart(
                name=name,
                sha256=hashlib.sha256(content).hexdigest(),
                state="refused" if root is None else "read" if supported else "unsupported",
            )
        )
        return supported

    def note(
        self, part: NotePartName, index: int, identity: str, text: str, *, supported: bool
    ) -> None:
        kind = "footnote" if part == "word/footnotes.xml" else "endnote"
        key = f"{kind}/{identity}"
        if key in self.seen:
            self.losses.append(f"duplicate note ID: {key}")
        self.seen.add(key)
        if supported:
            self.targets[key] = len(self.notes)
            self.notes.append(WordNote(xml_part=part, path=(index,), identity=identity, text=text))
        else:
            self.targets.pop(key, None)
            self.losses.append("note entry outside the supported XML view")

    def reference(
        self,
        kind: Literal["footnote", "endnote"],
        identity: str,
        path: tuple[int, ...],
        paragraph: tuple[int, ...],
        legacy: dict[str, str],
    ) -> None:
        key = f"{kind}/{identity}"
        target = self.targets.get(key)
        state: Literal["expanded", "empty", "missing", "unverified"]
        part = "word/footnotes.xml" if kind == "footnote" else "word/endnotes.xml"
        if target is not None:
            state = "expanded" if self.notes[target].text else "empty"
        elif key in legacy or any(
            item.name == part and item.state != "read" for item in self.parts
        ):
            state = "unverified"
        else:
            state = "missing"
        self.calls.append(
            WordNoteCall(
                path=path,
                paragraph_path=paragraph,
                kind=kind,
                identity=identity,
                state=state,
                target=target,
            )
        )


class DocxTextExtractor:
    """Paragraphs of `word/document.xml`, tables as tab-separated rows, `\\f` at page breaks."""

    def pages(self, data: bytes) -> int:
        """Word paginates when it renders; the file does not say how many pages it has."""
        return 0

    def select_pages(self, data: bytes, *, first: int, count: int) -> bytes:
        return data

    def extract(self, data: bytes) -> str:
        return self._read(data)[0]

    def extract_blocks(self, data: bytes) -> WordTextExtraction:
        return self._blocks(data)

    def extract_with_notes(self, data: bytes) -> WordDetailedExtraction:
        notes = _WordNoteOrigins()
        body = self._blocks(data, notes=notes)
        return WordDetailedExtraction(
            body=body,
            notes=WordNoteExtraction(
                input_sha256=body.input_sha256,
                body_manifest_sha256=body.manifest_sha256,
                parts=tuple(notes.parts),
                notes=tuple(notes.notes),
                calls=tuple(notes.calls),
                losses=tuple(dict.fromkeys(notes.losses)),
            ),
        )

    def _blocks(self, data: bytes, *, notes: _WordNoteOrigins | None = None) -> WordTextExtraction:
        blocks: list[WordTextBlock] = []
        projection: list[str] = []
        text, xml_sha256, state = self._read(
            data, blocks=blocks, projection=projection, origins=notes
        )
        result = WordTextExtraction(
            input_sha256=hashlib.sha256(data).hexdigest(),
            xml_sha256=xml_sha256,
            python_version=sys.version.split()[0],
            body_state=state,
            blocks=tuple(sorted(blocks, key=lambda block: block.path)),
            legacy_blocks=tuple(projection),
        )
        if result.legacy_text != text:
            raise ValueError("Word block projection differs from the legacy text view")
        return result

    def _read(
        self,
        data: bytes,
        *,
        blocks: list[WordTextBlock] | None = None,
        projection: list[str] | None = None,
        origins: _WordNoteOrigins | None = None,
    ) -> tuple[str, str, WordBodyState]:
        with _physical_zip(data) as archive:
            xml = _read_xml(archive, "word/document.xml")
            notes: dict[str, str] = {}
            for name, tag in (("footnotes", "footnote"), ("endnotes", "endnote")):
                path: NotePartName = (
                    "word/footnotes.xml" if name == "footnotes" else "word/endnotes.xml"
                )
                if path not in archive.namelist():
                    continue
                content = _read_xml(archive, path)
                if _declares_entities(content):
                    if origins is not None:
                        origins.part(path, content, None)
                    return "", hashlib.sha256(xml).hexdigest(), "unsupported"
                root = ET.fromstring(content)
                supported = origins.part(path, content, root) if origins is not None else False
                namespace, _ = _split_tag(root.tag)
                reader = _WordMl(namespace)
                for index, note in enumerate(root):
                    identity = note.get(reader.tag("id"))
                    if identity is not None:
                        text = "\n".join(reader.blocks(note))
                        notes[f"{tag}/{identity}"] = text
                        if origins is not None:
                            origins.note(
                                path,
                                index,
                                identity,
                                text,
                                supported=supported and note.tag == reader.tag(tag),
                            )
                    elif origins is not None:
                        origins.losses.append("note entry without an ID was not read")
        if _declares_entities(xml):
            return "", hashlib.sha256(xml).hexdigest(), "unsupported"
        root = ET.fromstring(xml)
        namespace, local = _split_tag(root.tag)
        state: WordBodyState = "read"
        if local not in _WORDML_ROOTS or namespace not in _WORDML_NAMESPACES:
            state = "unsupported"
        elif root.find(f"{{{namespace}}}body") is None:
            state = "missing"
        return (
            _wordml_text(root, notes=notes, blocks=blocks, projection=projection, origins=origins),
            hashlib.sha256(xml).hexdigest(),
            state,
        )


def _wordml_text(
    root: ET.Element,
    *,
    notes: dict[str, str] | None = None,
    blocks: list[WordTextBlock] | None = None,
    projection: list[str] | None = None,
    origins: _WordNoteOrigins | None = None,
) -> str:
    """The legacy body view of a supported WordprocessingML document element."""
    namespace, local = _split_tag(root.tag)
    if local not in _WORDML_ROOTS or namespace not in _WORDML_NAMESPACES:
        log.warning("not a WordprocessingML document (root %s); no text", root.tag)
        return ""
    reader = _WordMl(namespace, notes=notes, blocks=blocks, projection=projection, origins=origins)
    body = root.find(reader.tag("body"))
    if body is None:
        log.warning("Word document without a body; no text")
        return ""
    path = (
        (next(index for index, child in enumerate(root) if child is body),)
        if blocks is not None
        else None
    )
    return legacy_word_text(reader.blocks(body, path=path, project=True))


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

    def __init__(
        self,
        namespace: str,
        *,
        notes: dict[str, str] | None = None,
        blocks: list[WordTextBlock] | None = None,
        projection: list[str] | None = None,
        origins: _WordNoteOrigins | None = None,
    ) -> None:
        self._origins = origins
        self._ns = f"{{{namespace}}}"
        self._notes = notes or {}
        self._inline_notes = 0
        self._blocks = blocks
        self._projection = projection

    def tag(self, name: str) -> str:
        return f"{self._ns}{name}"

    def blocks(
        self, node: ET.Element, *, path: tuple[int, ...] | None = None, project: bool = False
    ) -> Iterator[str]:
        """Paragraphs and logical table rows in the current legacy Word view."""
        for index, child in enumerate(node):
            child_path = (*path, index) if path is not None else None
            if child.tag == self.tag("p"):
                text = self.paragraph(child, path=child_path)
                self._record("paragraph", child_path, text, project=project)
                yield text
            elif child.tag == self.tag("tbl"):
                yield from self._table(child, path=child_path, project=project)
            elif child.tag in (self.tag("sdt"), self.tag("sdtContent"), *_TRANSPARENT):
                yield from self.blocks(child, path=child_path, project=project)

    def _record(
        self,
        kind: Literal["paragraph", "table_row", "table_cell"],
        path: tuple[int, ...] | None,
        text: str,
        *,
        project: bool = False,
    ) -> None:
        if self._blocks is None or self._projection is None or path is None:
            return
        block = WordTextBlock(kind=kind, path=path, text=text)
        self._blocks.append(block)
        if project:
            self._projection.append(block.block_id)

    def _table(
        self, table: ET.Element, *, path: tuple[int, ...] | None, project: bool
    ) -> Iterator[str]:
        for row, row_path in self._direct(table, "tr", path):
            cells: list[str] = []
            for cell, cell_path in self._direct(row, "tc", row_path):
                text = " ".join(self.blocks(cell, path=cell_path))
                self._record("table_cell", cell_path, text)
                cells.append(text)
            text = "\t".join(cells)
            self._record("table_row", row_path, text, project=project)
            yield text

    def _direct(
        self, node: ET.Element, name: str, path: tuple[int, ...] | None
    ) -> Iterator[tuple[ET.Element, tuple[int, ...] | None]]:
        for index, child in enumerate(node):
            child_path = (*path, index) if path is not None else None
            if child.tag == self.tag(name):
                yield child, child_path
            elif child.tag in (self.tag("sdt"), self.tag("sdtContent"), *_TRANSPARENT):
                yield from self._direct(child, name, child_path)

    def paragraph(self, p: ET.Element, *, path: tuple[int, ...] | None = None) -> str:
        parts: list[str] = []
        self._inline(p, parts, path=path, paragraph=path)
        return "".join(parts)

    def _inline(
        self,
        node: ET.Element,
        parts: list[str],
        *,
        path: tuple[int, ...] | None = None,
        paragraph: tuple[int, ...] | None = None,
    ) -> None:
        for index, el in enumerate(node):
            child_path = (*path, index) if path is not None else None
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
                local: Literal["footnote", "endnote"] = (
                    "footnote" if el.tag == self.tag("footnoteReference") else "endnote"
                )
                identity = el.get(self.tag("id"), "")
                if self._origins is not None and child_path is not None and paragraph is not None:
                    self._origins.reference(local, identity, child_path, paragraph, self._notes)
                note = self._notes.get(f"{local}/{identity}")
                if note:
                    parts.append(f" [przypis {identity}: {note}] ")
                else:
                    parts.append(f" [nieodczytany przypis {identity}] ")
            elif el.tag in (self.tag("footnote"), self.tag("endnote")):
                # Word 2003 XML keeps the note itself at its reference, numbered by order alone.
                self._inline_notes += 1
                if self._origins is not None:
                    self._origins.losses.append("inline Word notes lack note-part origins")
                note = "\n".join(self.blocks(el))
                parts.append(f" [przypis {self._inline_notes}: {note}] ")
            else:
                self._inline(el, parts, path=child_path, paragraph=paragraph)


@dataclass
class _OdtOrigins:
    blocks: list[OdtTextBlock] = field(default_factory=list)
    legacy_blocks: list[str] = field(default_factory=list)
    ignored_nodes: list[OdtIgnoredNode] = field(default_factory=list)

    def record(
        self,
        kind: Literal["paragraph", "heading", "table_row", "table_cell"],
        path: tuple[int, ...],
        text: str,
        *,
        projected: bool,
    ) -> None:
        block = OdtTextBlock(kind=kind, path=path, text=text)
        self.blocks.append(block)
        if projected:
            self.legacy_blocks.append(block.block_id)


class OdtTextExtractor:
    """Paragraphs and headings of `content.xml`, tables as tab-separated rows."""

    def pages(self, data: bytes) -> int:
        return 0

    def select_pages(self, data: bytes, *, first: int, count: int) -> bytes:
        return data

    def extract(self, data: bytes) -> str:
        _, body, _ = self._body(data)
        return "\n".join(_odt_blocks(body)) if body is not None else ""

    def extract_odt_blocks(self, data: bytes) -> OdtTextExtraction:
        xml, body, state = self._body(data)
        origins = _OdtOrigins()
        if body is not None:
            list(_odt_blocks(body, origins=origins))
        return OdtTextExtraction(
            input_sha256=hashlib.sha256(data).hexdigest(),
            xml_sha256=hashlib.sha256(xml).hexdigest(),
            python_version=sys.version.split()[0],
            body_state=state,
            blocks=tuple(sorted(origins.blocks, key=lambda block: block.path)),
            legacy_blocks=tuple(origins.legacy_blocks),
            ignored_nodes=tuple(sorted(origins.ignored_nodes, key=lambda node: node.path)),
        )

    @staticmethod
    def _body(
        data: bytes,
    ) -> tuple[bytes, ET.Element | None, Literal["read", "missing", "unsupported"]]:
        with _physical_zip(data) as archive:
            xml = _read_xml(archive, "content.xml")
        if _declares_entities(xml):
            return xml, None, "unsupported"
        body = ET.fromstring(xml).find(f"{_ODT_OFFICE}body/{_ODT_OFFICE}text")
        if body is None:
            log.warning("OpenDocument file without a text body; no text")
            return xml, None, "missing"
        return xml, body, "read"


def _odt_blocks(
    node: ET.Element,
    *,
    path: tuple[int, ...] = (),
    origins: _OdtOrigins | None = None,
    projected: bool = True,
) -> Iterator[str]:
    for index, child in enumerate(node):
        child_path = (*path, index)
        if child.tag in (f"{_ODT_TEXT}p", f"{_ODT_TEXT}h"):
            text = _odt_paragraph(child)
            if origins is not None:
                origins.record(
                    "heading" if child.tag == f"{_ODT_TEXT}h" else "paragraph",
                    child_path,
                    text,
                    projected=projected,
                )
            yield text
        elif child.tag == f"{_ODT_TABLE}table":
            for row, row_path in _odt_rows(child, path=child_path, origins=origins):
                cells: list[str] = []
                for cell_index, cell in enumerate(row):
                    cell_path = (*row_path, cell_index)
                    if cell.tag not in _ODT_CELLS:
                        if origins is not None:
                            origins.ignored_nodes.append(
                                OdtIgnoredNode(path=cell_path, tag=cell.tag)
                            )
                        continue
                    text = " ".join(
                        _odt_blocks(cell, path=cell_path, origins=origins, projected=False)
                    )
                    if origins is not None:
                        origins.record("table_cell", cell_path, text, projected=False)
                    cells.append(text)
                text = "\t".join(cells)
                if origins is not None:
                    origins.record("table_row", row_path, text, projected=projected)
                yield text
        elif child.tag in _ODT_CONTAINERS:
            yield from _odt_blocks(child, path=child_path, origins=origins, projected=projected)
        elif origins is not None:
            origins.ignored_nodes.append(OdtIgnoredNode(path=child_path, tag=child.tag))


def _odt_rows(
    table: ET.Element, *, path: tuple[int, ...], origins: _OdtOrigins | None
) -> Iterator[tuple[ET.Element, tuple[int, ...]]]:
    """The rows of one table (not of the tables nested in its cells), through the row groups."""
    for index, child in enumerate(table):
        child_path = (*path, index)
        if child.tag == f"{_ODT_TABLE}table-row":
            yield child, child_path
        elif child.tag in _ODT_ROW_GROUPS:
            yield from _odt_rows(child, path=child_path, origins=origins)
        elif origins is not None:
            origins.ignored_nodes.append(OdtIgnoredNode(path=child_path, tag=child.tag))


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


@dataclass(frozen=True)
class _PhysicalOrigins:
    pdf_pages: list[PdfPartExtraction] | None
    word_blocks: list[WordPartExtraction] | None
    word_notes: list[WordPartNoteExtraction] | None
    odt_blocks: list[OdtPartExtraction] | None
    unlocated_text: list[UnlocatedPartExtraction] | None


class DocumentTextExtractor:
    """Route physical formats and retain the legacy package-selection facade."""

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

    def extract_parts(
        self,
        data: bytes,
        *,
        include_pdf_pages: bool = True,
        include_word_blocks: bool = True,
        include_word_notes: bool = True,
        include_odt_blocks: bool = True,
        include_unlocated_text: bool = True,
    ) -> ExtractedParts:
        parts: list[DocumentPart] = []
        rejected: list[str] = []
        origins = _PhysicalOrigins(
            pdf_pages=[] if include_pdf_pages else None,
            word_blocks=[] if include_word_blocks else None,
            word_notes=[] if include_word_notes and include_word_blocks else None,
            odt_blocks=[] if include_odt_blocks else None,
            unlocated_text=[] if include_unlocated_text else None,
        )
        digest = hashlib.sha256(data).hexdigest()
        capture = current_physical_walk_capture()
        if capture is not None:
            _physical_event(capture, partial(capture.start, data, sha256=digest))
            _physical_event(capture, lambda: capture.recipe(self._physical_recipe(origins)))
        walk_complete = False
        try:
            self._collect(data, (), digest, parts, rejected, [0, 0], origins)
            walk_complete = True
        finally:
            if capture is not None:
                _physical_event(capture, lambda: capture.rejected(rejected, complete=walk_complete))
        return ExtractedParts(
            input_sha256=digest,
            parts=tuple(parts),
            rejected=tuple(rejected),
            pdf_pages=tuple(origins.pdf_pages or ()),
            word_blocks=tuple(origins.word_blocks or ()),
            word_notes=tuple(origins.word_notes or ()),
            odt_blocks=tuple(origins.odt_blocks or ()),
            unlocated_text=tuple(origins.unlocated_text or ()),
        )

    def extract_document(self, data: bytes) -> ExtractedDocument:
        extracted = self.extract_parts(
            data,
            include_pdf_pages=False,
            include_word_blocks=False,
            include_word_notes=False,
            include_odt_blocks=False,
            include_unlocated_text=False,
        )
        return select_document_parts(extracted, max_part_chars=self._max_part_chars)

    def extract_document_with_provenance(self, data: bytes) -> DocumentExtraction:
        physical = self.extract_parts(data)
        selected = select_document_parts(physical, max_part_chars=self._max_part_chars)
        result = summarize_extraction(
            physical,
            selected,
            components=(
                extraction_component("package", self),
                extraction_component("pdf", self._pdf),
                extraction_component("word", self._docx),
                extraction_component("doc", self._doc),
                extraction_component("odt", self._odt),
            ),
            options=ExtractionOptions(
                max_member_bytes=self._max_member_bytes,
                max_part_chars=self._max_part_chars,
                archive_depth=_MAX_ARCHIVE_DEPTH,
                archive_bytes=_MAX_ARCHIVE_BYTES,
                archive_members=_MAX_ARCHIVE_MEMBERS,
                xml_bytes=_MAX_XML_BYTES,
            ),
        )
        capture = current_physical_walk_capture()
        if capture is not None:
            _physical_event(capture, lambda: capture.extraction(result))
        return DocumentExtraction(document=selected, result=result)

    def _physical_recipe(self, origins: _PhysicalOrigins) -> PhysicalMetadata:
        return _physical_recipe(
            (
                ("package", self),
                ("pdf", self._pdf),
                ("word", self._docx),
                ("doc", self._doc),
                ("odt", self._odt),
            ),
            PhysicalCaptureOptions(
                max_root_bytes=None,
                max_member_bytes=self._max_member_bytes or 32_000_000,
                max_part_chars=self._max_part_chars,
                archive_depth=_MAX_ARCHIVE_DEPTH,
                archive_bytes=_MAX_ARCHIVE_BYTES,
                archive_members=_MAX_ARCHIVE_MEMBERS,
                xml_bytes=_MAX_XML_BYTES,
                include_pdf_pages=origins.pdf_pages is not None,
                include_word_blocks=origins.word_blocks is not None,
                include_word_notes=origins.word_notes is not None,
                include_odt_blocks=origins.odt_blocks is not None,
                include_unlocated_text=origins.unlocated_text is not None,
            ),
        )

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

    def _extract(
        self, data: bytes, *, depth: int, include_pdf_pages: bool
    ) -> tuple[str, PdfTextExtraction | None]:
        head = data[:1024]
        if head.startswith(b"%PDF"):
            return self._pdf_text(data, include_pdf_pages=include_pdf_pages)
        if head.startswith(_OLE_MAGIC):
            return self._doc.extract(data), None
        if head.startswith(_ZIP_MAGIC):
            return self._zip(data, depth=depth), None
        if _is_word_xml(head):
            return self._word_xml.extract(data), None
        if b"%PDF-" in head:
            return self._pdf_text(data, include_pdf_pages=include_pdf_pages)
        log.warning("document of unknown format (starts with %r); no text", data[:8])
        return "", None

    def _pdf_text(
        self, data: bytes, *, include_pdf_pages: bool
    ) -> tuple[str, PdfTextExtraction | None]:
        if include_pdf_pages and isinstance(self._pdf, PdfPageTextExtractor):
            result = self._pdf.extract_pages(data)
            return result.legacy_text, result
        return self._pdf.extract(data), None

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
        origins: _PhysicalOrigins,
    ) -> None:
        capture = current_physical_walk_capture()
        if capture is None:
            self._collect_content(data, members, container, parts, rejected, usage, origins)
        else:
            with _physical_collect(capture, data):
                self._collect_content(data, members, container, parts, rejected, usage, origins)

    def _collect_content(
        self,
        data: bytes,
        members: tuple[str, ...],
        container: str,
        parts: list[DocumentPart],
        rejected: list[str],
        usage: list[int],
        origins: _PhysicalOrigins,
    ) -> None:
        pdf_result: PdfTextExtraction | None = None
        word_result: WordTextExtraction | None = None
        note_result: WordNoteExtraction | None = None
        odt_result: OdtTextExtraction | None = None
        if data.startswith(_ZIP_MAGIC):
            try:
                with _physical_zip(data) as archive:
                    names = set(archive.namelist())
                    if "word/document.xml" in names:
                        capture = current_physical_walk_capture()
                        if capture is not None and type(self._docx) is not DocxTextExtractor:
                            _physical_event(capture, capture.opaque_helper)
                        if origins.word_notes is not None and isinstance(
                            self._docx, WordNoteTextExtractor
                        ):
                            detailed = _physical_format(
                                partial(self._docx.extract_with_notes, data)
                            )
                            word_result, note_result = detailed.body, detailed.notes
                            text = word_result.legacy_text
                        elif origins.word_blocks is not None and isinstance(
                            self._docx, WordBlockTextExtractor
                        ):
                            word_result = _physical_format(partial(self._docx.extract_blocks, data))
                            text = word_result.legacy_text
                        else:
                            text = _physical_format(lambda: self._docx.extract(data))
                    elif "content.xml" in names and "mimetype" in names:
                        capture = current_physical_walk_capture()
                        if capture is not None and type(self._odt) is not OdtTextExtractor:
                            _physical_event(capture, capture.opaque_helper)
                        if origins.odt_blocks is not None and isinstance(
                            self._odt, OdtBlockTextExtractor
                        ):
                            odt_result = _physical_format(
                                partial(self._odt.extract_odt_blocks, data)
                            )
                            text = odt_result.legacy_text
                        else:
                            text = _physical_format(lambda: self._odt.extract(data))
                    else:
                        if len(members) >= _MAX_ARCHIVE_DEPTH:
                            _physical_depth(archive)
                            rejected.append(f"{members}: archive depth limit")
                            return
                        for info in sorted(archive.infolist(), key=lambda i: i.filename):
                            if not _is_readable(info.filename):
                                _physical_disposition(archive, info, "readability_rejected")
                                continue
                            usage[0] += info.file_size
                            usage[1] += 1
                            limit = self._max_member_bytes or 32_000_000
                            if (
                                info.file_size > limit
                                or usage[0] > _MAX_ARCHIVE_BYTES
                                or usage[1] > _MAX_ARCHIVE_MEMBERS
                            ):
                                _physical_disposition(
                                    archive, info, "unpacking_limit_source_pinned_inference"
                                )
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
                                    origins,
                                )
                            except Exception as exc:
                                rejected.append(
                                    f"{(*members, info.filename)}: {type(exc).__name__}: {exc}"
                                )
                        return
            except (zipfile.BadZipFile, ET.ParseError, ValueError) as exc:
                capture = current_physical_walk_capture()
                if capture is not None:
                    _physical_event(capture, partial(capture.format_failed, exc))
                rejected.append(f"{members}: {type(exc).__name__}")
                return
        else:
            text, pdf_result = self._extract(
                data, depth=len(members), include_pdf_pages=origins.pdf_pages is not None
            )
        pages = self.pages(data)
        losses = tuple(
            f"page {index}: no extracted text"
            for index, page in enumerate(text.split(PAGE_BREAK), 1)
            if pages and not page.strip()
        ) + tuple(re.findall(r"\[nieodczytany przypis [^\]]+\]", text))
        chunks = len(text.split(PAGE_BREAK))
        # The PDF extractor strips textless edge pages: past a whole act and its reasons, annexes.
        if pages and chunks != pages and not (chunks < pages and reads_through_justification(text)):
            losses += ("text page coverage differs from the PDF page count",)
        parts.append(
            DocumentPart(
                members=members,
                container_sha256=container,
                sha256=hashlib.sha256(data).hexdigest(),
                text=text,
                text_end=len(text),
                text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                pages=pages,
                page_start=1 if pages else None,
                page_end=pages or None,
                losses=losses if text.strip() else ("no extracted text",),
            )
        )

        capture = current_physical_walk_capture()
        if capture is not None:
            _physical_event(capture, lambda: capture.part(len(parts) - 1, parts[-1]))
        if origins.pdf_pages is not None and pdf_result is not None:
            origins.pdf_pages.append(
                PdfPartExtraction(part_index=len(parts) - 1, extraction=pdf_result)
            )
        if origins.word_blocks is not None and word_result is not None:
            origins.word_blocks.append(
                WordPartExtraction(part_index=len(parts) - 1, extraction=word_result)
            )
        if origins.word_notes is not None and note_result is not None:
            origins.word_notes.append(
                WordPartNoteExtraction(part_index=len(parts) - 1, extraction=note_result)
            )
        if origins.odt_blocks is not None and odt_result is not None:
            origins.odt_blocks.append(
                OdtPartExtraction(part_index=len(parts) - 1, extraction=odt_result)
            )
        if origins.unlocated_text is not None:
            head = data[:1024]
            route: Literal["doc", "word_xml"] | None = (
                "doc" if head.startswith(_OLE_MAGIC) else "word_xml" if _is_word_xml(head) else None
            )
            if route is not None:
                origins.unlocated_text.append(
                    UnlocatedPartExtraction(
                        part_index=len(parts) - 1,
                        extraction=UnlocatedTextExtraction(
                            route=route,
                            input_sha256=parts[-1].sha256,
                            text_sha256=parts[-1].text_sha256,
                            text_chars=len(text),
                        ),
                    )
                )


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


def _read_xml(archive: zipfile.ZipFile, name: str) -> bytes:
    request = _physical_request(archive, "xml", name)
    content = None
    error = None
    declared = None
    try:
        info = archive.getinfo(name)
        declared = info.file_size
        if isinstance(archive, _PhysicalZip):
            _physical_event(archive.capture, partial(_physical_lookup, archive, request, info))
        if declared > _MAX_XML_BYTES:
            raise ValueError("document XML size limit")
        with archive.open(name) as stream:
            content = stream.read(_MAX_XML_BYTES + 1)
        if len(content) > _MAX_XML_BYTES:
            raise ValueError("document XML size limit")
        return content
    except BaseException as exc:
        error = exc
        raise
    finally:
        _physical_returned(archive, request, content, declared, error)


def _read_member(archive: zipfile.ZipFile, info: ZipInfo, data: bytes, limit: int) -> bytes:
    request = _physical_request(archive, "member", info)
    result = None
    error = None
    try:
        result = _read_member_content(archive, info, data, limit)
        return result
    except BaseException as exc:
        error = exc
        raise
    finally:
        _physical_returned(archive, request, result, info.file_size, error)


def _read_member_content(archive: zipfile.ZipFile, info: ZipInfo, data: bytes, limit: int) -> bytes:
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


def _physical_event(capture: PhysicalWalkCapture, action: Callable[[], None]) -> None:
    try:
        action()
    except BaseException as error:
        capture.unavailable("capture_event_failed", error)


@contextmanager
def _physical_collect(capture: PhysicalWalkCapture, data: bytes) -> Iterator[None]:
    manager: AbstractContextManager[None] | None = None
    try:
        candidate = capture.collect(data, zip_input=data.startswith(_ZIP_MAGIC))
        candidate.__enter__()
        manager = candidate
    except BaseException as error:
        capture.unavailable("capture_context_entry_failed", error)
    if manager is None:
        with suspend_physical_walk_capture():
            yield
        return
    try:
        yield
    except BaseException as error:
        _physical_event(capture, partial(_physical_exit, manager, error))
        raise
    else:
        _physical_event(capture, partial(_physical_exit, manager, None))


def _physical_exit(manager: AbstractContextManager[None], error: BaseException | None) -> None:
    manager.__exit__(
        type(error) if error is not None else None,
        error,
        error.__traceback__ if error is not None else None,
    )


def _physical_format[T](action: Callable[[], T]) -> T:
    try:
        return action()
    except BaseException as error:
        capture = current_physical_walk_capture()
        if capture is not None:
            _physical_event(capture, partial(capture.format_failed, error))
        raise


def _physical_opaque(data: bytes) -> tuple[int, str | None]:
    return len(data), hashlib.sha256(data).hexdigest() if data else None


def _physical_decoded(info: ZipInfo) -> tuple[DecodedPath, ...]:
    extra = info.extra
    position = 0
    evidence: list[DecodedPath] = []
    original = info.orig_filename.encode("utf-8" if info.flag_bits & 2048 else "cp437")
    while position + 4 <= len(extra):
        tag, size = struct.unpack_from("<HH", extra, position)
        start = position + 4
        end = start + size
        if end > len(extra):
            evidence.append(("malformed_tail", position, tag, size))
            break
        if tag == 0x7075:
            body = extra[start:end]
            if len(body) < 5:
                raise ValueError(
                    "short Unicode extra requires an unavailable header evidence variant"
                )
            version = body[0]
            crc = struct.unpack_from("<I", body, 1)[0]
            try:
                text = body[5:].decode("utf-8")
                valid = True
                matches = text == info.filename
            except UnicodeDecodeError:
                valid = matches = False
            evidence.append(
                (
                    "unicode_path",
                    position,
                    version,
                    crc,
                    crc == zlib.crc32(original),
                    valid,
                    matches,
                    hashlib.sha256(body[5:]).hexdigest(),
                )
            )
        position = end
    if position < len(extra) and len(extra) - position < 4:
        evidence.append(("short_tail", position, len(extra) - position))
    return tuple(evidence)


def _physical_header_row(info: ZipInfo) -> HeaderRow:
    return (
        info.orig_filename,
        info.filename if info.filename != info.orig_filename else None,
        info.file_size,
        info.compress_size,
        info.compress_type,
        info.flag_bits,
        info.CRC,
        info.header_offset,
        info.external_attr,
        info.create_system,
        _physical_opaque(info.extra),
        _physical_opaque(info.comment),
        _physical_decoded(info),
    )


class _PhysicalZip(zipfile.ZipFile):
    def __init__(self, data: bytes, capture: PhysicalWalkCapture) -> None:
        self.capture = capture
        self.ordinals: dict[int, int] = {}
        self.open_lookup: ZipInfo | None = None
        try:
            super().__init__(io.BytesIO(data))
        except BaseException as error:
            _physical_event(capture, partial(capture.zip_failed, error))
            raise
        _physical_event(capture, lambda: self.observe_header(data))

    def observe_header(self, data: bytes) -> None:
        context = self.capture.context
        names = self.NameToInfo
        route: Literal["archive", "word", "odt"] = (
            "word"
            if "word/document.xml" in names
            else "odt"
            if "content.xml" in names and "mimetype" in names
            else "archive"
        )
        self.capture.parsed(len(self.filelist), route)
        if context is None or context.body is None:
            return
        if self.fp is None:
            self.capture.unavailable("successful_zip_parser_position_unavailable")
            return
        start, end = self.start_dir, self.fp.tell()
        if not 0 <= start <= end <= len(data):
            self.capture.unavailable("successful_zip_parser_span_unavailable")
            return
        header = PhysicalHeaders(
            body_sha=context.body.sha256,
            central_directory_sha256=hashlib.sha256(memoryview(data)[start:end]).hexdigest(),
            central_directory_span=(start, end),
            archive_comment=_physical_opaque(self.comment),
            rows=(),
        )
        count = len(self.filelist)
        minimum = PhysicalMetadata.of(
            header,
            omission=PhysicalOmission(
                reason="missing_metadata",
                full_metadata_sha256=None,
                omitted=(PhysicalOmittedCount(field="rows", count=count),),
                unknown_fields=1,
            )
            if count
            else None,
        )
        self.capture.header_prefix(minimum)
        if not self.capture.reserve((), fixed=count * 32):
            return
        builder = PhysicalHeaderBuilder(header)
        for info in self.filelist:
            builder.append(_physical_header_row(info))
        self.capture.header(builder.finish(complete=True), len(self.filelist), route)
        if self.capture.reserve((), fixed=len(self.filelist) * 48):
            self.ordinals = {id(info): index for index, info in enumerate(self.filelist)}

    def getinfo(self, name: str) -> ZipInfo:
        info = super().getinfo(name)
        self.open_lookup = info
        return info

    def open(
        self,
        name: str | ZipInfo,
        mode: Literal["r", "w"] = "r",
        pwd: bytes | None = None,
        *,
        force_zip64: bool = False,
    ) -> IO[bytes]:
        self.open_lookup = name if isinstance(name, ZipInfo) else None
        error = None
        try:
            return super().open(name, mode, pwd, force_zip64=force_zip64)
        except BaseException as exc:
            error = exc
            raise
        finally:
            info = self.open_lookup
            target = self.ordinals.get(id(name)) if isinstance(name, ZipInfo) else name
            resolved = self.ordinals.get(id(info)) if info is not None else None
            if target is not None:
                _physical_event(self.capture, lambda: self.capture.opened(target, resolved, error))
            else:
                self.capture.unavailable("opened_ordinal_unavailable")
                _physical_event(
                    self.capture,
                    partial(
                        self.capture.opened,
                        name.filename if isinstance(name, ZipInfo) else name,
                        resolved,
                        error,
                    ),
                )


def _physical_zip(data: bytes) -> zipfile.ZipFile:
    capture = current_physical_walk_capture()
    return _PhysicalZip(data, capture) if capture is not None else zipfile.ZipFile(io.BytesIO(data))


def _physical_disposition(
    archive: zipfile.ZipFile,
    info: ZipInfo,
    status: Literal["readability_rejected", "unpacking_limit_source_pinned_inference"],
) -> None:
    if isinstance(archive, _PhysicalZip):
        ordinal = archive.ordinals.get(id(info))
        if ordinal is not None:
            _physical_event(archive.capture, lambda: archive.capture.disposition(ordinal, status))


def _physical_depth(archive: zipfile.ZipFile) -> None:
    if isinstance(archive, _PhysicalZip):
        for ordinal in range(len(archive.ordinals)):
            _physical_event(
                archive.capture,
                partial(archive.capture.disposition, ordinal, "not_visited_depth_limit"),
            )


def _physical_request(
    archive: zipfile.ZipFile,
    mode: Literal["member", "xml"],
    target: str | ZipInfo,
) -> int | None:
    if not isinstance(archive, _PhysicalZip):
        return None
    try:
        ordinal = archive.ordinals.get(id(target)) if isinstance(target, ZipInfo) else None
        if isinstance(target, ZipInfo) and ordinal is None:
            archive.capture.unavailable("requested_ordinal_unavailable")
            return archive.capture.request(mode, target.filename, None)
        return archive.capture.request(
            mode,
            ordinal
            if ordinal is not None
            else target.filename
            if isinstance(target, ZipInfo)
            else target,
            ordinal,
        )
    except BaseException as error:
        archive.capture.unavailable("capture_request_failed", error)
        return None


def _physical_lookup(archive: zipfile.ZipFile, request: int | None, info: ZipInfo) -> None:
    if isinstance(archive, _PhysicalZip) and request is not None:
        context = archive.capture.context
        if context is not None:
            item = context.requests[request]
            context.requests[request] = (*item[:3], archive.ordinals.get(id(info)), *item[4:])


def _physical_returned(
    archive: zipfile.ZipFile,
    request: int | None,
    content: bytes | None,
    declared: int | None,
    error: BaseException | None,
) -> None:
    if isinstance(archive, _PhysicalZip):
        _physical_event(
            archive.capture, lambda: archive.capture.returned(request, content, declared, error)
        )


def _physical_recipe(
    components: tuple[tuple[Literal["package", "pdf", "word", "doc", "odt"], object], ...],
    options: PhysicalCaptureOptions,
) -> PhysicalMetadata:
    files: dict[str, PhysicalCodeFile] = {}
    implementations: list[PhysicalImplementation] = []
    capture = current_physical_walk_capture()
    if capture is None:
        raise ValueError("physical recipe requires its actual invocation capture")
    objects = (*components, ("capture", capture))
    for role, implementation in objects:
        cls = type(implementation)
        path = cls.__module__.replace(".", "/") + ".py"
        try:
            source_sha = component_code(inspect.getfile(cls))
        except TypeError:
            source_sha = None
        files[path] = PhysicalCodeFile(path=path, sha256=source_sha)
        implementations.append(
            PhysicalImplementation(
                role=role, qualified_name=f"{cls.__module__}.{cls.__qualname__}", code_path=path
            )
        )
    for module in (
        physical_walk_capture,
        physical_header_capture,
        physical_root_assembly,
        physical_assembly_encoding,
        physical_assembly_integrity,
        physical_assembly_inputs,
    ):
        source = inspect.getfile(module)
        path = module.__name__.replace(".", "/") + ".py"
        files[path] = PhysicalCodeFile(path=path, sha256=component_code(source))
    dependencies: list[PhysicalDependency] = []
    names: tuple[
        Literal["python", "pypdf", "olefile", "inflate64", "pydantic", "pydantic_core"], ...
    ] = ("python", "pypdf", "olefile", "inflate64", "pydantic", "pydantic_core")
    for name in names:
        try:
            version = (
                sys.version.split()[0] if name == "python" else importlib.metadata.version(name)
            )
        except importlib.metadata.PackageNotFoundError:
            version = None
        dependencies.append(PhysicalDependency(name=name, version=version))
    return PhysicalMetadata.of(
        PhysicalCaptureRecipe(
            mode="producer",
            code_files=tuple(files[key] for key in sorted(files)),
            implementations=tuple(implementations),
            dependencies=tuple(dependencies),
            options=options,
        )
    )
