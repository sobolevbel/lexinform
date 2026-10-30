"""Physical metadata readers share structural ownership, reference and byte-budget checks."""

import datetime as dt
import sqlite3
from collections.abc import Iterator
from typing import Literal

from lexinform.models.artifacts import ArtifactMetadata
from lexinform.models.extractions import ExtractionResult
from lexinform.models.physical_inventory import (
    MAX_PHYSICAL_ROOT_BYTES,
    MetadataKind,
    PhysicalCapture,
    PhysicalCaptureRecipe,
    PhysicalContainerExecution,
    PhysicalGraph,
    PhysicalHeaders,
    PhysicalMetadata,
    PhysicalRootOrder,
)

PHYSICAL_INVENTORY_MIGRATION = """
CREATE TABLE physical_metadata (
    metadata_id TEXT PRIMARY KEY NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('headers','graph','unit','execution','recipe')),
    body_sha256 TEXT,
    header_id TEXT REFERENCES physical_metadata(metadata_id) DEFERRABLE INITIALLY DEFERRED,
    coverage TEXT NOT NULL CHECK(coverage IN ('complete','partial')),
    recorded_at TEXT NOT NULL,
    record_json TEXT NOT NULL CHECK(json_valid(record_json) AND
      length(CAST(record_json AS BLOB)) <= 32768)
);
CREATE INDEX ix_physical_metadata_body ON physical_metadata(body_sha256,kind);
CREATE INDEX ix_physical_metadata_header ON physical_metadata(header_id);
CREATE TABLE physical_captures (
    capture_id TEXT PRIMARY KEY NOT NULL,
    artifact_sha256 TEXT NOT NULL REFERENCES artifacts(sha256),
    recipe_id TEXT NOT NULL REFERENCES physical_metadata(metadata_id),
    inventory_id TEXT NOT NULL REFERENCES physical_metadata(metadata_id),
    execution_id TEXT NOT NULL REFERENCES physical_metadata(metadata_id),
    extraction_id TEXT REFERENCES extraction_results(extraction_id),
    recorded_at TEXT NOT NULL,
    record_json TEXT NOT NULL CHECK(json_valid(record_json) AND
      length(CAST(record_json AS BLOB)) <= 1024)
);
CREATE UNIQUE INDEX ux_physical_capture_without_extraction ON physical_captures
  (artifact_sha256,recipe_id,inventory_id,execution_id) WHERE extraction_id IS NULL;
CREATE UNIQUE INDEX ux_physical_capture_with_extraction ON physical_captures
  (artifact_sha256,recipe_id,inventory_id,execution_id,extraction_id)
  WHERE extraction_id IS NOT NULL;
CREATE INDEX ix_physical_capture_artifact ON physical_captures(artifact_sha256);
CREATE INDEX ix_physical_capture_recipe ON physical_captures(recipe_id);
CREATE INDEX ix_physical_capture_inventory ON physical_captures(inventory_id);
CREATE INDEX ix_physical_capture_execution ON physical_captures(execution_id);
CREATE INDEX ix_physical_capture_extraction ON physical_captures(extraction_id);
CREATE VIEW physical_header_rows AS
  SELECT m.metadata_id AS header_id,m.body_sha256 AS parent_body_sha256,
    CAST(r.key AS INTEGER) AS ordinal,
    json_extract(r.value,'$[0]') AS original_name,
    COALESCE(json_extract(r.value,'$[1]'),json_extract(r.value,'$[0]')) AS runtime_name,
    json_extract(r.value,'$[2]') AS declared_bytes,
    json_extract(r.value,'$[3]') AS compressed_bytes,
    json_extract(r.value,'$[4]') AS method,json_extract(r.value,'$[5]') AS flags,
    json_extract(r.value,'$[6]') AS crc32
  FROM physical_metadata m,json_each(m.record_json,'$.metadata.rows') r
  WHERE m.kind='headers';
CREATE VIEW physical_graph_edges AS
  SELECT m.metadata_id AS parent_id,m.body_sha256 AS parent_body_sha256,m.header_id,
    CAST(e.key AS INTEGER) AS ordinal,
    json_extract(e.value,'$[0]') AS child_body_sha256,
    json_extract(e.value,'$[1]') AS child_id
  FROM physical_metadata m,json_each(m.record_json,'$.metadata.edges') e
  WHERE m.kind='graph';
CREATE VIEW physical_execution_units AS
  SELECT metadata_id AS unit_id,body_sha256,header_id,
    json_extract(record_json,'$.metadata.graph') AS graph_id
  FROM physical_metadata WHERE kind='unit';
CREATE VIEW physical_contexts AS
  SELECT x.metadata_id AS execution_id,CAST(c.key AS INTEGER) AS context_index,
    json_extract(c.value,'$[0]') AS parent_context,
    json_extract(c.value,'$[1]') AS parent_ordinal,
    json_extract(c.value,'$[2]') AS physical_index,
    json_extract(c.value,'$[3]') AS unit_id,
    json_extract(u.record_json,'$.metadata.graph') AS graph_id
  FROM physical_metadata x,json_each(x.record_json,'$.metadata.contexts') c
  LEFT JOIN physical_metadata u ON u.metadata_id=json_extract(c.value,'$[3]')
  WHERE x.kind='execution';
CREATE VIEW physical_requests AS
  WITH requests AS (
    SELECT x.metadata_id AS execution_id,CAST(s.key AS INTEGER) AS request_index,
      json_extract(s.value,'$[0]') AS context_index,
      json_extract(s.value,'$[1]') AS local_index,u.header_id,
      json_extract(u.record_json,'$.metadata.requests[' ||
        json_extract(s.value,'$[1]') || ']') AS request
    FROM physical_metadata x,json_each(x.record_json,'$.metadata.requests') s
    LEFT JOIN physical_contexts c ON c.execution_id=x.metadata_id
      AND c.context_index=json_extract(s.value,'$[0]')
    LEFT JOIN physical_metadata u ON u.metadata_id=c.unit_id
    WHERE x.kind='execution'
  ) SELECT execution_id,request_index,context_index,local_index,header_id,
    json_extract(request,'$[0]') AS mode,json_extract(request,'$[1]') AS request_form,
    json_extract(request,'$[2]') AS requested,
    json_extract(request,'$[3]') AS lookup_ordinal,
    json_extract(request,'$[4]') AS outcome,
    json_extract(request,'$[6]') AS known_body_sha256 FROM requests;
CREATE VIEW physical_opens AS
  WITH opens AS (
    SELECT x.metadata_id AS execution_id,CAST(s.key AS INTEGER) AS open_index,
      json_extract(s.value,'$[0]') AS context_index,
      json_extract(s.value,'$[1]') AS local_index,u.header_id,
      json_extract(u.record_json,'$.metadata.opens[' ||
        json_extract(s.value,'$[1]') || ']') AS opened
    FROM physical_metadata x,json_each(x.record_json,'$.metadata.opens') s
    LEFT JOIN physical_contexts c ON c.execution_id=x.metadata_id
      AND c.context_index=json_extract(s.value,'$[0]')
    LEFT JOIN physical_metadata u ON u.metadata_id=c.unit_id
    WHERE x.kind='execution'
  ) SELECT execution_id,open_index,context_index,local_index,header_id,
    json_extract(opened,'$[0]') AS local_request_index,
    json_extract(opened,'$[1]') AS request_form,json_extract(opened,'$[2]') AS requested,
    json_extract(opened,'$[3]') AS actual_read_ordinal,
    json_extract(opened,'$[4]') AS outcome FROM opens;
CREATE VIEW physical_parts AS
  SELECT x.metadata_id AS execution_id,CAST(p.key AS INTEGER) AS physical_index,
    json_extract(p.value,'$[0]') AS context_index,
    json_extract(p.value,'$[1]') AS text_sha256,
    json_extract(p.value,'$[2]') AS losses_sha256
  FROM physical_metadata x,json_each(x.record_json,'$.metadata.physical') p
  WHERE x.kind='execution';
CREATE VIEW physical_recipe_code AS
  SELECT m.metadata_id AS recipe_id,json_extract(c.value,'$.path') AS path,
    json_extract(c.value,'$.sha256') AS source_sha256
  FROM physical_metadata m,json_each(m.record_json,'$.metadata.code_files') c
  WHERE m.kind='recipe';
CREATE VIEW physical_metadata_refs AS
  SELECT metadata_id AS owner_id,header_id AS target_id,'header' AS relation
    FROM physical_metadata WHERE header_id IS NOT NULL
  UNION SELECT parent_id,child_id,'member' FROM physical_graph_edges
    WHERE child_id IS NOT NULL
  UNION SELECT unit_id,graph_id,'graph' FROM physical_execution_units
  UNION SELECT execution_id,unit_id,'context' FROM physical_contexts;
CREATE VIEW physical_capture_refs AS
  SELECT capture_id,recipe_id AS target_id,'recipe' AS relation FROM physical_captures
  UNION ALL SELECT capture_id,inventory_id,'inventory' FROM physical_captures
  UNION ALL SELECT capture_id,execution_id,'execution' FROM physical_captures;
"""


