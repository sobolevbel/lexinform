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
from contextlib import AbstractContextManager, contextmanager, nullcontext, suppress
from dataclasses import dataclass, field
from functools import partial
from typing import IO, Literal, Protocol
from xml.etree import ElementTree as ET
from zipfile import ZipInfo

from inflate64 import Inflater

import lexinform.physical_assembly_encoding as physical_assembly_encoding
import lexinform.physical_assembly_inputs as physical_assembly_inputs
import lexinform.physical_assembly_integrity as physical_assembly_integrity
import lexinform.physical_header_capture as physical_header_capture
import lexinform.physical_root_assembly as physical_root_assembly
import lexinform.physical_walk_capture as physical_walk_capture
from lexinform.adapters.doc_notes import DocNoteExtraction
from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.native_block_copies import (
    NativeBlockCopies,
    current_native_block_copies,
    pdf_block_copies,
)
from lexinform.adapters.odt_table_capture import capture_odt_tables
from lexinform.adapters.word_block_copies import PlainWordCopies, current_plain_word_copies
from lexinform.adapters.word_field_ranges import WordFieldExtraction, WordFieldRanges
from lexinform.adapters.word_grid_capture import capture_word_grid_merges
from lexinform.adapters.word_review_rows import bind_consultation_rows, consultation_word_rows
from lexinform.archive_policy_capture import native_archive_policy_sink
from lexinform.document_candidates import select_document_parts
from lexinform.extraction_records import (
    component_code,
    extraction_component,
    summarize_doc_note_cfspec,
    summarize_doc_note_projections,
    summarize_doc_notes,
    summarize_extraction,
    summarize_odt_tables,
    summarize_pdf_page_maps,
    summarize_word_block_copies,
    summarize_word_field_prefixes,
    summarize_word_grid_merges,
    summarize_word_observations,
)
from lexinform.models.block_input import MAX_BLOCK_INPUT_ROWS, BlockCopyWitness
from lexinform.models.doc_notes import (
    MAX_DOC_NOTE_PARTS,
    DocNoteCapture,
    DocNotedParts,
    DocPartNoteCapture,
)
from lexinform.models.documents import (
    ArchiveMemberReadFailure,
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
from lexinform.models.extractions import (
    DocumentExtraction,
    ExtractionOptions,
    root_pdf_extraction,
    root_pdf_first_page,
)
from lexinform.models.odt_notes import OdtNotedParts, OdtNoteOrigins, OdtPartNoteOrigins
from lexinform.models.odt_tables import OdtTableMerges
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
from lexinform.models.reviews import ConsultationRow
from lexinform.models.word_field_ranges import WordFieldPrefix
from lexinform.models.word_grid_merges import WordVerticalGridMerges
from lexinform.models.word_notes import (
    NotePartName,
    WordNote,
    WordNoteCall,
    WordNoteExtraction,
    WordNoteXmlPart,
)
from lexinform.models.word_structure import (
    WordPartStructureExtraction,
    WordSemanticStructureExtraction,
    WordStructuredExtraction,
    WordStructuredParts,
    WordStructureExtraction,
    WordStructureView,
)
from lexinform.models.word_view_capture import WordPartViewStatus, WordViewExtraction
from lexinform.odt_notes import OdtAppendParts, OdtNoteCollector, OdtNoteFrame, odt_note_event
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
    WordSemanticStructureTextExtractor,
    WordStructureTextExtractor,
    WordViewTextExtractor,
)
from lexinform.sections import PAGE_BREAK
from lexinform.word_projection import legacy_word_text
from lexinform.word_structure import (
    WORDML_NAMESPACES,
    WordStructureCollector,
    current_word_structure,
    suspend_word_structure,
)
from lexinform.word_table_grid import WordGridTable, word_table_grids

log = logging.getLogger(__name__)

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


class _WordNoteObserver(Protocol):
    def reference(
        self,
        kind: Literal["footnote", "endnote"],
        identity: str,
        path: tuple[int, ...],
        paragraph: tuple[int, ...],
        legacy: dict[str, str],
    ) -> None: ...

    def inline_note(self) -> None: ...


