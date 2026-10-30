"""Omission readers validate addressable owner evidence and known physical links without IO."""

import datetime as dt
import sqlite3
from collections.abc import Iterator

from lexinform.models.artifacts import (
    ArtifactMetadata,
    ArtifactReadReceipt,
    SelectionArtifactReference,
)
from lexinform.models.document_history import DocumentSelectionRecord, SourceObservation
from lexinform.models.extractions import ExtractionResult
from lexinform.models.source_omissions import (
    BillOmissionOwner,
    RclOmissionOwner,
    SenateOmissionOwner,
    SourceMetadataOmission,
)

SOURCE_OMISSION_MIGRATION = """
CREATE TABLE source_metadata_omissions (
    omission_id TEXT PRIMARY KEY NOT NULL,
    owner_kind TEXT NOT NULL CHECK(owner_kind IN ('bill','rcl_letter','senate')),
    owner_term INTEGER,
    owner_number TEXT,
    purpose TEXT,
    project_id INTEGER,
    stage_id INTEGER,
    stage_number INTEGER,
    letter_id INTEGER,
    resolution_term INTEGER,
    resolution_number TEXT,
    source_view_sha256 TEXT NOT NULL,
    parent_count INTEGER NOT NULL CHECK(parent_count>=0),
    parents_sha256 TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    record_json TEXT NOT NULL CHECK(json_valid(record_json) AND
      length(CAST(record_json AS BLOB))<=8192),
    FOREIGN KEY(owner_term,owner_number) REFERENCES bills(term,number)
);
CREATE INDEX ix_source_omissions_owner
  ON source_metadata_omissions(owner_term,owner_number,purpose);
CREATE INDEX ix_source_omissions_rcl ON source_metadata_omissions(project_id,stage_id,letter_id);
CREATE INDEX ix_source_omissions_senate
  ON source_metadata_omissions(resolution_term,resolution_number);
CREATE TABLE source_omission_parents (
    omission_id TEXT NOT NULL REFERENCES source_metadata_omissions(omission_id),
    ordinal INTEGER NOT NULL CHECK(ordinal>=0 AND ordinal<32),
    artifact_sha256 TEXT NOT NULL REFERENCES artifacts(sha256),
    artifact_read_id TEXT REFERENCES artifact_reads(read_id),
    extraction_id TEXT REFERENCES extraction_results(extraction_id),
    source_observation_id TEXT REFERENCES source_observations(observation_id),
    PRIMARY KEY(omission_id,ordinal)
);
CREATE INDEX ix_source_omission_artifact ON source_omission_parents(artifact_sha256);
CREATE INDEX ix_source_omission_read ON source_omission_parents(artifact_read_id);
CREATE INDEX ix_source_omission_extraction ON source_omission_parents(extraction_id);
CREATE INDEX ix_source_omission_source ON source_omission_parents(source_observation_id);
"""
SOURCE_OMISSION_COLUMNS = (
    "omission_id",
    "owner_kind",
    "owner_term",
    "owner_number",
    "purpose",
    "project_id",
    "stage_id",
    "stage_number",
    "letter_id",
    "resolution_term",
    "resolution_number",
    "source_view_sha256",
    "parent_count",
    "parents_sha256",
    "recorded_at",
)


class SourceOmissionRowError(ValueError):
    pass


def omission_values(record: SourceMetadataOmission) -> tuple[object, ...]:
    owner = record.owner
    bill = not isinstance(owner, RclOmissionOwner)
    rcl = owner if isinstance(owner, RclOmissionOwner) else None
    senate = owner if owner.kind == "senate" else None
    return (
        record.omission_id,
        owner.kind,
        owner.term if bill and not isinstance(owner, RclOmissionOwner) else None,
        owner.number if bill and not isinstance(owner, RclOmissionOwner) else None,
        owner.purpose if isinstance(owner, BillOmissionOwner) else None,
        rcl.project_id if rcl else None,
        rcl.stage_id if rcl else None,
        rcl.stage_number if rcl else None,
        rcl.letter_id if rcl else None,
        senate.resolution_term if senate else None,
        senate.resolution_number if senate else None,
        record.source_view_sha256,
        record.parent_count,
        record.parents_sha256,
        record.recorded_at.isoformat() if record.recorded_at else None,
    )