class PhysicalInventoryRowError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def physical_references(record: PhysicalMetadata) -> tuple[str, ...]:
    payload = record.metadata
    refs = [record.header_id] if record.header_id is not None else []
    if isinstance(payload, PhysicalGraph):
        refs.extend(child for _, child in payload.edges if child is not None)
    elif isinstance(payload, PhysicalContainerExecution):
        refs.append(payload.graph)
    elif isinstance(payload, PhysicalRootOrder):
        refs.extend(context[3] for context in payload.contexts)
    return tuple(dict.fromkeys(refs))


def _metadata_row(row: sqlite3.Row) -> PhysicalMetadata:
    try:
        record = PhysicalMetadata.model_validate_json(row["record_json"])
        if record.recorded_at is None:
            raise ValueError("persisted physical metadata requires recording time")
        expected = (
            record.metadata_id,
            record.kind,
            record.body_sha256,
            record.header_id,
            record.coverage,
            record.recorded_at.isoformat(),
        )
        if expected != tuple(
            row[key]
            for key in (
                "metadata_id",
                "kind",
                "body_sha256",
                "header_id",
                "coverage",
                "recorded_at",
            )
        ):
            raise ValueError("physical metadata columns differ from their immutable envelope")
        return record
    except (ValueError, TypeError, KeyError) as exc:
        raise PhysicalInventoryRowError("invalid_physical_metadata") from exc


