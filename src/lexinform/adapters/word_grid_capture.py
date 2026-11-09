"""Bind bounded grid evidence to the same-pass Word extraction."""

from collections import defaultdict
from collections.abc import Sequence

from lexinform.models.documents import WordDetailedExtraction
from lexinform.models.word_grid_merges import (
    WordHorizontalGridSpans,
    WordHorizontalGridTable,
    WordHorizontalSpan,
    WordVerticalGridMerges,
    WordVerticalGridTable,
    WordVerticalMerge,
)
from lexinform.word_table_grid import WordGridCell, WordGridTable, XmlPath


def _table(layout: WordGridTable) -> WordVerticalGridTable | None:
    groups: dict[XmlPath, list[WordGridCell]] = defaultdict(list)
    for cell in layout.merged:
        groups[cell.anchor].append(cell)
    merges = tuple(
        WordVerticalMerge(
            row=cells[0].row,
            columns=cells[0].columns,
            cells=tuple((cell.path[-2], cell.path[-1]) for cell in cells),
        )
        for cells in groups.values()
        if len(cells) > 1
    )
    if not merges:
        return None
    return WordVerticalGridTable(
        path=(layout.path[0], layout.path[1]),
        columns=layout.columns,
        rows=layout.rows,
        merges=merges,
    )


def capture_word_grid_merges(
    layouts: Sequence[WordGridTable], legacy: WordDetailedExtraction
) -> WordVerticalGridMerges | None:
    tables: tuple[WordVerticalGridTable, ...] = ()
    verified = unresolved = 0
    body = legacy.body
    manifest = legacy.notes.body_manifest_sha256
    notes = legacy.notes.manifest_sha256
    for layout in layouts:
        if layout.reason is not None:
            unresolved += 1
            continue
        try:
            table = _table(layout)
        except ValueError:
            unresolved += 1
            continue
        if table is None:
            continue
        verified += 1
        candidate = (*tables, table)
        try:
            WordVerticalGridMerges(
                input_sha256=body.input_sha256,
                body_manifest_sha256=manifest,
                notes_manifest_sha256=notes,
                verified_tables=verified,
                unresolved_tables=unresolved,
                tables=candidate,
            )
        except ValueError:
            continue
        tables = candidate
    if not verified and not unresolved:
        return None
    return WordVerticalGridMerges(
        input_sha256=body.input_sha256,
        body_manifest_sha256=manifest,
        notes_manifest_sha256=notes,
        verified_tables=verified,
        unresolved_tables=unresolved,
        tables=tables,
    )


def capture_word_horizontal_spans(
    layouts: Sequence[WordGridTable], legacy: WordDetailedExtraction
) -> WordHorizontalGridSpans | None:
    tables: tuple[WordHorizontalGridTable, ...] = ()
    retained = None
    for layout in layouts:
        if layout.reason is not None:
            continue
        try:
            table = WordHorizontalGridTable(
                path=(layout.path[0], layout.path[1]),
                columns=layout.columns,
                rows=layout.rows,
                spans=tuple(
                    WordHorizontalSpan(
                        row=cell.row, columns=cell.columns, cell=(cell.path[-2], cell.path[-1])
                    )
                    for cell in layout.merged
                ),
            )
            candidate = WordHorizontalGridSpans(
                input_sha256=legacy.body.input_sha256,
                body_manifest_sha256=legacy.notes.body_manifest_sha256,
                notes_manifest_sha256=legacy.notes.manifest_sha256,
                tables=(*tables, table),
            )
        except ValueError:
            continue
        tables = candidate.tables
        retained = candidate
    return retained
