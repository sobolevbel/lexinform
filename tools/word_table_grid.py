"""Resolve direct OOXML table grid positions without projecting the document."""

from collections.abc import Mapping
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from lexinform.models import WordTextBlock

XmlPath = tuple[int, ...]
Columns = tuple[int, int]
_MAX_COLUMNS = 128
_MAX_GRID_AREA = 16_384
_MAX_TABLES = 16


@dataclass(frozen=True)
class WordGridCell:
    path: XmlPath
    row: int
    columns: Columns
    anchor: XmlPath


@dataclass(frozen=True)
class WordGridTable:
    path: XmlPath
    columns: int = 0
    rows: int = 0
    merged: tuple[WordGridCell, ...] = ()
    reason: str | None = None


def _property(node: ET.Element, tag: str) -> ET.Element | None:
    entries = node.findall(tag)
    if len(entries) > 1:
        raise ValueError("duplicate_grid_property")
    return entries[0] if entries else None


def _count(node: ET.Element | None, namespace: str, *, default: int, minimum: int) -> int:
    if node is None:
        return default
    raw = node.get(namespace + "val", "")
    if not 1 <= len(raw) <= 3 or not raw.isascii() or not raw.isdecimal():
        raise ValueError("invalid_grid_count")
    count = int(raw)
    if not minimum <= count <= _MAX_COLUMNS:
        raise ValueError("invalid_grid_count")
    return count


def _rows(
    table: ET.Element,
    namespace: str,
    path: XmlPath,
    columns: int,
    block_paths: Mapping[XmlPath, str],
) -> tuple[int, tuple[WordGridCell, ...]]:
    merged: list[WordGridCell] = []
    previous: dict[Columns, XmlPath] = {}
    row_number = 0
    for row_position, row in enumerate(table):
        if row.tag in (namespace + "tblPr", namespace + "tblGrid"):
            continue
        if row.tag != namespace + "tr":
            raise ValueError("unsupported_table_child")
        if (row_number + 1) * columns > _MAX_GRID_AREA:
            raise ValueError("grid_area_limit")
        row_path = (*path, row_position)
        if block_paths.get(row_path) != "table_row":
            raise ValueError("unmapped_row")
        properties = _property(row, namespace + "trPr")
        before = None if properties is None else _property(properties, namespace + "gridBefore")
        after = None if properties is None else _property(properties, namespace + "gridAfter")
        start = _count(before, namespace, default=0, minimum=0)
        current: dict[Columns, XmlPath] = {}
        for cell_position, cell in enumerate(row):
            if cell.tag in (namespace + "trPr", namespace + "tblPrEx"):
                continue
            if cell.tag != namespace + "tc":
                raise ValueError("unsupported_row_child")
            cell_path = (*row_path, cell_position)
            if block_paths.get(cell_path) != "table_cell":
                raise ValueError("unmapped_cell")
            properties = _property(cell, namespace + "tcPr")
            span = None if properties is None else _property(properties, namespace + "gridSpan")
            end = start + _count(span, namespace, default=1, minimum=1)
            if end > columns:
                raise ValueError("cell_outside_grid")
            interval = (start, end)
            anchor = cell_path
            vertical = None if properties is None else _property(properties, namespace + "vMerge")
            if properties is not None and _property(properties, namespace + "hMerge") is not None:
                raise ValueError("unsupported_horizontal_merge")
            if vertical is not None:
                value = vertical.get(namespace + "val", "continue")
                if value == "continue":
                    if interval not in previous:
                        raise ValueError("unmatched_vertical_merge")
                    anchor = previous[interval]
                elif value != "restart":
                    raise ValueError("invalid_vertical_merge")
                current[interval] = anchor
            if vertical is not None or end - start > 1:
                merged.append(WordGridCell(cell_path, row_number, interval, anchor))
            start = end
        if start + _count(after, namespace, default=0, minimum=0) != columns:
            raise ValueError("row_width_mismatch")
        previous = current
        row_number += 1
    return row_number, tuple(merged)


def word_table_grids(
    root: ET.Element, blocks: tuple[WordTextBlock, ...]
) -> tuple[WordGridTable, ...]:
    namespace = root.tag.rsplit("}", 1)[0] + "}"
    bodies = [(index, child) for index, child in enumerate(root) if child.tag == namespace + "body"]
    if len(bodies) != 1:
        raise ValueError("unsupported_grid_body")
    ((body_position, body),) = bodies
    block_paths = {block.path: block.kind for block in blocks}
    tables: list[WordGridTable] = []
    markers = {namespace + name for name in ("gridSpan", "vMerge", "hMerge")}
    revisions = {
        namespace + name
        for name in ("cellIns", "cellDel", "cellMerge", "tblGridChange", "tcPrChange", "trPrChange")
    }
    for index, table in enumerate(body):
        if table.tag != namespace + "tbl" or not any(node.tag in markers for node in table.iter()):
            continue
        if len(tables) == _MAX_TABLES:
            raise ValueError("table_grid_count_limit")
        path = (body_position, index)
        try:
            if any(node is not table for node in table.iter(namespace + "tbl")):
                raise ValueError("nested_table")
            if any(node.tag in revisions for node in table.iter()):
                raise ValueError("grid_revision")
            grid = _property(table, namespace + "tblGrid")
            if (
                grid is None
                or not 1 <= len(grid) <= _MAX_COLUMNS
                or any(node.tag != namespace + "gridCol" for node in grid)
            ):
                raise ValueError("unsupported_grid_columns")
            rows, merged = _rows(table, namespace, path, len(grid), block_paths)
            if not merged:
                raise ValueError("unmapped_merge")
            tables.append(WordGridTable(path, len(grid), rows, merged))
        except ValueError as exc:
            tables.append(WordGridTable(path, reason=str(exc)))
    return tuple(tables)