def _capture_row(row: sqlite3.Row) -> PhysicalCapture:
    try:
        record = PhysicalCapture.model_validate_json(row["record_json"])
        if record.recorded_at is None:
            raise ValueError("persisted physical capture requires recording time")
        expected = (
            record.capture_id,
            record.artifact_sha256,
            record.recipe_id,
            record.inventory_id,
            record.execution_id,
            record.extraction_id,
            record.recorded_at.isoformat(),
        )
        if expected != tuple(
            row[key]
            for key in (
                "capture_id",
                "artifact_sha256",
                "recipe_id",
                "inventory_id",
                "execution_id",
                "extraction_id",
                "recorded_at",
            )
        ):
            raise ValueError("physical capture columns differ from their immutable envelope")
        return record
    except (ValueError, TypeError, KeyError) as exc:
        raise PhysicalInventoryRowError("invalid_physical_capture") from exc


class PhysicalInventoryReader:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.records: dict[str, PhysicalMetadata] = {}
        self.validated: set[str] = set()
        expected: dict[str, str] = {}
        for statement in PHYSICAL_INVENTORY_MIGRATION.split(";"):
            tokens = statement.split()
            if tokens:
                index = 3 if tokens[1] == "UNIQUE" else 2
                expected[tokens[index]] = " ".join(tokens)
        actual = {
            row["name"]: " ".join(row["sql"].split())
            for row in connection.execute(
                "SELECT name,sql FROM sqlite_master WHERE sql IS NOT NULL"
            )
            if row["name"] in expected
        }
        if actual != expected:
            raise PhysicalInventoryRowError("physical_inventory_schema_mismatch")

    def read(self, metadata_id: str) -> PhysicalMetadata:
        stack = [(metadata_id, False)]
        active: set[str] = set()
        while stack:
            identity, finishing = stack.pop()
            if identity in self.validated:
                continue
            if finishing:
                self._validate(self.records[identity])
                active.remove(identity)
                self.validated.add(identity)
                continue
            if identity in active:
                raise PhysicalInventoryRowError("physical_reference_cycle")
            if identity not in self.records:
                row = self.connection.execute(
                    "SELECT * FROM physical_metadata WHERE metadata_id=?", (identity,)
                ).fetchone()
                if row is None:
                    raise PhysicalInventoryRowError("physical_reference_missing")
                self.records[identity] = _metadata_row(row)
            active.add(identity)
            stack.append((identity, True))
            stack.extend(
                (ref, False) for ref in reversed(physical_references(self.records[identity]))
            )
        return self.records[metadata_id]

    def typed(self, metadata_id: str, kind: MetadataKind) -> PhysicalMetadata:
        record = self.records[metadata_id]
        if record.kind != kind:
            raise PhysicalInventoryRowError("physical_reference_kind")
        return record

    def _graph(self, identity: str) -> tuple[PhysicalMetadata, PhysicalGraph]:
        record = self.typed(identity, "graph")
        if not isinstance(record.metadata, PhysicalGraph):
            raise PhysicalInventoryRowError("physical_reference_kind")
        return record, record.metadata

    def _unit(self, identity: str) -> tuple[PhysicalMetadata, PhysicalContainerExecution]:
        record = self.typed(identity, "unit")
        if not isinstance(record.metadata, PhysicalContainerExecution):
            raise PhysicalInventoryRowError("physical_reference_kind")
        return record, record.metadata

    def _known_body_size(self, identity: str) -> int | None:
        artifact = self.connection.execute(
            "SELECT * FROM artifacts WHERE sha256=?", (identity,)
        ).fetchone()
        if artifact is None:
            return None
        try:
            if type(artifact["byte_size"]) is not int:
                raise ValueError("known complete bodies require integer byte sizes")
            return ArtifactMetadata(
                sha256=artifact["sha256"],
                byte_size=artifact["byte_size"],
                recorded_at=dt.datetime.fromisoformat(artifact["recorded_at"]),
            ).byte_size
        except (ValueError, TypeError) as exc:
            raise PhysicalInventoryRowError("physical_capture_artifact_invalid") from exc

    def _validate(self, record: PhysicalMetadata) -> None:
        try:
            self._relationships(record)
        except (ValueError, IndexError, KeyError) as exc:
            if isinstance(exc, PhysicalInventoryRowError):
                raise
            raise PhysicalInventoryRowError("physical_parent_mismatch") from exc

    def _relationships(self, record: PhysicalMetadata) -> None:
        payload = record.metadata
        if isinstance(payload, PhysicalGraph):
            if payload.header is None:
                if record.total("edges", len(payload.edges)):
                    raise ValueError("unobserved headers cannot claim member occurrences")
            else:
                header = self.typed(payload.header, "headers")
                if not isinstance(header.metadata, PhysicalHeaders):
                    raise ValueError("graph header has the wrong kind")
                if header.body_sha256 != record.body_sha256:
                    raise ValueError("graph and header must identify the same complete body")
                if record.total("edges", len(payload.edges)) != header.total(
                    "rows", len(header.metadata.rows)
                ):
                    raise ValueError("graph edges must retain the observed header universe")
                known_size = self._known_body_size(payload.body_sha)
                if (
                    known_size is not None
                    and header.metadata.central_directory_span[1] > known_size
                ):
                    raise ValueError("container header exceeds its known complete body size")
            for body, child_id in payload.edges:
                if child_id is not None and self._graph(child_id)[0].body_sha256 != body:
                    raise ValueError("child graph differs from its known member body")
            if payload.header is not None:
                parent_header = self.typed(payload.header, "headers").metadata
                if isinstance(parent_header, PhysicalHeaders):
                    for index, (body, child_id) in enumerate(payload.edges):
                        if index >= len(parent_header.rows):
                            continue
                        known_size = self._known_body_size(body) if body is not None else None
                        if known_size is not None and known_size != parent_header.rows[index][2]:
                            raise ValueError(
                                "member header differs from its known complete body size"
                            )
                        if child_id is None:
                            continue
                        child_graph = self._graph(child_id)[1]
                        if child_graph.header is not None:
                            child_header = self.typed(child_graph.header, "headers").metadata
                            if (
                                isinstance(child_header, PhysicalHeaders)
                                and child_header.central_directory_span[1]
                                > parent_header.rows[index][2]
                            ):
                                raise ValueError(
                                    "nested header exceeds its known complete member size"
                                )
        elif isinstance(payload, PhysicalContainerExecution):
            self._validate_unit(record, payload)
        elif isinstance(payload, PhysicalRootOrder):
            self._validate_root(record, payload)
        elif isinstance(payload, PhysicalCaptureRecipe):
            if record.header_id is not None:
                raise ValueError("recipes have no artifact header")

    def _validate_unit(self, record: PhysicalMetadata, unit: PhysicalContainerExecution) -> None:
        graph_record, graph = self._graph(unit.graph)
        if (record.body_sha256, record.header_id) != (graph_record.body_sha256, graph.header):
            raise ValueError("container execution must identify its exact graph/body/header")
        total = graph_record.total("edges", len(graph.edges))
        if record.total("statuses", len(unit.statuses)) != total:
            raise ValueError("container dispositions must retain every observed header")
        for index, request in enumerate(unit.requests):
            _, form, requested, lookup, outcome, _, body, _ = request
            if lookup is not None and lookup >= total:
                raise ValueError("lookup ordinal is outside its actual header universe")
            if form == "ordinal" and isinstance(requested, int) and requested >= total:
                raise ValueError("enumerated ordinal is outside its actual header universe")
            opened = [item[3] for item in unit.opens if item[0] == index and item[4] == "opened"]
            targets = opened or ([lookup] if lookup is not None else [])
            if outcome == "complete":
                assert body is not None, "the typed complete request requires its full body SHA"
                known_size = self._known_body_size(body)
                if known_size is not None and known_size != request[5]:
                    raise ValueError("complete read differs from its known complete body size")
                if not targets:
                    raise ValueError("known member bodies require a recorded occurrence")
                for target in targets:
                    if (
                        target is not None
                        and target < len(graph.edges)
                        and graph.edges[target][0] != body
                    ):
                        raise ValueError("completed read differs from its actual member body")
                    if target is not None and graph.header is not None:
                        header = self.typed(graph.header, "headers").metadata
                        if (
                            isinstance(header, PhysicalHeaders)
                            and target < len(header.rows)
                            and request[5] != header.rows[target][2]
                        ):
                            raise ValueError("complete read differs from its observed member size")
        for local_request, _, _, resolved, _, _ in unit.opens:
            if resolved is not None and resolved >= total:
                raise ValueError("actual read ordinal is outside its header universe")
            if local_request is not None and local_request >= record.total(
                "requests", len(unit.requests)
            ):
                raise ValueError("opened payload refers to an unavailable local request")

    def _validate_root(self, record: PhysicalMetadata, root: PhysicalRootOrder) -> None:
        contexts = root.contexts
        total_contexts = record.total("contexts", len(contexts))
        if contexts and (contexts[0][0] is not None or contexts[0][1] is not None):
            raise ValueError("root context cannot borrow a parent occurrence")
        units: list[tuple[PhysicalMetadata, PhysicalContainerExecution]] = []
        for index, (parent, ordinal, _, unit_id) in enumerate(contexts):
            unit_record, unit = self._unit(unit_id)
            units.append((unit_record, unit))
            if index == 0:
                if unit_record.body_sha256 != record.body_sha256:
                    raise ValueError("root order differs from its exact input body")
            else:
                if parent is None or ordinal is None or parent >= index:
                    raise ValueError("context parent must be an earlier actual occurrence")
                parent_record, parent_graph = self._graph(units[parent][1].graph)
                if ordinal >= parent_record.total("edges", len(parent_graph.edges)):
                    raise ValueError("context parent ordinal is outside its observed universe")
                if ordinal < len(parent_graph.edges):
                    body, child = parent_graph.edges[ordinal]
                    if (body, child) != (unit_record.body_sha256, unit.graph):
                        raise ValueError("context differs from its immediate physical parent")
        self._validate_order(record, root.requests, units, "requests")
        self._validate_order(record, root.opens, units, "opens")
        for index, (owner, _, _) in enumerate(root.physical):
            if owner >= total_contexts:
                raise ValueError("physical part refers to an unknown context")
            if owner < len(contexts) and contexts[owner][2] != index:
                raise ValueError("physical index differs from its original context")
        seen = [context[2] for context in contexts if context[2] is not None]
        if len(seen) != len(set(seen)):
            raise ValueError("a physical index belongs to exactly one occurrence")
        for _, _, physical, _ in contexts:
            if physical is not None and physical >= record.total("physical", len(root.physical)):
                raise ValueError("context physical index is outside its observed universe")
        for owner, _ in root.zip_failures:
            if owner >= total_contexts:
                raise ValueError("ZIP failure refers to an unknown context")

    def _validate_order(
        self,
        record: PhysicalMetadata,
        slots: tuple[tuple[int, int], ...],
        units: list[tuple[PhysicalMetadata, PhysicalContainerExecution]],
        field: Literal["requests", "opens"],
    ) -> None:
        if len(slots) != len(set(slots)):
            raise ValueError("execution order cannot duplicate a local event")
        for owner, local in slots:
            if owner >= record.total("contexts", len(units)):
                raise ValueError("execution event owner is outside observed contexts")
            if owner < len(units):
                unit_record, unit = units[owner]
                retained = len(unit.requests) if field == "requests" else len(unit.opens)
                if local >= unit_record.total(field, retained):
                    raise ValueError("execution order refers to an unknown local event")
        if not record.omitted("contexts"):
            expected = sum(
                unit_record.total(
                    field, len(unit.requests) if field == "requests" else len(unit.opens)
                )
                for unit_record, unit in units
            )
            if record.total(field, len(slots)) != expected:
                raise ValueError("execution order does not retain every observed local event")

    def capture(self, row: sqlite3.Row) -> PhysicalCapture:
        record = _capture_row(row)
        return self.verify_capture(record)

    def verify_capture(self, record: PhysicalCapture) -> PhysicalCapture:
        recipe = self.read(record.recipe_id)
        inventory = self.read(record.inventory_id)
        execution = self.read(record.execution_id)
        self.typed(recipe.metadata_id, "recipe")
        self.typed(inventory.metadata_id, "graph")
        self.typed(execution.metadata_id, "execution")
        if (inventory.body_sha256, execution.body_sha256) != (
            record.artifact_sha256,
            record.artifact_sha256,
        ):
            raise PhysicalInventoryRowError("physical_capture_owner_mismatch")
        artifact = self.connection.execute(
            "SELECT * FROM artifacts WHERE sha256=?", (record.artifact_sha256,)
        ).fetchone()
        if artifact is None:
            raise PhysicalInventoryRowError("physical_capture_artifact_missing")
        try:
            if not isinstance(artifact["byte_size"], int):
                raise ValueError("physical artifact parents require an integer byte size")
            parent_artifact = ArtifactMetadata(
                sha256=artifact["sha256"],
                byte_size=artifact["byte_size"],
                recorded_at=dt.datetime.fromisoformat(artifact["recorded_at"]),
            )
        except (ValueError, TypeError) as exc:
            raise PhysicalInventoryRowError("physical_capture_artifact_invalid") from exc
        if isinstance(inventory.metadata, PhysicalGraph) and inventory.metadata.header is not None:
            header = self.typed(inventory.metadata.header, "headers").metadata
            if (
                isinstance(header, PhysicalHeaders)
                and header.central_directory_span[1] > parent_artifact.byte_size
            ):
                raise PhysicalInventoryRowError("physical_capture_owner_mismatch")
        if isinstance(execution.metadata, PhysicalRootOrder) and execution.metadata.contexts:
            unit = self._unit(execution.metadata.contexts[0][3])[1]
            if unit.graph != inventory.metadata_id:
                raise PhysicalInventoryRowError("physical_capture_owner_mismatch")
        if record.extraction_id is not None:
            parent = self.connection.execute(
                "SELECT * FROM extraction_results WHERE extraction_id=?", (record.extraction_id,)
            ).fetchone()
            if parent is None:
                raise PhysicalInventoryRowError("physical_capture_extraction_missing")
            try:
                extraction = ExtractionResult.model_validate_json(parent["result_json"])
                if (
                    extraction.result_id,
                    extraction.artifact_sha256,
                    parent["artifact_sha256"],
                ) != (record.extraction_id, record.artifact_sha256, record.artifact_sha256):
                    raise ValueError(
                        "physical capture extraction has a different actual artifact owner"
                    )
            except (ValueError, TypeError) as exc:
                raise PhysicalInventoryRowError("physical_capture_extraction_mismatch") from exc
        reachable = self.reachable((record.recipe_id, record.inventory_id, record.execution_id))
        if (
            record.budget_bytes + sum(self.records[item].budget_bytes for item in reachable)
            > MAX_PHYSICAL_ROOT_BYTES
        ):
            raise PhysicalInventoryRowError("physical_root_over_budget")
        return record

    def reachable(self, roots: tuple[str, ...]) -> set[str]:
        pending = list(roots)
        seen: set[str] = set()
        while pending:
            identity = pending.pop()
            if identity not in seen:
                seen.add(identity)
                pending.extend(physical_references(self.read(identity)))
        return seen


