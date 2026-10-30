"""Read primary references using stored owners, explicit links and primary receipts."""

import sqlite3
from collections.abc import Iterator

from lexinform.json_storage import read_json
from lexinform.models.computations import ComputationResult, ResultApplication
from lexinform.primary_projections import (
    PrimaryProjectionContext,
    PrimaryProjectionError,
    PrimaryProjectionSource,
    ProjectionOwner,
    compact_primary_projection,
    read_primary_json,
)
from lexinform.saved_records import (
    BILL_SNAPSHOT_FIELDS,
    CONTEXT_RESULT_TABLES,
    SavedRecord,
    saved_records,
)


class PrimaryProjectionReader:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def _rows(self, sql: str, parameters: tuple[object, ...]) -> list[sqlite3.Row]:
        cursor = self.connection.cursor()
        cursor.row_factory = sqlite3.Row
        try:
            return list(cursor.execute(sql, parameters))
        finally:
            cursor.close()

    def compact(self, raw: str, owner: ProjectionOwner) -> str:
        try:
            value = read_json(raw)
        except ValueError, TypeError, OverflowError, RecursionError:
            return raw
        if not isinstance(value, dict):
            return raw
        identifier = value.get("computation_id")
        if not isinstance(identifier, str):
            return raw
        source = self._source(identifier, (), ready=False)
        return compact_primary_projection(raw, source, owner)

    def _aliases(self, owner: ProjectionOwner) -> tuple[ProjectionOwner, ...]:
        rows = self._rows(
            "SELECT source.term, source.number FROM bills target JOIN bills source"
            " ON source.term=target.term AND source.number=target.linked_number"
            " AND source.linked_number=target.number AND source.status='linked'"
            " WHERE target.term=? AND target.number=?",
            (owner.term, owner.number),
        )
        return tuple(ProjectionOwner(row["term"], row["number"]) for row in rows)

    def _source(
        self, result_id: str, owners: tuple[ProjectionOwner, ...], *, ready: bool
    ) -> PrimaryProjectionSource | None:
        rows = self._rows("SELECT * FROM computation_results WHERE result_id=?", (result_id,))
        if not rows:
            return None
        row = rows[0]
        result = ComputationResult.model_validate_json(row["result_json"])
        expected = (
            result.result_id,
            result.provider,
            result.batch_id,
            result.custom_id,
            result.term,
            result.number,
        )
        columns = ("result_id", "provider", "batch_id", "custom_id", "term", "number")
        if expected != tuple(row[column] for column in columns):
            raise PrimaryProjectionError("primary result columns differ from immutable identity")
        applications: list[ResultApplication] = []
        for owner in owners:
            for stored in self._rows(
                "SELECT * FROM result_applications WHERE result_id=? AND term=? AND number=?"
                " AND phase=? AND generation IS NOT NULL"
                + (" AND generation=?" if ready else "")
                + " ORDER BY id LIMIT 1",
                (result_id, owner.term, owner.number, "collection" if ready else "projection")
                + ((result.generation,) if ready else ()),
            ):
                item = ResultApplication.model_validate_json(stored["application_json"])
                if (
                    item.result_id != result_id
                    or (item.term, item.number) != (owner.term, owner.number)
                    or item.generation != stored["generation"]
                    or item.phase != stored["phase"]
                    or item.target is not None
                ):
                    raise PrimaryProjectionError("primary application columns differ from receipt")
                applications.append(item)
        return PrimaryProjectionSource(row["result_json"], tuple(applications))

    def materialize(
        self, column: str, raw: str, owner: ProjectionOwner | None, generation: int | None = None
    ) -> str:
        try:
            return self._materialize(column, raw, owner, generation)
        except PrimaryProjectionError:
            raise
        except (ValueError, TypeError, RecursionError, sqlite3.Error) as error:
            raise PrimaryProjectionError("invalid stored primary projection") from error

    def _materialize(
        self, column: str, raw: str, owner: ProjectionOwner | None, generation: int | None = None
    ) -> str:
        if "modern_primary_ref" not in raw and "\\u" not in raw:
            return raw
        value = read_json(raw)
        marked = [
            item.ref
            for item in saved_records(value, "", encoded_fields=BILL_SNAPSHOT_FIELDS)
            if isinstance(item, SavedRecord) and "modern_primary_ref" in item.value
        ]
        if not marked:
            return raw
        expected = "/record" if column == "ready_analysis_json" else ""
        if column not in ("analysis_json", "ready_analysis_json", "record_json") or marked != [
            expected
        ]:
            raise PrimaryProjectionError("primary reference is outside an admitted record position")
        if owner is None:
            raise PrimaryProjectionError("primary projection has no known consumer owner")
        context = PrimaryProjectionContext(
            owner,
            self._aliases(owner),
            "ready"
            if column == "ready_analysis_json"
            else "memo"
            if column == "record_json"
            else "current",
            generation,
        )
        return read_primary_json(
            raw,
            lambda result_id: self._source(
                result_id, (owner, *context.source_aliases), ready=context.role == "ready"
            ),
            context,
        )


def validate_primary_cost_projections(connection: sqlite3.Connection) -> None:
    reader = PrimaryProjectionReader(connection)
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        for row in cursor.execute(
            "SELECT term, number, analysis_json FROM bills"
            " WHERE json_type(analysis_json, '$.modern_primary_ref') IS NOT NULL"
        ):
            reader.materialize(
                "analysis_json", row["analysis_json"], ProjectionOwner(row["term"], row["number"])
            )
    finally:
        cursor.close()


def primary_projection_issues(connection: sqlite3.Connection) -> Iterator[tuple[str, str]]:
    reader = PrimaryProjectionReader(connection)
    for table, shape in CONTEXT_RESULT_TABLES.items():
        existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        candidates = (
            (*shape.columns, "request_json") if table == "llm_batch_intents" else shape.columns
        )
        columns = [column for column in candidates if column in existing]
        keys = tuple(
            dict.fromkeys(
                (
                    *shape.keys,
                    *(k for k in ("term", "number", "analysis_generation") if k in existing),
                )
            )
        )
        if not columns:
            continue
        cursor = connection.cursor()
        cursor.row_factory = sqlite3.Row
        try:
            for row in cursor.execute(f"SELECT {', '.join((*keys, *columns))} FROM {table}"):
                owner = (
                    ProjectionOwner(row["term"], row["number"])
                    if "term" in keys
                    and "number" in keys
                    and isinstance(row["term"], int)
                    and isinstance(row["number"], str)
                    else None
                )
                for column in columns:
                    raw = row[column]
                    if not isinstance(raw, str):
                        continue
                    try:
                        reader.materialize(
                            column,
                            raw,
                            owner,
                            row["analysis_generation"] if "analysis_generation" in keys else None,
                        )
                    except ValueError, TypeError, RecursionError, sqlite3.Error:
                        ref = "/".join((table, *(str(row[key]) for key in shape.keys), column))
                        yield "invalid_modern_primary_projection", ref
        finally:
            cursor.close()


def validate_primary_projections(connection: sqlite3.Connection) -> None:
    issue = next(primary_projection_issues(connection), None)
    if issue is not None:
        raise PrimaryProjectionError(f"{issue[0]}: {issue[1]}")
