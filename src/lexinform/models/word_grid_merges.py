"""Bounded vertical merge evidence for direct OOXML body tables."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lexinform.models.physical_inventory import Position, Sha256

MAX_WORD_GRID_MERGE_BYTES = 4096
_MAX_COLUMNS = 128
_MAX_GRID_AREA = 16_384
_MAX_TABLES = 16
type GridPosition = tuple[Position, Position]


class WordVerticalMerge(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    row: Position
    columns: GridPosition
    cells: tuple[GridPosition, ...] = Field(min_length=2, max_length=256)


class WordVerticalGridTable(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    path: GridPosition
    columns: Position = Field(gt=0, le=_MAX_COLUMNS)
    rows: Position = Field(gt=0, le=_MAX_GRID_AREA)
    merges: tuple[WordVerticalMerge, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def consistent_chains(self) -> Self:
        if self.columns * self.rows > _MAX_GRID_AREA:
            raise ValueError("Word vertical merge grid exceeds its area limit")
        anchors = [(merge.row, merge.columns[0]) for merge in self.merges]
        if anchors != sorted(set(anchors)):
            raise ValueError("Word vertical merges require distinct ordered anchors")
        physical_rows: dict[int, int] = {}
        row_cells: dict[int, list[tuple[int, int]]] = {}
        occupied: set[tuple[int, int]] = set()
        cells: set[GridPosition] = set()
        for merge in self.merges:
            start, end = merge.columns
            if not start < end <= self.columns or merge.row + len(merge.cells) > self.rows:
                raise ValueError("Word vertical merge interval escapes its grid")
            for row, cell in enumerate(merge.cells, start=merge.row):
                if cell in cells:
                    raise ValueError("Word vertical merge chains require distinct XML cells")
                cells.add(cell)
                if physical_rows.setdefault(row, cell[0]) != cell[0]:
                    raise ValueError("Word vertical merge XML row differs from its logical row")
                row_cells.setdefault(row, []).append((start, cell[1]))
                area = {(row, column) for column in range(start, end)}
                if not occupied.isdisjoint(area):
                    raise ValueError("Word vertical merge intervals cannot overlap")
                occupied.update(area)
        order = [physical_rows[row] for row in sorted(physical_rows)]
        if order != sorted(set(order)):
            raise ValueError("Word vertical merge rows require increasing XML positions")
        for coordinates in row_cells.values():
            cell_order = [physical for _, physical in sorted(coordinates)]
            if cell_order != sorted(cell_order):
                raise ValueError("Word vertical merge columns require increasing XML positions")
        return self


class WordVerticalGridMerges(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    profile: Literal["word-vertical-grid-merges-v1"] = "word-vertical-grid-merges-v1"
    input_sha256: Sha256
    body_manifest_sha256: Sha256
    notes_manifest_sha256: Sha256 | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    verified_tables: Position = Field(le=_MAX_TABLES)
    unresolved_tables: Position = Field(le=_MAX_TABLES)
    tables: tuple[WordVerticalGridTable, ...] = Field(max_length=_MAX_TABLES)

    @model_validator(mode="after")
    def bounded_tables(self) -> Self:
        paths = [table.path for table in self.tables]
        if not 1 <= self.verified_tables + self.unresolved_tables <= _MAX_TABLES:
            raise ValueError("Word vertical merge coverage requires observed tables")
        if len(paths) > self.verified_tables or paths != sorted(set(paths)):
            raise ValueError("Retained Word tables require distinct ordered verified coordinates")
        if len(self.model_dump_json().encode()) > MAX_WORD_GRID_MERGE_BYTES:
            raise ValueError("Stored Word vertical merges exceed their byte limit")
        return self


class WordHorizontalSpan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    row: Position
    columns: GridPosition
    cell: GridPosition


class WordHorizontalGridTable(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    path: GridPosition
    columns: Position = Field(gt=0, le=_MAX_COLUMNS)
    rows: Position = Field(gt=0, le=_MAX_GRID_AREA)
    spans: tuple[WordHorizontalSpan, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def consistent_spans(self) -> Self:
        if self.columns * self.rows > _MAX_GRID_AREA:
            raise ValueError("Word horizontal span grid exceeds its area limit")
        previous_row = previous_xml_row = previous_xml_cell = previous_end = -1
        for span in self.spans:
            start, end = span.columns
            xml_row, xml_cell = span.cell
            if not start + 1 < end <= self.columns or span.row >= self.rows:
                raise ValueError("Word horizontal span escapes its grid or covers one column")
            if span.row == previous_row:
                if xml_row != previous_xml_row or xml_cell <= previous_xml_cell:
                    raise ValueError("Word horizontal span XML cells require increasing positions")
                if start < previous_end:
                    raise ValueError("Word horizontal span intervals cannot overlap")
            elif span.row < previous_row or xml_row <= previous_xml_row:
                raise ValueError("Word horizontal span rows require increasing XML positions")
            previous_row, previous_xml_row, previous_xml_cell, previous_end = (
                span.row,
                xml_row,
                xml_cell,
                end,
            )
        return self


class WordHorizontalGridSpans(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    profile: Literal["word-horizontal-grid-spans-v1"] = "word-horizontal-grid-spans-v1"
    input_sha256: Sha256
    body_manifest_sha256: Sha256
    notes_manifest_sha256: Sha256 | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    tables: tuple[WordHorizontalGridTable, ...] = Field(min_length=1, max_length=_MAX_TABLES)

    @model_validator(mode="after")
    def bounded_tables(self) -> Self:
        paths = [table.path for table in self.tables]
        if paths != sorted(set(paths)):
            raise ValueError("Retained Word tables require distinct ordered verified coordinates")
        if len(self.model_dump_json().encode()) > MAX_WORD_GRID_MERGE_BYTES:
            raise ValueError("Stored Word horizontal spans exceed their byte limit")
        return self
