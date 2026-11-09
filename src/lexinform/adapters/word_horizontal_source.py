"""Bind optional horizontal table metadata to an unchanged standalone Word extraction."""

import hashlib
from contextlib import suppress
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from lexinform.adapters.document_text import DocxTextExtractor
from lexinform.adapters.word_grid_capture import capture_word_horizontal_spans
from lexinform.extraction_records import summarize_word_grid_merges
from lexinform.models.documents import WordTextBlock
from lexinform.models.extractions import (
    DocumentExtraction,
    ExtractionResult,
    word_mixed_grid_parent_sha256,
)
from lexinform.models.word_grid_merges import (
    MAX_WORD_MIXED_SPANS,
    WordHorizontalSpan,
    WordMixedGridSpans,
    WordMixedGridTable,
)
from lexinform.physical_walk_capture import suspend_physical_walk_capture
from lexinform.word_structure import WORDML_NAMESPACES
from lexinform.word_table_grid import has_horizontal_grid_table, word_table_grids

_MAX_XML_BYTES = 2 * 1024 * 1024
_BODY = "word/document.xml"


def enrich_word_horizontal_source(
    reader: DocxTextExtractor, data: bytes, observed: DocumentExtraction
) -> DocumentExtraction:
    result = observed.result
    if result.physical_part_count != 1 or len(result.selected_parts) != 1:
        return observed
    (part,) = result.selected_parts
    if (
        (part.format, part.coordinates, part.text_view)
        != ("word", "word-blocks-v1", "legacy-word-body-v1")
        or part.sha256 != result.artifact_sha256
        or not data.startswith(b"PK\x03\x04")
    ):
        return observed
    with ZipFile(BytesIO(data)) as archive:
        info = archive.getinfo(_BODY)
        if (
            sum(
                entry.file_size
                for entry in archive.infolist()
                if entry.filename in (_BODY, "word/footnotes.xml", "word/endnotes.xml")
            )
            > _MAX_XML_BYTES
        ):
            return observed
        with archive.open(info) as stream:
            xml = stream.read(_MAX_XML_BYTES + 1)
    if len(xml) > _MAX_XML_BYTES or b"gridSpan" not in xml or b"<!DOCTYPE" in xml:
        return observed
    root = ET.fromstring(xml)
    if not any(root.tag == f"{{{ns}}}document" for ns in WORDML_NAMESPACES):
        return observed
    standalone = has_horizontal_grid_table(root)
    vertical = part.word_grid_merges
    mixed_paths = frozenset(table.path for table in vertical.tables) if vertical else frozenset()
    if not standalone and not mixed_paths:
        return observed
    with suspend_physical_walk_capture():
        detail = reader.extract_with_notes(data)
    if (
        detail.body.body_state != "read"
        or detail.body.input_sha256 != part.sha256
        or detail.body.xml_sha256 != hashlib.sha256(xml).hexdigest()
    ):
        return observed
    enriched = result
    if standalone:
        layouts = word_table_grids(root, detail.body.blocks, horizontal_only=True)
        horizontal = capture_word_horizontal_spans(layouts, detail)
        if horizontal is not None:
            enriched = summarize_word_grid_merges(result, None, {part.physical_index: horizontal})
    if mixed_paths:
        with suppress(Exception):
            enriched = _with_mixed_grid_spans(enriched, root, detail.body.blocks, mixed_paths)
    return observed if enriched is result else observed.model_copy(update={"result": enriched})


def _with_mixed_grid_spans(
    result: ExtractionResult,
    root: ET.Element,
    blocks: tuple[WordTextBlock, ...],
    paths: frozenset[tuple[int, int]],
) -> ExtractionResult:
    (part,) = result.selected_parts
    vertical = part.word_grid_merges
    assert vertical is not None, "mixed paths come from the retained vertical grid"
    namespace = root.tag.rsplit("}", 1)[0] + "}"
    property_path = f"{namespace}tcPr/{namespace}vMerge"
    layouts = word_table_grids(root, blocks, table_paths=paths)
    tables: tuple[WordMixedGridTable, ...] = ()
    parent = word_mixed_grid_parent_sha256(vertical)
    for layout in layouts:
        if layout.reason is not None:
            continue
        table = root[layout.path[0]][layout.path[1]]
        spans = tuple(
            WordHorizontalSpan(
                row=cell.row, columns=cell.columns, cell=(cell.path[-2], cell.path[-1])
            )
            for cell in layout.merged
            if cell.anchor == cell.path
            and cell.columns[1] - cell.columns[0] > 1
            and table[cell.path[-2]][cell.path[-1]].find(property_path) is None
        )
        for count in range(min(len(spans), MAX_WORD_MIXED_SPANS), 0, -1):
            try:
                retained = WordMixedGridTable(
                    path=(layout.path[0], layout.path[1]),
                    columns=layout.columns,
                    rows=layout.rows,
                    observed_spans=len(spans),
                    spans=spans[:count],
                )
                mixed = WordMixedGridSpans(parent_sha256=parent, tables=(*tables, retained))
                updated = part.model_copy(update={"word_mixed_grid_spans": mixed})
                result = ExtractionResult.model_validate(
                    result.model_copy(update={"selected_parts": (updated,)})
                )
            except ValueError:
                continue
            tables = mixed.tables
            break
    return result
