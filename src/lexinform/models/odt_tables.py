"""Bounded physical and logical coordinates for retained ODT merged tables."""

from itertools import pairwise
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lexinform.models.odt_notes import Path
from lexinform.models.physical_inventory import Position, Sha256

MAX_ODT_TABLE_BYTES = 4096
_MAX_COVERED = 256
type CellPosition = tuple[Position, Position]


class OdtMergedCell(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    anchor: Path
    position: CellPosition
    shape: CellPosition
    covered: tuple[Path, ...] = Field(min_length=1, max_length=_MAX_COVERED)

    @model_validator(mode="after")
    def complete_rectangle(self) -> Self:
        height, width = self.shape
        if not height or not width or height * width != len(self.covered) + 1:
            raise ValueError("An ODT merge requires one physical path for each covered position")
        return self

    def positions(self) -> set[CellPosition]:
        row, column = self.position
        height, width = self.shape
        return {(y, x) for y in range(row, row + height) for x in range(column, column + width)}


class OdtMergedTable(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    path: Path
    merges: tuple[OdtMergedCell, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def consistent_coordinates(self) -> Self:
        anchors = [merge.position for merge in self.merges]
        if anchors != sorted(anchors):
            raise ValueError("Retained ODT merges require logical row order")
        if {merge.anchor for merge in self.merges}.intersection(
            path for merge in self.merges for path in merge.covered
        ):
            raise ValueError("An ODT XML cell cannot be both an anchor and a covered cell")
        occupied: set[CellPosition] = set()
        for merge in self.merges:
            if (
                any(
                    path[: len(self.path)] != self.path or len(path) < len(self.path) + 2
                    for path in (merge.anchor, *merge.covered)
                )
                or merge.anchor in merge.covered
            ):
                raise ValueError(
                    "An ODT merge requires distinct anchor and covered XML cells in its table"
                )
            positions = merge.positions()
            if not occupied.isdisjoint(positions):
                raise ValueError("Retained ODT merge rectangles cannot overlap")
            occupied.update(positions)
        return self


class OdtTableMerges(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    profile: Literal["odt-table-merges-v1"] = "odt-table-merges-v1"
    input_sha256: Sha256
    body_manifest_sha256: Sha256
    verified_tables: Position
    unresolved_tables: Position
    tables: tuple[OdtMergedTable, ...] = Field(max_length=16)

    @model_validator(mode="after")
    def bounded_tables(self) -> Self:
        paths = [table.path for table in self.tables]
        if len(paths) > self.verified_tables or paths != sorted(set(paths)):
            raise ValueError("Retained ODT tables require distinct ordered verified coordinates")
        if not self.verified_tables and not self.unresolved_tables:
            raise ValueError("Retained ODT table metadata requires an observed merge")
        if len(self.model_dump_json().encode()) > MAX_ODT_TABLE_BYTES:
            raise ValueError("Stored ODT table metadata exceeds its byte limit")
        return self


class OdtSoftRowBreak(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    path: Path
    offset: Position

    @model_validator(mode="after")
    def direct_cell_paragraph(self) -> Self:
        if not 3 <= len(self.path) <= 12 or self.path[1] != 0:
            raise ValueError("ODT soft row breaks require a path inside one direct cell paragraph")
        return self


class OdtSoftRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    path: tuple[Position, Position]
    span: tuple[Position, Position]
    row_sha256: Sha256
    breaks: tuple[OdtSoftRowBreak, ...] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def ordered_breaks(self) -> Self:
        start, end = self.span
        offsets = [item.offset for item in self.breaks]
        paths = [item.path for item in self.breaks]
        if (
            end - start <= len(offsets)
            or offsets != sorted(set(offsets))
            or offsets[-1] >= end - start
            or paths != sorted(set(paths))
        ):
            raise ValueError(
                "ODT soft row breaks require distinct ordered in-row offsets and XML paths"
            )
        return self


class OdtSoftRows(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    profile: Literal["odt-soft-rows-v1"] = "odt-soft-rows-v1"
    parent_sha256: Sha256
    rows: tuple[OdtSoftRow, ...] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def bounded_rows(self) -> Self:
        paths = [row.path for row in self.rows]
        if paths != sorted(set(paths)) or any(
            left.span[1] > right.span[0] for left, right in pairwise(self.rows)
        ):
            raise ValueError("ODT soft rows require distinct ordered disjoint rows")
        if len(self.model_dump_json().encode()) > MAX_ODT_TABLE_BYTES:
            raise ValueError("Stored ODT soft rows exceed their byte limit")
        return self
