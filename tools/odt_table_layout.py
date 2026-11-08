"""Verify ODT merged-cell coordinates in a bounded logical table."""

from collections.abc import Iterator
from xml.etree import ElementTree as ET

TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
_ROW_GROUPS = {TABLE + name for name in ("table-header-rows", "table-rows", "table-row-group")}
_COLUMN_NODES = {
    TABLE + name
    for name in ("table-column", "table-columns", "table-header-columns", "table-column-group")
}
_ROW = TABLE + "table-row"
_CELL = TABLE + "table-cell"
_COVERED = TABLE + "covered-table-cell"
_SPANS = (TABLE + "number-rows-spanned", TABLE + "number-columns-spanned")
_MAX_MERGE_AREA = 4096
_MAX_LOGICAL_CELLS = 16_384

Path = tuple[int, ...]
Position = tuple[int, int]
Cell = tuple[Position, Path]


def _rows(node: ET.Element, path: Path) -> Iterator[tuple[Path, ET.Element]]:
    for index, child in enumerate(node):
        position = (*path, index)
        if child.tag in _ROW_GROUPS:
            yield from _rows(child, position)
        elif child.tag not in _COLUMN_NODES:
            yield position, child


def _positive(node: ET.Element, attribute: str, reason: str) -> int:
    raw = node.get(attribute, "1")
    if len(raw) > 4 or not raw.isascii() or not raw.isdecimal() or int(raw) < 1:
        raise ValueError(reason)
    return int(raw)


def _cells(
    rows: list[tuple[Path, ET.Element]], block_paths: set[Path]
) -> Iterator[tuple[Cell, ET.Element]]:
    row_index = 0
    remaining = _MAX_LOGICAL_CELLS
    for row_path, row in rows:
        if row.tag != _ROW:
            raise ValueError("unsupported_table_child")
        height = _positive(row, TABLE + "number-rows-repeated", "invalid_row_repeat")
        physical: list[tuple[Path, ET.Element, int]] = []
        width = 0
        for index, cell in enumerate(row):
            path = (*row_path, index)
            if cell.tag not in (_CELL, _COVERED) or path not in block_paths:
                raise ValueError("unmapped_cell")
            repeat = _positive(cell, TABLE + "number-columns-repeated", "invalid_cell_repeat")
            physical.append((path, cell, repeat))
            width += repeat
        cost = height * max(1, width)
        if cost > remaining:
            raise ValueError("logical_table_limit")
        remaining -= cost
        for y in range(row_index, row_index + height):
            column = 0
            for path, cell, repeat in physical:
                for x in range(column, column + repeat):
                    yield ((y, x), path), cell
                column += repeat
        row_index += height


def _merges(rows: list[tuple[Path, ET.Element]], block_paths: set[Path]) -> dict[Cell, list[Cell]]:
    owners: dict[Position, Cell] = {}
    matches: dict[Cell, list[Cell]] = {}
    remaining = _MAX_MERGE_AREA
    for key, cell in _cells(rows, block_paths):
        position, _ = key
        row_index, column = position
        owner = owners.pop(position, None)
        if cell.tag == _COVERED:
            if owner is None or any(attribute in cell.attrib for attribute in _SPANS):
                raise ValueError("unmatched_covered_cell")
            matches[owner].append(key)
            continue
        if owner is not None:
            raise ValueError("occupied_cell")
        height, width = (_positive(cell, attribute, "invalid_span") for attribute in _SPANS)
        area = height * width - 1
        if area > remaining:
            raise ValueError("merge_area_limit")
        if not area:
            continue
        remaining -= area
        matches[key] = []
        for y in range(row_index, row_index + height):
            for x in range(column, column + width):
                target = (y, x)
                if target == position:
                    continue
                if target in owners:
                    raise ValueError("overlapping_spans")
                owners[target] = key
    if owners:
        raise ValueError("missing_covered_cell")
    return matches


def merged_table_layouts(body: ET.Element, block_paths: set[Path]) -> list[dict[str, object]]:
    reports: list[dict[str, object]] = []
    pending: list[tuple[Path, ET.Element]] = [((), body)]
    while pending:
        path, node = pending.pop()
        pending.extend(((*path, index), node[index]) for index in reversed(range(len(node))))
        if node.tag != TABLE + "table":
            continue
        rows = list(_rows(node, path))
        if not any(
            cell.tag == _COVERED or any(attribute in cell.attrib for attribute in _SPANS)
            for _, row in rows
            if row.tag == _ROW
            for cell in row
        ):
            continue
        try:
            matches = _merges(rows, block_paths)
        except ValueError as exc:
            reports.append({"table_path": path, "status": "unresolved", "reason": str(exc)})
        else:
            reports.append(
                {
                    "table_path": path,
                    "status": "verified",
                    "merges": [
                        {
                            "anchor": anchor_path,
                            "covered": [path for _, path in covered],
                            "logical_anchor": position,
                            "logical_covered": [position for position, _ in covered],
                        }
                        for (position, anchor_path), covered in matches.items()
                    ],
                }
            )
    return reports
