"""Retain complete, bounded tables from the native ODT XML traversal."""

from xml.etree import ElementTree as ET

from lexinform.models.documents import OdtTextExtraction
from lexinform.models.odt_tables import OdtMergedCell, OdtMergedTable, OdtTableMerges
from lexinform.odt_table_layout import TableLayout, merged_table_layouts


def _retained_table(layout: TableLayout) -> OdtMergedTable:
    merges = []
    for (position, anchor), covered in layout.merges.items():
        last, _ = covered[-1]
        height, width = last[0] - position[0] + 1, last[1] - position[1] + 1
        merges.append(
            OdtMergedCell(
                anchor=anchor,
                position=position,
                shape=(height, width),
                covered=tuple(path for _, path in covered),
            )
        )
    return OdtMergedTable(path=layout.path, merges=tuple(merges))


def capture_odt_tables(body: ET.Element, extracted: OdtTextExtraction) -> OdtTableMerges | None:
    tables: tuple[OdtMergedTable, ...] = ()
    verified = unresolved = 0
    manifest: str | None = None
    block_paths = {block.path for block in extracted.blocks if block.kind == "table_cell"}
    for layout in merged_table_layouts(body, block_paths):
        if layout.reason is not None:
            unresolved += 1
            continue
        verified += 1
        if manifest is None:
            manifest = extracted.manifest_sha256
        try:
            candidate = (*tables, _retained_table(layout))
            OdtTableMerges(
                input_sha256=extracted.input_sha256,
                body_manifest_sha256=manifest,
                verified_tables=verified,
                unresolved_tables=unresolved,
                tables=candidate,
            )
        except ValueError:
            continue
        tables = candidate
    if not verified and not unresolved:
        return None
    return OdtTableMerges(
        input_sha256=extracted.input_sha256,
        body_manifest_sha256=manifest or extracted.manifest_sha256,
        verified_tables=verified,
        unresolved_tables=unresolved,
        tables=tables,
    )
