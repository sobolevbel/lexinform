"""Bounded physical and logical coordinates for retained ODT merged tables."""

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
