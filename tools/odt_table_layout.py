"""Verify explicit ODT merged-cell coordinates without expanding repeated rows or cells."""

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

Path = tuple[int, ...]


def _rows(node: ET.Element, path: Path) -> Iterator[tuple[Path, ET.Element]]:
    for index, child in enumerate(node):
        position = (*path, index)
        if child.tag in _ROW_GROUPS:
            yield from _rows(child, position)
        elif child.tag not in _COLUMN_NODES:
            yield position, child


def _span(cell: ET.Element, attribute: str) -> int:
    raw = cell.get(attribute, "1")
    if len(raw) > 4 or not raw.isascii() or not raw.isdecimal() or int(raw) < 1:
        raise ValueError("invalid_span")
    return int(raw)


def _merges(rows: list[tuple[Path, ET.Element]], block_paths: set[Path]) -> dict[Path, list[Path]]:
    owners: dict[tuple[int, int], Path] = {}
    matches: dict[Path, list[Path]] = {}
    remaining = _MAX_MERGE_AREA
    for row_index, (row_path, row) in enumerate(rows):
        if row.tag != _ROW:
            raise ValueError("unsupported_table_child")
        if row.get(TABLE + "number-rows-repeated", "1") != "1":
            raise ValueError("repeated_rows")
        for column, cell in enumerate(row):
            cell_path = (*row_path, column)
            if cell.tag not in (_CELL, _COVERED) or cell_path not in block_paths:
                raise ValueError("unmapped_cell")
            if cell.get(TABLE + "number-columns-repeated", "1") != "1":
                raise ValueError("repeated_cells")
            position = (row_index, column)
            owner = owners.pop(position, None)
            if cell.tag == _COVERED:
                if owner is None or any(attribute in cell.attrib for attribute in _SPANS):
                    raise ValueError("unmatched_covered_cell")
                matches[owner].append(cell_path)
                continue
            if owner is not None:
                raise ValueError("occupied_cell")
            height, width = (_span(cell, attribute) for attribute in _SPANS)
            area = height * width - 1
            if area > remaining:
                raise ValueError("merge_area_limit")
            if not area:
                continue
            remaining -= area
            matches[cell_path] = []
            for y in range(row_index, row_index + height):
                for x in range(column, column + width):
                    target = (y, x)
                    if target == position:
                        continue
                    if target in owners:
                        raise ValueError("overlapping_spans")
                    owners[target] = cell_path
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
                        {"anchor": anchor, "covered": covered}
                        for anchor, covered in matches.items()
                    ],
                }
            )
    return reports