def physical_inventory_issues(connection: sqlite3.Connection) -> Iterator[tuple[str, str]]:
    try:
        reader = PhysicalInventoryReader(connection)
    except PhysicalInventoryRowError as exc:
        yield exc.code, "physical_inventory"
        return
    for row in connection.execute("SELECT * FROM physical_metadata ORDER BY metadata_id"):
        try:
            reader.read(row["metadata_id"])
        except PhysicalInventoryRowError as exc:
            yield exc.code, "physical_metadata/" + row["metadata_id"]
    for row in connection.execute("SELECT * FROM physical_captures ORDER BY capture_id"):
        try:
            reader.capture(row)
        except PhysicalInventoryRowError as exc:
            yield exc.code, "physical_captures/" + row["capture_id"]


def validate_physical_inventory(connection: sqlite3.Connection) -> None:
    old_factory = connection.row_factory
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        required = {"physical_metadata", "physical_captures"}
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if not required.intersection(tables) and version < 62:
            return
        if not required <= tables:
            raise PhysicalInventoryRowError("physical_inventory_table_missing")
        for code, _ in physical_inventory_issues(connection):
            raise PhysicalInventoryRowError(code)
    finally:
        connection.row_factory = old_factory


def physical_recorded_at(value: dt.datetime) -> str:
    if value.utcoffset() is None:
        raise ValueError("physical recording time requires a timezone")
    return value.astimezone(dt.UTC).isoformat()