def _selection_bodies(
    connection: sqlite3.Connection, selection: DocumentSelectionRecord
) -> set[str]:
    bodies: set[str] = set()
    for row in connection.execute(
        "SELECT * FROM selection_artifacts WHERE selection_id=?", (selection.selection_id,)
    ):
        link = SelectionArtifactReference.model_validate(dict(row))
        parts = (
            selection.selection.parts
            if link.part_scope == "parts"
            else selection.selection.observed_parts
        )
        if (
            link.part_index >= len(parts)
            or parts[link.part_index].source_sha256 != link.artifact_sha256
        ):
            raise ValueError("selection artifact link differs from its actual retained part")
        bodies.add(link.artifact_sha256)
    sources = {
        part.source_sha256
        for part in (*selection.selection.parts, *selection.selection.observed_parts)
    }
    for link in connection.execute(
        "SELECT * FROM selection_extractions WHERE selection_id=?", (selection.selection_id,)
    ):
        row = connection.execute(
            "SELECT * FROM extraction_results WHERE extraction_id=?", (link["extraction_id"],)
        ).fetchone()
        if row is None:
            raise ValueError("selection extraction parent is missing")
        result = ExtractionResult.model_validate_json(row["result_json"])
        if (result.result_id, result.artifact_sha256) != (
            link["extraction_id"],
            row["artifact_sha256"],
        ) or result.artifact_sha256 not in sources:
            raise ValueError("selection extraction link differs from its actual retained body")
        bodies.add(result.artifact_sha256)
    return bodies


def read_source_omission(
    connection: sqlite3.Connection, row: sqlite3.Row
) -> SourceMetadataOmission:
    try:
        record = SourceMetadataOmission.model_validate_json(row["record_json"])
        if record.recorded_at is None or omission_values(record) != tuple(
            row[key] for key in SOURCE_OMISSION_COLUMNS
        ):
            raise ValueError("source omission indexed columns differ from its immutable record")
        if (
            not isinstance(record.owner, RclOmissionOwner)
            and connection.execute(
                "SELECT 1 FROM bills WHERE term=? AND number=?",
                (record.owner.term, record.owner.number),
            ).fetchone()
            is None
        ):
            raise ValueError("source omission bill owner is missing")
        rows = connection.execute(
            "SELECT * FROM source_omission_parents WHERE omission_id=? ORDER BY ordinal",
            (record.omission_id,),
        ).fetchall()
        expected = [
            (
                index,
                item.artifact_sha256,
                item.artifact_read_id,
                item.extraction_id,
                item.source_observation_id,
            )
            for index, item in enumerate(record.parents)
        ]
        if expected != [
            tuple(
                item[key]
                for key in (
                    "ordinal",
                    "artifact_sha256",
                    "artifact_read_id",
                    "extraction_id",
                    "source_observation_id",
                )
            )
            for item in rows
        ]:
            raise ValueError("source omission SQL links differ from its retained parent manifest")
        for parent in record.parents:
            artifact = connection.execute(
                "SELECT * FROM artifacts WHERE sha256=?", (parent.artifact_sha256,)
            ).fetchone()
            if artifact is None or type(artifact["byte_size"]) is not int:
                raise ValueError("source omission complete-body parent is unavailable")
            metadata = ArtifactMetadata(
                sha256=artifact["sha256"],
                byte_size=artifact["byte_size"],
                recorded_at=dt.datetime.fromisoformat(artifact["recorded_at"]),
            )
            if parent.artifact_read_id is not None:
                read = connection.execute(
                    "SELECT * FROM artifact_reads WHERE read_id=?", (parent.artifact_read_id,)
                ).fetchone()
                if read is None or type(read["byte_size"]) is not int:
                    raise ValueError("source omission read parent is missing")
                receipt = ArtifactReadReceipt(
                    sha256=read["artifact_sha256"],
                    byte_size=read["byte_size"],
                    requested_url=read["requested_url"],
                    recorded_at=dt.datetime.fromisoformat(read["recorded_at"]),
                )
                if (receipt.read_id, receipt.sha256, receipt.byte_size) != (
                    parent.artifact_read_id,
                    parent.artifact_sha256,
                    metadata.byte_size,
                ):
                    raise ValueError("source omission read parent belongs to another body")
            if parent.extraction_id is not None:
                extraction = connection.execute(
                    "SELECT * FROM extraction_results WHERE extraction_id=?",
                    (parent.extraction_id,),
                ).fetchone()
                if extraction is None:
                    raise ValueError("source omission extraction parent is missing")
                result = ExtractionResult.model_validate_json(extraction["result_json"])
                if (result.result_id, result.artifact_sha256, extraction["artifact_sha256"]) != (
                    parent.extraction_id,
                    parent.artifact_sha256,
                    parent.artifact_sha256,
                ):
                    raise ValueError("source omission extraction parent belongs to another body")
            if parent.source_observation_id is not None:
                source = connection.execute(
                    "SELECT * FROM source_observations WHERE observation_id=?",
                    (parent.source_observation_id,),
                ).fetchone()
                if source is None:
                    raise ValueError("source omission observation parent is missing")
                observation = SourceObservation.model_validate_json(source["observation_json"])
                if (
                    observation.observation_id,
                    observation.term,
                    observation.number,
                    observation.purpose,
                    observation.selection_id,
                    observation.document.url,
                    observation.document.kind,
                ) != (
                    parent.source_observation_id,
                    source["term"],
                    source["number"],
                    source["purpose"],
                    source["selection_id"],
                    source["source_url"],
                    source["source_kind"],
                ):
                    raise ValueError("source omission observation identity differs from saved row")
                if isinstance(record.owner, RclOmissionOwner):
                    raise ValueError("RCL context has no verified Bill observation owner")
                if (
                    observation.term,
                    observation.number,
                ) != (record.owner.term, record.owner.number):
                    raise ValueError("source omission observation belongs to another bill")
                if (
                    isinstance(record.owner, BillOmissionOwner)
                    and observation.purpose != record.owner.purpose
                ):
                    raise ValueError("source omission observation has another purpose")
                if isinstance(record.owner, SenateOmissionOwner) and (
                    observation.purpose != "analysis"
                    or observation.document.kind != "senate_amendments"
                ):
                    raise ValueError("Senate source requires actual analysis resolution role")
                selection_row = connection.execute(
                    "SELECT record_json FROM document_selections WHERE selection_id=?",
                    (observation.selection_id,),
                ).fetchone()
                if selection_row is None:
                    raise ValueError("source omission selection parent is missing")
                selection = DocumentSelectionRecord.model_validate_json(
                    selection_row["record_json"]
                )
                if selection.selection_id != observation.selection_id:
                    raise ValueError("source omission selection identity is inconsistent")
                if parent.artifact_sha256 not in _selection_bodies(connection, selection):
                    raise ValueError(
                        "source omission body has no actual retained selection relation"
                    )
        return record
    except (ValueError, TypeError, KeyError, IndexError, OverflowError, sqlite3.Error) as exc:
        raise SourceOmissionRowError("invalid_source_metadata_omission") from exc