class _WordNoteOrigins:
    def __init__(self) -> None:
        self.parts: list[WordNoteXmlPart] = []
        self.notes: list[WordNote] = []
        self.calls: list[WordNoteCall] = []
        self.losses: list[str] = []
        self.targets: dict[str, int] = {}
        self.seen: set[str] = set()

    def inline_note(self) -> None:
        self.losses.append("inline Word notes lack note-part origins")

    def part(self, name: NotePartName, content: bytes, root: ET.Element | None) -> bool:
        namespace, local = _split_tag(root.tag) if root is not None else ("", "")
        supported = namespace in WORDML_NAMESPACES and local == name[5:-4]
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

    def extract_consultation_rows(
        self, data: bytes
    ) -> tuple[tuple[WordTextBlock, str | None], ...]:
        extraction = self.extract_blocks(data)
        if extraction.body_state != "read":
            return ()
        with _physical_zip(data) as archive:
            xml = _read_xml(archive, "word/document.xml")
            numbering = None
            if "word/numbering.xml" in archive.namelist():
                with suppress(ET.ParseError, ValueError):
                    definition = _read_xml(archive, "word/numbering.xml")
                    if not _declares_entities(definition):
                        numbering = ET.fromstring(definition)
        return consultation_word_rows(extraction, ET.fromstring(xml), numbering)

    def consultation_row_spans(self, data: bytes, text: str) -> tuple[ConsultationRow, ...]:
        return bind_consultation_rows(text, self.extract_consultation_rows(data))

    def consultation_archive_row_spans(
        self, data: bytes, parts: tuple[DocumentPart, ...]
    ) -> tuple[ConsultationRow, ...]:
        rows: list[ConsultationRow] = []
        offset = 0
        remaining = _MAX_ARCHIVE_BYTES
        for index, part in enumerate(parts):
            if not part.text.strip():
                continue
            if part.members and part.members[-1].casefold().endswith((".docx", ".docm")):
                with (
                    suspend_physical_walk_capture(),
                    suppress(ValueError, KeyError, RuntimeError, ET.ParseError, zipfile.BadZipFile),
                ):
                    member = data
                    for name in part.members:
                        with _physical_zip(member) as archive:
                            entries = [
                                entry for entry in archive.infolist() if entry.filename == name
                            ]
                            if len(entries) != 1:
                                raise ValueError("Ambiguous consultation archive member")
                            (entry,) = entries
                            limit = min(remaining, 32_000_000)
                            if entry.file_size > limit:
                                raise ValueError(
                                    "Consultation archive member exceeds its read budget"
                                )
                            remaining -= entry.file_size
                            member = _read_member(archive, entry, member, limit)
                    if hashlib.sha256(member).hexdigest() == part.sha256:
                        for row in self.consultation_row_spans(member, part.text):
                            locator = row.locator
                            if len(parts) > 1:
                                path = locator.removeprefix("word-xml:")
                                locator = f"word-xml:member-{index}/{path}"
                            rows.append(
                                row.model_copy(
                                    update={
                                        "locator": locator,
                                        "start": offset + row.start,
                                        "end": offset + row.end,
                                    }
                                )
                            )
            offset += len(part.text) + 2
        return tuple(rows)

    def extract_with_field_ranges(self, data: bytes) -> WordFieldExtraction:
        fields = WordFieldRanges()
        return fields.finish(self._blocks(data, fields=fields))

    def extract_with_notes(self, data: bytes) -> WordDetailedExtraction:
        notes = _WordNoteOrigins()
        body = self._blocks(data, notes=notes)
        return self._detailed(body, notes)

    def extract_with_view_status(self, data: bytes) -> WordViewExtraction:
        return self._view_status(data)

    def _view_status(
        self,
        data: bytes,
        *,
        fields: WordFieldRanges | None = None,
        capture_grid_merges: bool = False,
    ) -> WordViewExtraction:
        try:
            collector = WordStructureCollector.for_view_status()
        except Exception:
            with suspend_word_structure():
                legacy = self.extract_with_notes(data)
            return WordViewExtraction(legacy=legacy, status=None)
        notes = _WordNoteOrigins()
        copies: PlainWordCopies | None = None
        with suppress(Exception):
            copies = PlainWordCopies()
        grid_tables: list[WordGridTable] | None = [] if capture_grid_merges else None
        with collector.view_scope(), copies.scope() if copies is not None else nullcontext():
            if grid_tables is None and fields is None:
                body = self._blocks(data, notes=notes)
            else:
                body = self._blocks(data, notes=notes, fields=fields, grid_tables=grid_tables)
        legacy = self._detailed(body, notes)
        try:
            status = collector.finish_view_status(legacy)
        except Exception:
            status = None
        witness = None
        if copies is not None:
            with suppress(Exception):
                witness = copies.finish(legacy)
        grids = None
        if grid_tables:
            with suppress(Exception):
                grids = capture_word_grid_merges(grid_tables, legacy)
        return WordViewExtraction(
            legacy=legacy, status=status, block_copy_witness=witness, word_grid_merges=grids
        )

    def extract_with_structure(
        self, data: bytes, *, max_events: int = 4096, max_bytes: int = 524288
    ) -> WordStructuredExtraction:
        notes = _WordNoteOrigins()
        collector = WordStructureCollector(max_events=max_events, max_bytes=max_bytes)
        with collector.scope():
            body = self._blocks(data, notes=notes)
        legacy = self._detailed(body, notes)
        return WordStructuredExtraction(
            legacy=legacy, structure=collector.finish(legacy, notes.targets, notes.seen)
        )

    def extract_with_semantic_structure(
        self, data: bytes, *, max_events: int = 4096
    ) -> WordStructuredExtraction:
        notes = _WordNoteOrigins()
        collector = WordStructureCollector(max_events=max_events, retention="semantic-first-v2")
        with collector.scope():
            body = self._blocks(data, notes=notes)
        legacy = self._detailed(body, notes)
        return WordStructuredExtraction(
            legacy=legacy, structure=collector.finish(legacy, notes.targets, notes.seen)
        )

    @staticmethod
    def _detailed(body: WordTextExtraction, notes: _WordNoteOrigins) -> WordDetailedExtraction:
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

    def _blocks(
        self,
        data: bytes,
        *,
        notes: _WordNoteOrigins | None = None,
        fields: WordFieldRanges | None = None,
        grid_tables: list[WordGridTable] | None = None,
    ) -> WordTextExtraction:
        blocks: list[WordTextBlock] = []
        projection: list[str] = []
        if grid_tables is not None:
            text, xml_sha256, state = self._read(
                data,
                blocks=blocks,
                projection=projection,
                origins=notes,
                fields=fields,
                grid_tables=grid_tables,
            )
        elif fields is None:
            text, xml_sha256, state = self._read(
                data, blocks=blocks, projection=projection, origins=notes
            )
        else:
            text, xml_sha256, state = self._read(
                data, blocks=blocks, projection=projection, origins=notes, fields=fields
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
        fields: WordFieldRanges | None = None,
        grid_tables: list[WordGridTable] | None = None,
    ) -> tuple[str, str, WordBodyState]:
        structure = current_word_structure()
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
                if structure is not None:
                    structure.part = path
                    structure.report_support(
                        (), root.tag, len(root), "part_root", recognized=supported
                    )
                for index, note in enumerate(root):
                    identity = note.get(reader.tag("id"))
                    if structure is not None:
                        structure.report_support(
                            (index,) if structure.collects_coordinates else (),
                            note.tag,
                            len(note),
                            "note_entry" if identity is not None else "skipped_missing_id",
                            recognized=supported and note.tag == reader.tag(tag),
                        )
                    if identity is not None:
                        text = "\n".join(
                            reader.blocks(
                                note,
                                structure_path=(index,)
                                if structure is not None and structure.collects_coordinates
                                else None,
                            )
                        )
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
        if local not in _WORDML_ROOTS or namespace not in WORDML_NAMESPACES:
            state = "unsupported"
        elif root.find(f"{{{namespace}}}body") is None:
            state = "missing"
        if structure is not None:
            structure.part = "word/document.xml"
            structure.report_support(
                (), root.tag, len(root), "part_root", recognized=state != "unsupported"
            )
        text = _wordml_text(
            root,
            notes=notes,
            blocks=blocks,
            projection=projection,
            origins=origins,
            fields=fields,
        )
        if state == "read" and blocks is not None and grid_tables is not None:
            with suppress(Exception):
                grid_tables.extend(word_table_grids(root, blocks, vertical_only=True))
        return text, hashlib.sha256(xml).hexdigest(), state


def _wordml_text(
    root: ET.Element,
    *,
    notes: dict[str, str] | None = None,
    blocks: list[WordTextBlock] | None = None,
    projection: list[str] | None = None,
    origins: _WordNoteObserver | None = None,
    fields: WordFieldRanges | None = None,
) -> str:
    """The legacy body view of a supported WordprocessingML document element."""
    namespace, local = _split_tag(root.tag)
    if local not in _WORDML_ROOTS or namespace not in WORDML_NAMESPACES:
        log.warning("not a WordprocessingML document (root %s); no text", root.tag)
        return ""
    reader = _WordMl(
        namespace, notes=notes, blocks=blocks, projection=projection, origins=origins, fields=fields
    )
    body = root.find(reader.tag("body"))
    if body is None:
        log.warning("Word document without a body; no text")
        return ""
    if fields is not None:
        fields.classify(body, f"{{{namespace}}}")
    path = (
        (next(index for index, child in enumerate(root) if child is body),)
        if blocks is not None or origins is not None
        else None
    )
    return legacy_word_text(reader.blocks(body, path=path, project=True))


_PKG = "{http://schemas.microsoft.com/office/2006/xmlPackage}"
_WORD_XML_MARKERS = (b"xmlPackage", b'progid="Word.Document"', b"wordDocument")


def _is_word_xml(head: bytes) -> bool:
    return head.lstrip()[:6] == b"<?xml " and any(m in head for m in _WORD_XML_MARKERS)


_MAX_FLAT_OPC_FOOTNOTES = 64
_MAX_FLAT_OPC_NOTE_PATH = 32


@dataclass(frozen=True)
class FlatOpcFootnoteLink:
    """Zero-based XML element paths from the original package root."""

    identity: str
    call_path: tuple[int, ...]
    paragraph_path: tuple[int, ...]
    definition_path: tuple[int, ...]


