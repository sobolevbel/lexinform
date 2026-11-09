"""Add exact soft-row markers to unchanged native ODT results when existing budgets allow."""

import hashlib
from collections.abc import Iterator
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from lexinform.adapters.document_text import OdtTextExtractor
from lexinform.models.extractions import (
    DocumentExtraction,
    ExtractionResult,
    odt_soft_row_parent_sha256,
)
from lexinform.models.odt_tables import OdtSoftRow, OdtSoftRowBreak, OdtSoftRows
from lexinform.physical_walk_capture import suspend_physical_walk_capture
from lexinform.sections import PAGE_BREAK

_OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
_TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_SOFT_BREAK = _TEXT + "soft-page-break"
_CELLS = {_TABLE + "table-cell", _TABLE + "covered-table-cell"}
_SPACING = {_TEXT + "tab": "\t", _TEXT + "line-break": "\n", _SOFT_BREAK: PAGE_BREAK}
_MAX_XML_BYTES = 2 * 1024 * 1024


def _paragraph_tokens(
    node: ET.Element, path: tuple[int, ...]
) -> Iterator[tuple[str, tuple[int, ...] | None]]:
    if len(path) > 11:
        raise ValueError("ODT soft-row XML path exceeds its bound")
    yield node.text or "", None
    for index, child in enumerate(node):
        child_path = (*path, index)
        if child.tag in (_TEXT + "span", _TEXT + "a"):
            yield from _paragraph_tokens(child, child_path)
        elif len(child) or child.text:
            raise ValueError("ODT soft-row inline token has unsupported content")
        elif child.tag in _SPACING and not child.attrib:
            yield _SPACING[child.tag], child_path if child.tag == _SOFT_BREAK else None
        elif child.tag == _TEXT + "s" and set(child.attrib) <= {_TEXT + "c"}:
            count = child.get(_TEXT + "c", "1")
            if not 1 <= len(count) <= 4 or not count.isascii() or not count.isdecimal():
                raise ValueError("ODT soft-row spacing is not bounded")
            spaces = int(count)
            if not 1 <= spaces <= 4096:
                raise ValueError("ODT soft-row spacing is not bounded")
            yield " " * spaces, None
        else:
            raise ValueError("ODT soft-row inline token is unsupported")
        yield child.tail or "", None


def _row_markers(row: ET.Element, text: str) -> tuple[OdtSoftRowBreak, ...]:
    if not 1 <= len(row) <= 64:
        raise ValueError("ODT soft row exceeds its cell bound")
    offset = 0
    breaks: list[OdtSoftRowBreak] = []
    cells: list[str] = []
    for index, cell in enumerate(row):
        if cell.tag not in _CELLS or (len(cell) and (len(cell) != 1 or cell[0].tag != _TEXT + "p")):
            raise ValueError("ODT soft row requires a single plain paragraph per cell")
        chunks: list[str] = []
        if len(cell):
            for fragment, path in _paragraph_tokens(cell[0], (index, 0)):
                if path is not None:
                    breaks.append(OdtSoftRowBreak(path=path, offset=offset))
                chunks.append(fragment)
                offset += len(fragment)
        cells.append("".join(chunks))
        offset += 1
    if "\t".join(cells) != text or tuple(mark.offset for mark in breaks) != tuple(
        index for index, char in enumerate(text) if char == PAGE_BREAK
    ):
        raise ValueError("ODT soft row differs from the native emitted block")
    return tuple(breaks)


def _direct_rows(body: ET.Element) -> dict[tuple[int, int], ET.Element]:
    return {
        (table_index, row_index): row
        for table_index, table in enumerate(body)
        if table.tag == _TABLE + "table"
        for row_index, row in enumerate(table)
        if row.tag == _TABLE + "table-row" and next(row.iter(_SOFT_BREAK), None) is not None
    }


def enrich_odt_soft_rows(
    reader: OdtTextExtractor, data: bytes, observed: DocumentExtraction
) -> DocumentExtraction:
    result = observed.result
    if result.physical_part_count != 1 or len(result.selected_parts) != 1:
        return observed
    (part,) = result.selected_parts
    witness = part.block_copy_witness
    if (
        (part.format, part.coordinates) != ("odt", "odt-blocks-v1")
        or part.sha256 != result.artifact_sha256
        or witness is None
        or witness.xml_sha256 is None
        or not data.startswith(b"PK\x03\x04")
    ):
        return observed
    with ZipFile(BytesIO(data)) as archive:
        info = archive.getinfo("content.xml")
        if info.file_size > _MAX_XML_BYTES:
            return observed
        with archive.open(info) as stream:
            xml = stream.read(_MAX_XML_BYTES + 1)
    if (
        len(xml) > _MAX_XML_BYTES
        or b"soft-page-break" not in xml
        or b"<!DOCTYPE" in xml
        or hashlib.sha256(xml).hexdigest() != witness.xml_sha256
    ):
        return observed
    body = ET.fromstring(xml).find(f"{_OFFICE}body/{_OFFICE}text")
    if body is None or not (direct := _direct_rows(body)):
        return observed
    with suspend_physical_walk_capture():
        extracted = reader.extract_odt_blocks(data)
    if (
        extracted.body_state != "read"
        or extracted.input_sha256 != part.sha256
        or extracted.xml_sha256 != witness.xml_sha256
        or extracted.manifest_sha256 != part.manifest_sha256
        or extracted.legacy_text != observed.document.text
    ):
        return observed
    blocks = {block.block_id: block for block in extracted.blocks}
    rows: list[OdtSoftRow] = []
    start = 0
    for identity in extracted.legacy_blocks:
        block = blocks[identity]
        end = start + len(block.text)
        if len(block.path) == 2 and block.kind == "table_row":
            path = (block.path[0], block.path[1])
            row = direct.get(path)
            if row is not None and not any(
                run.block_id == identity or start < run.source_end and run.source_start < end
                for run in witness.runs
            ):
                try:
                    markers = _row_markers(row, block.text)
                    rows.append(
                        OdtSoftRow(
                            path=path,
                            span=(start, end),
                            row_sha256=block.text_sha256,
                            breaks=markers,
                        )
                    )
                except ValueError:
                    pass
                if len(rows) == 4:
                    break
        start = end + 1
    parent = odt_soft_row_parent_sha256(witness)
    for count in range(len(rows), 0, -1):
        try:
            receipt = OdtSoftRows(parent_sha256=parent, rows=tuple(rows[:count]))
            updated = part.model_copy(update={"odt_soft_rows": receipt})
            enriched = ExtractionResult.model_validate(
                result.model_copy(update={"selected_parts": (updated,)})
            )
        except ValueError:
            continue
        return observed.model_copy(update={"result": enriched})
    return observed