def source_omission_issues(connection: sqlite3.Connection) -> Iterator[tuple[str, str]]:
    expected = {}
    for statement in SOURCE_OMISSION_MIGRATION.split(";"):
        tokens = statement.split()
        if tokens:
            expected[tokens[2]] = " ".join(tokens)
    actual = {
        row["name"]: " ".join(row["sql"].split())
        for row in connection.execute("SELECT name,sql FROM sqlite_master WHERE sql IS NOT NULL")
        if row["name"] in expected
    }
    if actual != expected:
        yield "source_omission_schema_mismatch", "source_metadata_omissions"
        return
    for row in connection.execute("SELECT * FROM source_metadata_omissions ORDER BY omission_id"):
        try:
            read_source_omission(connection, row)
        except SourceOmissionRowError as exc:
            yield str(exc), "source_metadata_omissions/" + row["omission_id"]
    for row in connection.execute(
        "SELECT p.omission_id FROM source_omission_parents p LEFT JOIN source_metadata_omissions o"
        " USING(omission_id) WHERE o.omission_id IS NULL"
    ):
        yield "orphan_source_omission_parent", "source_omission_parents/" + row["omission_id"]


def validate_source_omissions(connection: sqlite3.Connection) -> None:
    previous = connection.row_factory
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        required = {"source_metadata_omissions", "source_omission_parents"}
        if (
            not required.intersection(tables)
            and connection.execute("PRAGMA user_version").fetchone()[0] < 63
        ):
            return
        if not required <= tables:
            raise SourceOmissionRowError("source_omission_table_missing")
        for code, _ in source_omission_issues(connection):
            raise SourceOmissionRowError(code)
    finally:
        connection.row_factory = previous