@dataclass(frozen=True)
class FlatOpcNoteProof:
    """Exact footnote links in one bounded standalone Flat OPC observation."""

    input_sha256: str
    legacy_text_sha256: str
    footnote_links: tuple[FlatOpcFootnoteLink, ...]
    unproven_footnote_calls: int


class _FlatOpcNoteOrigins:
    def __init__(self, main_path: tuple[int, ...], targets: dict[str, tuple[int, ...]]) -> None:
        self.main_path = main_path
        self.targets = targets
        self.links: list[FlatOpcFootnoteLink] = []
        self.unproven = 0
        self.overflow = False

    def inline_note(self) -> None:
        pass

    def reference(
        self,
        kind: Literal["footnote", "endnote"],
        identity: str,
        path: tuple[int, ...],
        paragraph: tuple[int, ...],
        legacy: dict[str, str],
    ) -> None:
        if kind != "footnote" or self.overflow:
            return
        call_path = (*self.main_path, *path)
        if (
            len(self.links) + self.unproven == _MAX_FLAT_OPC_FOOTNOTES
            or len(call_path) > _MAX_FLAT_OPC_NOTE_PATH
        ):
            self.overflow = True
            return
        definition = self.targets.get(identity)
        if definition is None or not legacy.get(f"footnote/{identity}"):
            self.unproven += 1
            return
        self.links.append(
            FlatOpcFootnoteLink(
                identity=identity,
                call_path=call_path,
                paragraph_path=(*self.main_path, *paragraph),
                definition_path=definition,
            )
        )


