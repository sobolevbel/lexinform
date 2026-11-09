"""Bind optional horizontal table metadata to an unchanged standalone Word extraction."""

import hashlib
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from lexinform.adapters.document_text import DocxTextExtractor
from lexinform.adapters.word_grid_capture import capture_word_horizontal_spans
from lexinform.extraction_records import summarize_word_grid_merges
from lexinform.models.extractions import DocumentExtraction
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
    if not has_horizontal_grid_table(root):
        return observed
    with suspend_physical_walk_capture():
        detail = reader.extract_with_notes(data)
    if (
        detail.body.body_state != "read"
        or detail.body.input_sha256 != part.sha256
        or detail.body.xml_sha256 != hashlib.sha256(xml).hexdigest()
    ):
        return observed
    layouts = word_table_grids(root, detail.body.blocks, horizontal_only=True)
    horizontal = capture_word_horizontal_spans(layouts, detail)
    if horizontal is None:
        return observed
    enriched = summarize_word_grid_merges(result, None, {part.physical_index: horizontal})
    return observed if enriched is result else observed.model_copy(update={"result": enriched})
