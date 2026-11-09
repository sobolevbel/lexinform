"""Read primary references using stored owners, explicit links and primary receipts."""

import json
import sqlite3
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import NamedTuple

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


class _ApplicationKey(NamedTuple):
    result_id: str
    term: int
    number: str
    phase: str
    generation: int | None


@dataclass(frozen=True)
class _PrimaryReadScope:
    results: dict[str, sqlite3.Row | None]
    aliases: dict[ProjectionOwner, tuple[ProjectionOwner, ...]]
    applications: dict[_ApplicationKey, sqlite3.Row | None]


def _primary_reference(raw: object, *, ready: bool) -> str | None:
    if not isinstance(raw, str) or ("modern_primary_ref" not in raw and "\\u" not in raw):
        return None
    try:
        value = read_json(raw)
    except ValueError, TypeError, RecursionError:
        return None
    if ready and isinstance(value, dict):
        value = value.get("record")
    ref = value.get("modern_primary_ref") if isinstance(value, dict) else None
    return ref if isinstance(ref, str) else None


class PrimaryProjectionReader:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self._scope: _PrimaryReadScope | None = None

    @classmethod
    def for_bill_rows(
        cls, connection: sqlite3.Connection, rows: Sequence[sqlite3.Row]
    ) -> PrimaryProjectionReader:
        reader = cls(connection)
        refs: set[tuple[str, ProjectionOwner, bool]] = set()
        for row in rows:
            owner = ProjectionOwner(row["term"], row["number"])
            for column, ready in (("analysis_json", False), ("ready_analysis_json", True)):
                ref = _primary_reference(row[column], ready=ready)
                if ref is not None:
                    refs.add((ref, owner, ready))
        if not refs:
            return reader
        results: dict[str, sqlite3.Row | None] = dict.fromkeys(ref for ref, _, _ in refs)
        results.update(
            (row["result_id"], row)
            for row in reader._rows(
                "SELECT * FROM computation_results"
                " WHERE result_id IN (SELECT value FROM json_each(?))",
                (json.dumps(list(results)),),
            )
        )
        keys: set[_ApplicationKey] = set()
        for ref, owner, ready in refs:
            stored_result = results[ref]
            if stored_result is None:
                continue
            generation = None
            if ready:
                try:
                    generation = ComputationResult.model_validate_json(
                        stored_result["result_json"]
                    ).generation
                except ValueError, TypeError, RecursionError:
                    continue
            keys.add(
                _ApplicationKey(
                    ref,
                    owner.term,
                    owner.number,
                    "collection" if ready else "projection",
                    generation,
                )
            )
        aliases, applications = reader._bill_dependencies(keys)
        reader._scope = _PrimaryReadScope(results, aliases, applications)
        return reader

    def _bill_dependencies(
        self, keys: set[_ApplicationKey]
    ) -> tuple[
        dict[ProjectionOwner, tuple[ProjectionOwner, ...]],
        dict[_ApplicationKey, sqlite3.Row | None],
    ]:
        aliases: dict[ProjectionOwner, tuple[ProjectionOwner, ...]] = {}
        applications: dict[_ApplicationKey, sqlite3.Row | None] = {}
        if not keys:
            return aliases, applications
        for row in self._rows(
            """
            WITH requested(ref, target_term, target_number, role, rev) AS (
                SELECT json_extract(value,'$[0]'), json_extract(value,'$[1]'),
                       json_extract(value,'$[2]'), json_extract(value,'$[3]'),
                       json_extract(value,'$[4]') FROM json_each(?)
            ), candidates AS (
                SELECT requested.*, target_number AS candidate_number, 0 AS is_alias FROM requested
                UNION ALL
                SELECT requested.*, source.number, 1 FROM requested
                CROSS JOIN bills target ON target.term=target_term AND target.number=target_number
                CROSS JOIN bills source ON source.term=target.term
                AND source.number=target.linked_number
                AND source.linked_number=target.number AND source.status='linked'
            ), first_receipts AS (
                SELECT candidates.*, MIN(matching.id) AS receipt_id
                FROM candidates LEFT JOIN result_applications matching
                ON matching.result_id=ref AND matching.term=target_term
                  AND matching.number=candidate_number AND matching.phase=role
                  AND matching.generation IS NOT NULL
                  AND (role='projection' OR matching.generation=rev)
                GROUP BY ref, target_term, target_number, role, rev, candidate_number, is_alias
            )
            SELECT first_receipts.*, receipt.* FROM first_receipts
            LEFT JOIN result_applications receipt ON receipt.id=first_receipts.receipt_id
            """,
            (json.dumps(list(keys)),),
        ):
            owner = ProjectionOwner(row["target_term"], row["target_number"])
            aliases.setdefault(owner, ())
            candidate = ProjectionOwner(owner.term, row["candidate_number"])
            if row["is_alias"]:
                aliases[owner] = (candidate,)
            key = _ApplicationKey(
                row["ref"], candidate.term, candidate.number, row["role"], row["rev"]
            )
            applications[key] = row if row["id"] is not None else None
        return aliases, applications

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
        if self._scope is not None and owner in self._scope.aliases:
            return self._scope.aliases[owner]
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
        if self._scope is not None and result_id in self._scope.results:
            row = self._scope.results[result_id]
        else:
            rows = self._rows("SELECT * FROM computation_results WHERE result_id=?", (result_id,))
            row = rows[0] if rows else None
        if row is None:
            return None
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
            key = _ApplicationKey(
                result_id,
                owner.term,
                owner.number,
                "collection" if ready else "projection",
                result.generation if ready else None,
            )
            stored = self._application_row(key)
            if stored is not None:
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

    def _application_row(self, key: _ApplicationKey) -> sqlite3.Row | None:
        if self._scope is not None and key in self._scope.applications:
            return self._scope.applications[key]
        ready = key.phase == "collection"
        rows = self._rows(
            "SELECT * FROM result_applications WHERE result_id=? AND term=? AND number=?"
            " AND phase=? AND generation IS NOT NULL"
            + (" AND generation=?" if ready else "")
            + " ORDER BY id LIMIT 1",
            key if ready else key[:4],
        )
        return rows[0] if rows else None

    def materialize(
        self,
        column: str,
        raw: str,
        owner: ProjectionOwner | None,
        generation: int | None = None,
        *,
        owner_unlinked: bool = False,
    ) -> str:
        try:
            return self._materialize(column, raw, owner, generation, owner_unlinked)
        except PrimaryProjectionError:
            raise
        except (ValueError, TypeError, RecursionError, sqlite3.Error) as error:
            raise PrimaryProjectionError("invalid stored primary projection") from error

    def _materialize(
        self,
        column: str,
        raw: str,
        owner: ProjectionOwner | None,
        generation: int | None,
        owner_unlinked: bool,
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
            () if owner_unlinked else self._aliases(owner),
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