def _flat_opc_note_parts(
    root: ET.Element,
) -> tuple[tuple[int, ...], tuple[int, ...], ET.Element] | None:
    if root.tag != f"{_PKG}package":
        return None
    part_tag = f"{_PKG}part"
    expected = {
        "/word/document.xml": "document",
        "/word/footnotes.xml": "footnotes",
        "/word/endnotes.xml": "endnotes",
    }
    # Legacy lookup uses iter(), so a nested matching part can change the source of a note.
    direct = {
        part: index
        for index, part in enumerate(root)
        if part.tag == part_tag and part.get(f"{_PKG}name") in expected
    }
    paths: dict[str, tuple[int, ...]] = {}
    footnotes = None
    for part in root.iter(part_tag):
        name = part.get(f"{_PKG}name", "")
        if name not in expected:
            continue
        if part not in direct or name in paths:
            return None
        payloads = part.findall(f"{_PKG}xmlData")
        if len(payloads) != 1 or len(payloads[0]) != 1:
            return None
        payload = payloads[0]
        node = payload[0]
        namespace, local = _split_tag(node.tag)
        if namespace not in WORDML_NAMESPACES or local != expected[name]:
            return None
        paths[name] = (direct[part], list(part).index(payload), 0)
        if local == "footnotes":
            footnotes = node
        if local == "endnotes" and any(child.tag != f"{{{namespace}}}endnote" for child in node):
            return None
    if footnotes is None or "/word/document.xml" not in paths:
        return None
    return paths["/word/document.xml"], paths["/word/footnotes.xml"], footnotes


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
        if local in _WORDML_ROOTS and namespace in WORDML_NAMESPACES:
            return _wordml_text(root)  # Word 2003 XML: the root is the document
        return self._flat_opc_text(root)

    def observe_flat_opc_notes(self, data: bytes) -> FlatOpcNoteProof | None:
        """Locate native footnote calls in the same standalone Flat OPC view as extract."""
        if len(data) > _MAX_XML_BYTES or b"\x00" in data or b"<!DOCTYPE" in data:
            return None
        try:
            root = ET.fromstring(data)
        except ET.ParseError, LookupError, ValueError:
            return None
        parts = _flat_opc_note_parts(root)
        if parts is None:
            return None
        main_path, footnotes_path, footnotes = parts
        if len(footnotes) > _MAX_FLAT_OPC_FOOTNOTES:
            return None
        targets: dict[str, tuple[int, ...]] = {}
        seen: set[str] = set()
        namespace, _ = _split_tag(footnotes.tag)
        for index, note in enumerate(footnotes):
            if note.tag != f"{{{namespace}}}footnote":
                return None
            identity = note.get(f"{{{namespace}}}id", "")
            if not re.fullmatch(r"-?[0-9]{1,31}", identity) or identity in seen:
                return None
            seen.add(identity)
            if note.get(f"{{{namespace}}}type", "normal") == "normal":
                targets[identity] = (*footnotes_path, index)
        observer = _FlatOpcNoteOrigins(main_path, targets)
        try:
            text = self._flat_opc_text(root, origins=observer)
        except RecursionError:
            return None
        if observer.overflow or not observer.links:
            return None
        return FlatOpcNoteProof(
            input_sha256=hashlib.sha256(data).hexdigest(),
            legacy_text_sha256=hashlib.sha256(text.encode()).hexdigest(),
            footnote_links=tuple(observer.links),
            unproven_footnote_calls=observer.unproven,
        )

    @staticmethod
    def _flat_opc_text(root: ET.Element, *, origins: _WordNoteObserver | None = None) -> str:
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
            return _wordml_text(payload[0], notes=notes, origins=origins)
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
        origins: _WordNoteObserver | None = None,
        fields: WordFieldRanges | None = None,
    ) -> None:
        self._fields = fields
        self._plain_copies = current_plain_word_copies()
        self._origins = origins
        self._ns = f"{{{namespace}}}"
        self._notes = notes or {}
        self._inline_notes = 0
        self._blocks = blocks
        self._projection = projection
        self._structure = current_word_structure()
        self._status_inline_tags = (
            frozenset(
                self.tag(name)
                for name in (
                    "t",
                    "tab",
                    "br",
                    "cr",
                    "lastRenderedPageBreak",
                    "footnoteReference",
                    "endnoteReference",
                    "footnote",
                    "endnote",
                )
            )
            if self._structure is not None
            and not self._structure.collects_coordinates
            and namespace in WORDML_NAMESPACES
            else None
        )
        wrappers = (
            frozenset((self.tag("sdt"), self.tag("sdtContent"), *_TRANSPARENT))
            if self._status_inline_tags is not None
            else frozenset()
        )
        self._removal_tags = (
            {self.tag("del"), self.tag("moveFrom")}
            if self._status_inline_tags is not None
            else frozenset()
        )
        self._status_block_tags = (
            wrappers | {self.tag("p"), self.tag("tbl")}
            if self._status_inline_tags is not None
            else None
        )
        self._status_direct_tags = (
            {name: wrappers | {self.tag(name)} for name in ("tr", "tc")}
            if self._status_inline_tags is not None
            else {}
        )

    def tag(self, name: str) -> str:
        return f"{self._ns}{name}"

    def blocks(
        self,
        node: ET.Element,
        *,
        path: tuple[int, ...] | None = None,
        project: bool = False,
        structure_path: tuple[int, ...] | None = None,
    ) -> Iterator[str]:
        """Paragraphs and logical table rows in the current legacy Word view."""
        observed = (
            (path if path is not None else structure_path)
            if self._structure is None or self._structure.collects_coordinates
            else None
        )
        if (
            self._structure is not None
            and node.tag == self.tag("body")
            and (observed is not None or not self._structure.collects_coordinates)
        ):
            self._structure.report_support(observed or (), node.tag, len(node), "structural")
        for index, child in enumerate(node):
            child_path = (*path, index) if path is not None else None
            located = (*observed, index) if observed is not None else None
            if self._structure is not None and (
                located is not None or not self._structure.collects_coordinates
            ):
                recognized = (
                    child.tag in self._status_block_tags
                    if self._status_block_tags is not None
                    else child.tag
                    in (
                        self.tag("p"),
                        self.tag("tbl"),
                        self.tag("sdt"),
                        self.tag("sdtContent"),
                        *_TRANSPARENT,
                    )
                )
                self._structure.report_support(
                    located or (),
                    child.tag,
                    len(child),
                    "structural" if recognized else "skipped_block",
                    recognized=recognized,
                )
            if child.tag == self.tag("p"):
                text = self.paragraph(child, path=child_path, structure_path=located)
                self._record("paragraph", child_path, text, project=project, structure_path=located)
                yield text
            elif child.tag == self.tag("tbl"):
                yield from self._table(
                    child, path=child_path, project=project, structure_path=located
                )
            elif child.tag in (self.tag("sdt"), self.tag("sdtContent"), *_TRANSPARENT):
                yield from self.blocks(
                    child, path=child_path, project=project, structure_path=located
                )

    def _record(
        self,
        kind: Literal["paragraph", "table_row", "table_cell"],
        path: tuple[int, ...] | None,
        text: str,
        *,
        project: bool = False,
        structure_path: tuple[int, ...] | None = None,
    ) -> None:
        if (
            self._structure is not None
            and self._structure.collects_coordinates
            and structure_path is not None
        ):
            self._structure.block(kind, structure_path, text)
        if self._blocks is None or self._projection is None or path is None:
            return
        block = WordTextBlock(kind=kind, path=path, text=text)
        self._blocks.append(block)
        if project:
            self._projection.append(block.block_id)

    def _table(
        self,
        table: ET.Element,
        *,
        path: tuple[int, ...] | None,
        project: bool,
        structure_path: tuple[int, ...] | None = None,
    ) -> Iterator[str]:
        for row, row_path, row_location in self._direct(table, "tr", path, structure_path):
            cells: list[str] = []
            for cell, cell_path, cell_location in self._direct(row, "tc", row_path, row_location):
                text = " ".join(self.blocks(cell, path=cell_path, structure_path=cell_location))
                self._record("table_cell", cell_path, text, structure_path=cell_location)
                cells.append(text)
            text = "\t".join(cells)
            self._record("table_row", row_path, text, project=project, structure_path=row_location)
            yield text

    def _direct(
        self,
        node: ET.Element,
        name: str,
        path: tuple[int, ...] | None,
        structure_path: tuple[int, ...] | None = None,
    ) -> Iterator[tuple[ET.Element, tuple[int, ...] | None, tuple[int, ...] | None]]:
        observed = (
            (path if path is not None else structure_path)
            if self._structure is None or self._structure.collects_coordinates
            else None
        )
        for index, child in enumerate(node):
            child_path = (*path, index) if path is not None else None
            located = (*observed, index) if observed is not None else None
            if self._structure is not None and (
                located is not None or not self._structure.collects_coordinates
            ):
                tags = self._status_direct_tags.get(name)
                recognized = (
                    child.tag in tags
                    if tags is not None
                    else child.tag
                    in (
                        self.tag(name),
                        self.tag("sdt"),
                        self.tag("sdtContent"),
                        *_TRANSPARENT,
                    )
                )
                self._structure.report_support(
                    located or (),
                    child.tag,
                    len(child),
                    "structural" if recognized else "skipped_block",
                    recognized=recognized,
                )
            if child.tag == self.tag(name):
                yield child, child_path, located
            elif child.tag in (self.tag("sdt"), self.tag("sdtContent"), *_TRANSPARENT):
                yield from self._direct(child, name, child_path, located)

    def paragraph(
        self,
        p: ET.Element,
        *,
        path: tuple[int, ...] | None = None,
        structure_path: tuple[int, ...] | None = None,
    ) -> str:
        parts: list[str] = []
        self._inline(
            p,
            parts,
            path=path,
            paragraph=path,
            structure_path=structure_path,
            structure_paragraph=structure_path,
        )
        text = "".join(parts)
        if self._fields is not None and path is not None:
            self._fields.paragraph(path, parts, text)
        return text

    def _inline(
        self,
        node: ET.Element,
        parts: list[str],
        *,
        path: tuple[int, ...] | None = None,
        paragraph: tuple[int, ...] | None = None,
        structure_path: tuple[int, ...] | None = None,
        structure_paragraph: tuple[int, ...] | None = None,
        in_removal: bool = False,
    ) -> None:
        observed = (
            (path if path is not None else structure_path)
            if self._structure is None or self._structure.collects_coordinates
            else None
        )
        for index, el in enumerate(node):
            child_path = (*path, index) if path is not None else None
            located = (*observed, index) if observed is not None else None
            if self._structure is not None and (
                located is not None or not self._structure.collects_coordinates
            ):
                self._support_inline(el, located or ())
            if el.tag == _MC_FALLBACK:
                continue
            if self._fields is not None and child_path is not None:
                self._fields.visit_marker(el)
            if self._plain_copies is not None and paragraph is not None:
                plain = (
                    el.tag == self.tag("t")
                    and node.tag == self.tag("r")
                    and child_path is not None
                    and len(child_path) == len(paragraph) + 2
                    and not in_removal
                )
                if el.tag in (
                    self.tag("t"),
                    self.tag("tab"),
                    self.tag("br"),
                    self.tag("cr"),
                    self.tag("lastRenderedPageBreak"),
                    self.tag("footnoteReference"),
                    self.tag("endnoteReference"),
                    self.tag("footnote"),
                    self.tag("endnote"),
                    self.tag("fldChar"),
                    self.tag("instrText"),
                ):
                    try:
                        self._plain_copies.fragment(paragraph, el.text or "", plain=plain)
                    except Exception:
                        with suppress(Exception):
                            self._plain_copies.fail()
            if el.tag == self.tag("t"):
                if self._fields is not None and child_path is not None and paragraph is not None:
                    self._fields.fragment(el, child_path, paragraph, len(parts))
                parts.append(el.text or "")
                if in_removal and el.text and self._structure is not None:
                    self._structure.report_removal_text()
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
                if (
                    self._structure is not None
                    and located is not None
                    and structure_paragraph is not None
                ):
                    self._structure.call(
                        local, identity, located, structure_paragraph, expanded=bool(note)
                    )
                if note:
                    parts.append(f" [przypis {identity}: {note}] ")
                else:
                    parts.append(f" [nieodczytany przypis {identity}] ")
            elif el.tag in (self.tag("footnote"), self.tag("endnote")):
                self._inline_notes += 1
                if self._origins is not None:
                    self._origins.inline_note()
                note = "\n".join(self.blocks(el, structure_path=located))
                parts.append(f" [przypis {self._inline_notes}: {note}] ")
            else:
                self._inline(
                    el,
                    parts,
                    path=child_path,
                    paragraph=paragraph,
                    structure_path=located,
                    structure_paragraph=structure_paragraph,
                    in_removal=in_removal or el.tag in self._removal_tags,
                )

    def _support_inline(self, node: ET.Element, path: tuple[int, ...]) -> None:
        structure = self._structure
        if structure is None:
            return
        if self._status_inline_tags is not None:
            if node.tag in self._status_inline_tags:
                return
            structure.report_support(
                path,
                node.tag,
                len(node),
                "ignored_fallback"
                if node.tag == _MC_FALLBACK
                else "recursed"
                if len(node)
                else "ignored_leaf",
                recognized=node.tag == _MC_FALLBACK,
            )
            return
        view: WordStructureView
        if node.tag == _MC_FALLBACK:
            view = "ignored_fallback"
        elif node.tag == self.tag("t"):
            view = "text"
        elif node.tag == self.tag("tab"):
            view = "tab"
        elif node.tag in (self.tag("br"), self.tag("cr"), self.tag("lastRenderedPageBreak")):
            view = (
                "page_break"
                if node.tag == self.tag("lastRenderedPageBreak")
                or (node.tag == self.tag("br") and node.get(self.tag("type")) == "page")
                else "line_break"
            )
        elif node.tag in (self.tag("footnoteReference"), self.tag("endnoteReference")):
            view = "note_reference"
        elif node.tag in (self.tag("footnote"), self.tag("endnote")):
            view = "inline_note"
        else:
            structure.report_support(
                path,
                node.tag,
                len(node),
                "recursed" if len(node) else "ignored_leaf",
                recognized=False,
            )
            return
        structure.report_support(path, node.tag, len(node), view)


_ODT_COPYABLE_INLINE = frozenset(
    _ODT_TEXT + name for name in ("p", "h", "span", "a", "s", "tab", "line-break")
)


def _odt_copyable_row(row: ET.Element) -> bool:
    return all(
        cell.tag in _ODT_CELLS
        and (
            not len(cell)
            or (
                len(cell) == 1
                and cell[0].tag == _ODT_TEXT + "p"
                and all(node.tag in _ODT_COPYABLE_INLINE for node in cell[0].iter())
            )
        )
        for cell in row
    )


@dataclass
class _OdtOrigins:
    blocks: list[OdtTextBlock] = field(default_factory=list)
    legacy_blocks: list[str] = field(default_factory=list)
    ignored_nodes: list[OdtIgnoredNode] = field(default_factory=list)
    notes: OdtNoteCollector | None = None
    copyable_paths: set[tuple[int, ...]] = field(default_factory=set)
    copyable_rows: set[tuple[int, ...]] = field(default_factory=set)

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
        return self._read(data, None)[0]

    def extract_with_note_origins(
        self, data: bytes, *, tables: dict[str, OdtTableMerges] | None = None
    ) -> tuple[OdtTextExtraction, OdtNoteOrigins | None]:
        if not _native_odt_notes(self):
            return self.extract_odt_blocks(data), None
        try:
            notes = OdtNoteCollector()
        except Exception:
            notes = None
        return self._read(data, notes, tables=tables)

    def _read(
        self,
        data: bytes,
        notes: OdtNoteCollector | None,
        *,
        tables: dict[str, OdtTableMerges] | None = None,
    ) -> tuple[OdtTextExtraction, OdtNoteOrigins | None]:
        xml, body, state = self._body(data)
        origins = _OdtOrigins(notes=notes)
        if body is not None:
            list(_odt_blocks(body, origins=origins))
        extracted = OdtTextExtraction(
            input_sha256=hashlib.sha256(data).hexdigest(),
            xml_sha256=hashlib.sha256(xml).hexdigest(),
            python_version=sys.version.split()[0],
            body_state=state,
            blocks=tuple(sorted(origins.blocks, key=lambda block: block.path)),
            legacy_blocks=tuple(origins.legacy_blocks),
            ignored_nodes=tuple(sorted(origins.ignored_nodes, key=lambda node: node.path)),
        )
        copies = current_native_block_copies()
        if copies is not None:
            with suppress(Exception):
                copies.finish_odt(extracted, origins.copyable_paths, origins.copyable_rows)
        if body is not None and tables is not None and extracted.input_sha256 not in tables:
            with suppress(Exception):
                captured = capture_odt_tables(body, extracted)
                if captured is not None:
                    tables[extracted.input_sha256] = captured
        observed = (
            odt_note_event(notes, partial(_finish_odt_notes, notes, extracted))
            if notes is not None
            else None
        )
        return extracted, observed

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


def _finish_odt_notes(notes: OdtNoteCollector, body: OdtTextExtraction) -> OdtNoteOrigins | None:
    value = notes.finish(body)
    return OdtNoteOrigins.model_validate(value) if value is not None else None


_NATIVE_ODT_EXTRACT = OdtTextExtractor.extract
_NATIVE_ODT_BLOCKS = OdtTextExtractor.extract_odt_blocks
_NATIVE_ODT_NOTES = OdtTextExtractor.extract_with_note_origins
_NATIVE_ODT_READ = OdtTextExtractor._read
_NATIVE_ODT_BODY = OdtTextExtractor._body


def _native_odt_notes(value: object) -> bool:
    return (
        type(value) is OdtTextExtractor
        and getattr(value, "_body", None) is _NATIVE_ODT_BODY
        and all(
            getattr(getattr(value, name, None), "__func__", None) is method
            for name, method in (
                ("extract", _NATIVE_ODT_EXTRACT),
                ("_read", _NATIVE_ODT_READ),
                ("extract_odt_blocks", _NATIVE_ODT_BLOCKS),
                ("extract_with_note_origins", _NATIVE_ODT_NOTES),
            )
        )
    )


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
            text = _odt_paragraph(child, path=child_path, notes=origins.notes if origins else None)
            if origins is not None:
                if (
                    current_native_block_copies() is not None
                    and len(origins.copyable_paths) < MAX_BLOCK_INPUT_ROWS
                    and all(element.tag in _ODT_COPYABLE_INLINE for element in child.iter())
                ):
                    origins.copyable_paths.add(child_path)
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
                    if (
                        projected
                        and text.strip()
                        and current_native_block_copies() is not None
                        and len(origins.copyable_rows) < MAX_BLOCK_INPUT_ROWS
                        and _odt_copyable_row(row)
                    ):
                        origins.copyable_rows.add(row_path)
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


def _odt_paragraph(
    p: ET.Element, *, path: tuple[int, ...] = (), notes: OdtNoteCollector | None = None
) -> str:
    parts: list[str] = [p.text or ""]
    if notes is not None:
        tracked = odt_note_event(notes, lambda: OdtAppendParts(p.text or "", notes))
        if tracked is not None:
            parts = tracked
    _odt_inline(p, parts, notes=notes, path=path, paragraph=path, block=path)
    return "".join(parts)


def _odt_inline(
    node: ET.Element,
    parts: list[str],
    *,
    notes: OdtNoteCollector | None = None,
    path: tuple[int, ...] | None = None,
    paragraph: tuple[int, ...] | None = None,
    block: tuple[int, ...] = (),
    parent: OdtNoteFrame | None = None,
) -> None:
    for index, el in enumerate(node):
        located = (
            odt_note_event(notes, partial(notes.child, path, index)) if notes is not None else None
        )
        frame = (
            odt_note_event(
                notes, partial(_odt_note_enter, notes, el, located, paragraph, block, parent, parts)
            )
            if notes is not None
            else None
        )
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
            _odt_inline(
                el,
                parts,
                notes=notes,
                path=located,
                paragraph=located if el.tag in (_ODT_TEXT + "p", _ODT_TEXT + "h") else paragraph,
                block=block,
                parent=frame if frame is not None and frame.entry is not None else parent,
            )
        if notes is not None and isinstance(parts, OdtAppendParts):
            odt_note_event(notes, partial(_odt_note_leave, notes, frame, parts))
        if el.tail:
            parts.append(el.tail)


def _odt_note_enter(
    notes: OdtNoteCollector,
    node: ET.Element,
    path: tuple[int, ...] | None,
    paragraph: tuple[int, ...] | None,
    block: tuple[int, ...],
    parent: OdtNoteFrame | None,
    parts: list[str],
) -> OdtNoteFrame | None:
    return notes.enter(
        node,
        path,
        paragraph,
        block,
        parent,
        parts.position if isinstance(parts, OdtAppendParts) else 0,
    )


def _odt_note_leave(
    notes: OdtNoteCollector, frame: OdtNoteFrame | None, parts: OdtAppendParts
) -> None:
    notes.leave(frame, parts.position)


_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # OLE2 container: legacy .doc (also .xls, .ppt)
_ZIP_MAGIC = b"PK\x03\x04"  # .docx, .docm, .odt and plain archives all start like this


@dataclass(frozen=True)
class _PhysicalOrigins:
    pdf_pages: list[PdfPartExtraction] | None
    word_blocks: list[WordPartExtraction] | None
    word_notes: list[WordPartNoteExtraction] | None
    odt_blocks: list[OdtPartExtraction] | None
    unlocated_text: list[UnlocatedPartExtraction] | None
    word_structure: list[WordPartStructureExtraction] | None = None
    semantic_word_structure: bool = False
    word_view_status: list[WordPartViewStatus] | None = None
    word_field_prefixes: dict[int, WordFieldPrefix] | None = None
    word_block_copies: dict[int, BlockCopyWitness] | None = None
    word_grid_merges: dict[int, WordVerticalGridMerges] | None = None
    odt_notes: list[OdtPartNoteOrigins] | None = None
    odt_tables: dict[str, OdtTableMerges] | None = None
    doc_notes: list[DocPartNoteCapture] | None = None
    capture_doc_note_projection: bool = False
    capture_doc_note_cfspec: bool = False
    unreadable_members: list[ArchiveMemberReadFailure] = field(default_factory=list)
    part_archive_ordinals: list[tuple[int, ...]] = field(default_factory=list)


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
        capture_word_grid_merges: bool = False,
    ) -> None:
        self._pdf = pdf
        self._docx = docx
        self._doc = doc
        self._odt = odt or OdtTextExtractor()
        self._word_xml = WordXmlTextExtractor()
        self._max_member_bytes = max_member_bytes
        self._max_part_chars = max_part_chars
        self._capture_word_grid_merges = capture_word_grid_merges

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
        origins = _PhysicalOrigins(
            pdf_pages=[] if include_pdf_pages else None,
            word_blocks=[] if include_word_blocks else None,
            word_notes=[] if include_word_notes and include_word_blocks else None,
            odt_blocks=[] if include_odt_blocks else None,
            unlocated_text=[] if include_unlocated_text else None,
        )
        return self._extract_parts(data, origins)

    def extract_parts_with_odt_notes(
        self, data: bytes, *, include_pdf_pages: bool = True
    ) -> OdtNotedParts:
        if (
            type(self) is not DocumentTextExtractor
            or getattr(self.extract_parts, "__func__", None) is not _NATIVE_PHYSICAL_PARTS
        ):
            legacy = self.extract_parts(data, include_pdf_pages=include_pdf_pages)
            return OdtNotedParts(legacy=legacy, odt_notes=())
        origins = _PhysicalOrigins(
            pdf_pages=[] if include_pdf_pages else None,
            word_blocks=[],
            word_notes=[],
            odt_blocks=[],
            unlocated_text=[],
            odt_notes=[],
        )
        legacy = self._extract_parts(data, origins)
        empty = OdtNotedParts(legacy=legacy, odt_notes=())
        try:
            return OdtNotedParts(legacy=legacy, odt_notes=tuple(origins.odt_notes or ()))
        except Exception:
            return empty

    def extract_parts_with_doc_notes(
        self,
        data: bytes,
        *,
        include_pdf_pages: bool = True,
        capture_projection: bool = False,
        capture_cfspec: bool = False,
    ) -> DocNotedParts:
        if (
            type(self) is not DocumentTextExtractor
            or getattr(self.extract_parts, "__func__", None) is not _NATIVE_PHYSICAL_PARTS
        ):
            return DocNotedParts(
                legacy=self.extract_parts(data, include_pdf_pages=include_pdf_pages), doc_notes=()
            )
        origins = _PhysicalOrigins(
            pdf_pages=[] if include_pdf_pages else None,
            word_blocks=[],
            word_notes=[],
            odt_blocks=[],
            unlocated_text=[],
            doc_notes=[],
            capture_doc_note_projection=capture_projection,
            capture_doc_note_cfspec=capture_cfspec,
        )
        legacy = self._extract_parts(data, origins)
        return DocNotedParts(legacy=legacy, doc_notes=tuple(origins.doc_notes or ()))

    def extract_parts_with_word_structure(
        self, data: bytes, *, include_pdf_pages: bool = True
    ) -> WordStructuredParts:
        origins = _PhysicalOrigins(
            pdf_pages=[] if include_pdf_pages else None,
            word_blocks=[],
            word_notes=[],
            odt_blocks=[],
            unlocated_text=[],
            word_structure=[],
        )
        legacy = self._extract_parts(data, origins)
        return WordStructuredParts(
            legacy=legacy, word_structure=tuple(origins.word_structure or ())
        )

    def extract_parts_with_semantic_word_structure(
        self, data: bytes, *, include_pdf_pages: bool = True
    ) -> WordStructuredParts:
        origins = _PhysicalOrigins(
            pdf_pages=[] if include_pdf_pages else None,
            word_blocks=[],
            word_notes=[],
            odt_blocks=[],
            unlocated_text=[],
            word_structure=[],
            semantic_word_structure=True,
        )
        legacy = self._extract_parts(data, origins)
        return WordStructuredParts(
            legacy=legacy, word_structure=tuple(origins.word_structure or ())
        )

    def _extract_parts(self, data: bytes, origins: _PhysicalOrigins) -> ExtractedParts:
        parts: list[DocumentPart] = []
        rejected: list[str] = []
        digest = hashlib.sha256(data).hexdigest()
        capture = current_physical_walk_capture()
        if capture is not None:
            _physical_event(capture, partial(capture.start, data, sha256=digest))
            _physical_event(capture, lambda: capture.recipe(self._physical_recipe(origins)))
        walk_complete = False
        try:
            self._collect(data, (), digest, parts, rejected, [0, 0], origins, ordinals=())
            walk_complete = True
        finally:
            if capture is not None:
                _physical_event(capture, lambda: capture.rejected(rejected, complete=walk_complete))
        return ExtractedParts(
            input_sha256=digest,
            parts=tuple(parts),
            rejected=tuple(rejected),
            unreadable_members=tuple(origins.unreadable_members),
            part_archive_ordinals=tuple(origins.part_archive_ordinals),
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
        views: list[WordPartViewStatus] = []
        word_fields: dict[int, WordFieldPrefix] = {}
        word_copies: dict[int, BlockCopyWitness] = {}
        word_grids: dict[int, WordVerticalGridMerges] | None = (
            {} if self._capture_word_grid_merges else None
        )
        native_copies = NativeBlockCopies()
        odt_notes: list[OdtPartNoteOrigins] = []
        odt_tables: dict[str, OdtTableMerges] = {}
        doc_notes: list[DocPartNoteCapture] = []
        if (
            type(self) is DocumentTextExtractor
            and getattr(self.extract_parts, "__func__", None) is _NATIVE_PHYSICAL_PARTS
        ):
            origins = _PhysicalOrigins(
                pdf_pages=[],
                word_blocks=[],
                word_notes=[],
                odt_blocks=[],
                unlocated_text=[],
                word_view_status=views,
                word_field_prefixes=word_fields,
                word_block_copies=word_copies,
                word_grid_merges=word_grids,
                odt_notes=odt_notes,
                odt_tables=odt_tables,
                doc_notes=doc_notes,
                capture_doc_note_projection=True,
                capture_doc_note_cfspec=True,
            )
            with native_copies.scope():
                physical = self._extract_parts(data, origins)
        else:
            physical = self.extract_parts(data)
        native_policy = (
            type(self) is DocumentTextExtractor
            and getattr(self.extract_parts, "__func__", None) is _NATIVE_PHYSICAL_PARTS
        )
        selected = select_document_parts(
            physical,
            max_part_chars=self._max_part_chars,
            decision_sink=native_archive_policy_sink(len(physical.parts))
            if native_policy
            else None,
        )
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
            word_views=tuple(views),
            odt_origins=tuple(odt_notes),
            options=ExtractionOptions(
                max_member_bytes=self._max_member_bytes,
                max_part_chars=self._max_part_chars,
                archive_depth=_MAX_ARCHIVE_DEPTH,
                archive_bytes=_MAX_ARCHIVE_BYTES,
                archive_members=_MAX_ARCHIVE_MEMBERS,
                xml_bytes=_MAX_XML_BYTES,
            ),
        )
        linked = DocNotedParts(legacy=physical, doc_notes=tuple(doc_notes)) if doc_notes else None
        if linked is not None:
            result = summarize_doc_notes(result, linked, include_projections=False)
        result = summarize_word_observations(result, tuple(views))
        if linked is not None:
            result = summarize_doc_note_projections(result, linked)
            result = summarize_doc_note_cfspec(result, linked)
        result = summarize_word_field_prefixes(result, word_fields)
        result = summarize_word_block_copies(result, word_copies)
        native_witnesses: dict[int, BlockCopyWitness] = {}
        for page in physical.pdf_pages:
            with suppress(Exception):
                witness = pdf_block_copies(page.extraction)
                if witness is not None:
                    native_witnesses[page.part_index] = witness
        for body in physical.odt_blocks:
            witness = native_copies.odt.get(body.extraction.input_sha256)
            if witness is not None:
                native_witnesses[body.part_index] = witness
        result = summarize_word_block_copies(result, native_witnesses)
        result = summarize_pdf_page_maps(result, physical)
        result = summarize_odt_tables(result, odt_tables)
        result = summarize_word_block_copies(
            result,
            {
                body.part_index: native_copies.odt_rows[body.extraction.input_sha256]
                for body in physical.odt_blocks
                if body.extraction.input_sha256 in native_copies.odt_rows
            },
        )
        if word_grids:
            result = summarize_word_grid_merges(result, word_grids)
        capture = current_physical_walk_capture()
        if capture is not None:
            _physical_event(capture, lambda: capture.extraction(result))
        pdf = root_pdf_extraction(physical)
        complete_pages = (
            len(pdf.pages)
            if pdf is not None
            and 1 <= len(pdf.pages) <= 600
            and all(
                page.error_type is None and page.text.strip() and PAGE_BREAK not in page.text
                for page in pdf.pages
            )
            else None
        )
        return DocumentExtraction(
            document=selected,
            result=result,
            first_pdf_page=root_pdf_first_page(pdf),
            complete_pdf_pages=complete_pages,
        )

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
        *,
        ordinals: tuple[int, ...],
    ) -> None:
        capture = current_physical_walk_capture()
        if capture is None:
            self._collect_content(
                data, members, container, parts, rejected, usage, origins, ordinals
            )
        else:
            with _physical_collect(capture, data):
                self._collect_content(
                    data, members, container, parts, rejected, usage, origins, ordinals
                )

    def _collect_content(
        self,
        data: bytes,
        members: tuple[str, ...],
        container: str,
        parts: list[DocumentPart],
        rejected: list[str],
        usage: list[int],
        origins: _PhysicalOrigins,
        ordinals: tuple[int, ...],
    ) -> None:
        pdf_result: PdfTextExtraction | None = None
        word_result: WordTextExtraction | None = None
        note_result: WordNoteExtraction | None = None
        structure_result: WordStructureExtraction | WordSemanticStructureExtraction | None = None
        view_result: WordViewExtraction | None = None
        word_fields: WordFieldRanges | None = None
        odt_result: OdtTextExtraction | None = None
        odt_notes: OdtNoteOrigins | None = None
        doc_notes: DocNoteExtraction | None = None
        if data.startswith(_ZIP_MAGIC):
            try:
                with _physical_zip(data) as archive:
                    names = set(archive.namelist())
                    if "word/document.xml" in names:
                        capture = current_physical_walk_capture()
                        if capture is not None and type(self._docx) is not DocxTextExtractor:
                            _physical_event(capture, capture.opaque_helper)
                        if (
                            origins.word_view_status is not None
                            and (
                                type(self._docx) is DocxTextExtractor
                                and getattr(
                                    getattr(self._docx, "extract_with_notes", None),
                                    "__func__",
                                    None,
                                )
                                is DocxTextExtractor.extract_with_notes
                                or type(self._docx) is not DocxTextExtractor
                                and "extract_with_view_status" in type(self._docx).__dict__
                            )
                            and isinstance(self._docx, WordViewTextExtractor)
                        ):
                            if (
                                origins.word_field_prefixes is not None
                                and isinstance(self._docx, DocxTextExtractor)
                                and _native_word_fields(self._docx)
                            ):
                                word_fields = WordFieldRanges()
                                view_result = _physical_format(
                                    partial(
                                        self._docx._view_status,
                                        data,
                                        fields=word_fields,
                                        capture_grid_merges=origins.word_grid_merges is not None,
                                    )
                                )
                            else:
                                view_result = _physical_format(
                                    partial(self._docx.extract_with_view_status, data)
                                )
                            word_result, note_result = (
                                view_result.legacy.body,
                                view_result.legacy.notes,
                            )
                            text = word_result.legacy_text
                        elif origins.semantic_word_structure and isinstance(
                            self._docx, WordSemanticStructureTextExtractor
                        ):
                            structured = _physical_format(
                                partial(self._docx.extract_with_semantic_structure, data)
                            )
                            word_result, note_result = (
                                structured.legacy.body,
                                structured.legacy.notes,
                            )
                            structure_result = structured.structure
                            text = word_result.legacy_text
                        elif (
                            not origins.semantic_word_structure
                            and origins.word_structure is not None
                            and isinstance(self._docx, WordStructureTextExtractor)
                        ):
                            structured = _physical_format(
                                partial(self._docx.extract_with_structure, data)
                            )
                            word_result, note_result = (
                                structured.legacy.body,
                                structured.legacy.notes,
                            )
                            structure_result = structured.structure
                            text = word_result.legacy_text
                        elif origins.word_notes is not None and isinstance(
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
                        if (
                            origins.odt_notes is not None
                            and len(origins.odt_notes) < 64
                            and _native_odt_notes(self._odt)
                            and isinstance(self._odt, OdtTextExtractor)
                        ):
                            odt_result, odt_notes = _physical_format(
                                partial(
                                    self._odt.extract_with_note_origins,
                                    data,
                                    tables=origins.odt_tables,
                                )
                            )
                            text = odt_result.legacy_text
                        elif origins.odt_blocks is not None and isinstance(
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
                        for ordinal, info in sorted(
                            enumerate(archive.infolist()), key=lambda entry: entry[1].filename
                        ):
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
                            read_completed = False
                            try:
                                payload = _read_member(archive, info, data, limit)
                                read_completed = True
                                self._collect(
                                    payload,
                                    (*members, info.filename),
                                    container,
                                    parts,
                                    rejected,
                                    usage,
                                    origins,
                                    ordinals=(*ordinals, ordinal),
                                )
                            except Exception as exc:
                                if not read_completed:
                                    origins.unreadable_members.append(
                                        ArchiveMemberReadFailure(
                                            members=(*members, info.filename),
                                            ordinals=(*ordinals, ordinal),
                                            error_type=type(exc).__name__,
                                        )
                                    )
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
        elif (
            data.startswith(_OLE_MAGIC)
            and origins.doc_notes is not None
            and len(origins.doc_notes) < MAX_DOC_NOTE_PARTS
            and _native_doc_notes(self._doc)
            and isinstance(self._doc, DocTextExtractor)
        ):
            doc_notes = _physical_format(
                partial(
                    self._doc.extract_with_notes,
                    data,
                    capture_projection=origins.capture_doc_note_projection,
                    capture_cfspec=origins.capture_doc_note_cfspec,
                )
            )
            text = doc_notes.result.legacy_text
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
        losses = losses if text.strip() else ("no extracted text",)
        if pages and chunks != pages:
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
                losses=losses,
            )
        )
        if ordinals:
            origins.part_archive_ordinals.append(ordinals)

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
        if (
            origins.word_view_status is not None
            and view_result is not None
            and view_result.status is not None
        ):
            origins.word_view_status.append(
                WordPartViewStatus(
                    part_index=len(parts) - 1,
                    input_sha256=view_result.legacy.body.input_sha256,
                    body_manifest_sha256=view_result.legacy.body.manifest_sha256,
                    notes_manifest_sha256=view_result.legacy.notes.manifest_sha256,
                    status=view_result.status,
                )
            )
        if (
            word_fields is not None
            and view_result is not None
            and origins.word_field_prefixes is not None
        ):
            with suppress(ValueError):
                prefix = word_fields.prefix(view_result.legacy)
                if prefix is not None:
                    origins.word_field_prefixes[len(parts) - 1] = prefix
        if (
            origins.word_block_copies is not None
            and view_result is not None
            and view_result.block_copy_witness is not None
        ):
            origins.word_block_copies[len(parts) - 1] = view_result.block_copy_witness
        if (
            origins.word_grid_merges is not None
            and view_result is not None
            and view_result.word_grid_merges is not None
        ):
            origins.word_grid_merges[len(parts) - 1] = view_result.word_grid_merges
        if origins.word_structure is not None and structure_result is not None:
            origins.word_structure.append(
                WordPartStructureExtraction(part_index=len(parts) - 1, extraction=structure_result)
            )
        if origins.odt_blocks is not None and odt_result is not None:
            origins.odt_blocks.append(
                OdtPartExtraction(part_index=len(parts) - 1, extraction=odt_result)
            )
        if origins.odt_notes is not None and odt_notes is not None:
            with suppress(Exception):
                origins.odt_notes.append(
                    OdtPartNoteOrigins(part_index=len(parts) - 1, extraction=odt_notes)
                )
        if origins.doc_notes is not None and doc_notes is not None:
            origins.doc_notes.append(
                DocPartNoteCapture(
                    part_index=len(parts) - 1,
                    extraction=_doc_note_capture(doc_notes),
                    projections=doc_notes.projections or None,
                    cfspec=doc_notes.cfspec or None,
                )
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


_NATIVE_WORD_FIELD_METHODS = (
    ("extract_with_notes", DocxTextExtractor.extract_with_notes),
    ("extract_with_view_status", DocxTextExtractor.extract_with_view_status),
    ("_view_status", DocxTextExtractor._view_status),
    ("_blocks", DocxTextExtractor._blocks),
    ("_read", DocxTextExtractor._read),
)


def _native_word_fields(value: DocxTextExtractor) -> bool:
    return type(value) is DocxTextExtractor and all(
        getattr(getattr(value, name, None), "__func__", None) is method
        for name, method in _NATIVE_WORD_FIELD_METHODS
    )


_NATIVE_PHYSICAL_PARTS = DocumentTextExtractor.extract_parts


_NATIVE_DOC_EXTRACT = DocTextExtractor.extract
_NATIVE_DOC_NOTE_EXTRACT = DocTextExtractor.extract_with_notes


def _native_doc_notes(value: object) -> bool:
    return type(value) is DocTextExtractor and all(
        getattr(getattr(value, name, None), "__func__", None) is implementation
        for name, implementation in (
            ("extract", _NATIVE_DOC_EXTRACT),
            ("extract_with_notes", _NATIVE_DOC_NOTE_EXTRACT),
        )
    )


def _doc_note_capture(value: DocNoteExtraction) -> DocNoteCapture:
    source = value.result
    fields: dict[str, object] = {
        "input_sha256": value.input_sha256,
        "text_sha256": hashlib.sha256(source.legacy_text.encode()).hexdigest(),
        "state": "unavailable",
        "table_name": None,
        "main_characters": None,
        "footnote_characters": None,
        "endnote_characters": None,
        "validation": None,
        "unavailable_reason": "legacy_unavailable"
        if source.unavailable_reason == "legacy_extraction_unavailable"
        else "unsupported_or_invalid",
        "links": (),
    }
    unknown = DocNoteCapture.model_validate(fields)
    if source.links is None or source.validation != "source_characters_checked_cf_spec_unknown":
        return unknown
    fields.update(
        state="checked",
        table_name=source.links.table_name,
        main_characters=source.links.main_characters,
        footnote_characters=source.links.footnote_characters,
        endnote_characters=source.links.endnote_characters,
        validation=source.validation,
        unavailable_reason=None,
        links=tuple(
            {
                "kind": link.kind,
                "reference_cp": link.reference_cp,
                "note_start_cp": link.note_start_cp,
                "note_end_cp": link.note_end_cp,
                "story_start_cp": link.story_start_cp,
                "numbering_flag": link.numbering_flag,
            }
            for link in source.links.links
        ),
    )
    try:
        return DocNoteCapture.model_validate(fields)
    except ValueError:
        return unknown
