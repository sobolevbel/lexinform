"""Catalog attempt readers validate immutable owner and physical evidence without source IO."""

import datetime as dt
import json
import sqlite3
from collections.abc import Iterator

from lexinform.models.artifacts import ArtifactMetadata, ArtifactReadReceipt
from lexinform.models.extractions import ExtractionResult
from lexinform.models.source_catalog_attempts import SourceCatalogAttempt, SourceCatalogSnapshot
from lexinform.models.source_omissions import BillOmissionOwner, source_omission_canonical

SOURCE_CATALOG_MIGRATION = """
CREATE TABLE source_catalog_snapshots (
    catalog_id TEXT PRIMARY KEY NOT NULL,
    source_view_sha256 TEXT NOT NULL,
    descriptor_count INTEGER NOT NULL CHECK(descriptor_count>0),
    coverage TEXT NOT NULL CHECK(coverage IN ('complete','partial')),
    recorded_at TEXT NOT NULL,
    record_json TEXT NOT NULL CHECK(json_valid(record_json) AND
      length(CAST(record_json AS BLOB))<=8192)
);
CREATE INDEX ix_source_catalog_view ON source_catalog_snapshots(source_view_sha256);
CREATE TABLE source_catalog_attempts (
    attempt_id TEXT PRIMARY KEY NOT NULL,
    catalog_id TEXT NOT NULL REFERENCES source_catalog_snapshots(catalog_id),
    owner_kind TEXT NOT NULL CHECK(owner_kind IN ('bill','source_reference')),
    owner_term INTEGER,
    owner_number TEXT,
    source_term INTEGER,
    source_number TEXT,
    purpose TEXT NOT NULL CHECK(purpose IN ('prefilter','analysis','review')),
    source_key TEXT,
    status TEXT NOT NULL CHECK(status='read_failed'),
    error_class TEXT NOT NULL,
    parent_count INTEGER NOT NULL CHECK(parent_count>=0),
    parents_sha256 TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    record_json TEXT NOT NULL CHECK(json_valid(record_json) AND
      length(CAST(record_json AS BLOB))<=8192),
    FOREIGN KEY(owner_term,owner_number) REFERENCES bills(term,number)
);
CREATE INDEX ix_source_attempt_owner ON source_catalog_attempts(owner_term,owner_number,purpose);
CREATE INDEX ix_source_attempt_reference
  ON source_catalog_attempts(source_term,source_number,purpose);
CREATE INDEX ix_source_attempt_key ON source_catalog_attempts(source_key);
CREATE INDEX ix_source_attempt_catalog ON source_catalog_attempts(catalog_id);
CREATE TABLE source_catalog_attempt_parents (
    attempt_id TEXT NOT NULL REFERENCES source_catalog_attempts(attempt_id),
    ordinal INTEGER NOT NULL CHECK(ordinal>=0 AND ordinal<32),
    artifact_sha256 TEXT NOT NULL REFERENCES artifacts(sha256),
    artifact_read_id TEXT REFERENCES artifact_reads(read_id),
    extraction_id TEXT REFERENCES extraction_results(extraction_id),
    PRIMARY KEY(attempt_id,ordinal)
);
CREATE INDEX ix_source_attempt_artifact ON source_catalog_attempt_parents(artifact_sha256);
CREATE INDEX ix_source_attempt_read ON source_catalog_attempt_parents(artifact_read_id);
CREATE INDEX ix_source_attempt_extraction ON source_catalog_attempt_parents(extraction_id);
"""
CATALOG_COLUMNS = (
    "catalog_id",
    "source_view_sha256",
    "descriptor_count",
    "coverage",
    "recorded_at",
)
ATTEMPT_COLUMNS = (
    "attempt_id",
    "catalog_id",
    "owner_kind",
    "owner_term",
    "owner_number",
    "source_term",
    "source_number",
    "purpose",
    "source_key",
    "status",
    "error_class",
    "parent_count",
    "parents_sha256",
    "recorded_at",
)
TABLES = {"source_catalog_snapshots", "source_catalog_attempts", "source_catalog_attempt_parents"}


class SourceCatalogRowError(ValueError):
    pass


def catalog_values(record: SourceCatalogSnapshot) -> tuple[object, ...]:
    return (
        record.catalog_id,
        record.source_view_sha256,
        record.descriptor_count,
        record.coverage,
        record.recorded_at.isoformat() if record.recorded_at else None,
    )


def attempt_values(record: SourceCatalogAttempt) -> tuple[object, ...]:
    owner = record.owner
    tracked = isinstance(owner, BillOmissionOwner)
    return (
        record.attempt_id,
        record.catalog_id,
        owner.kind,
        owner.term if tracked else None,
        owner.number if tracked else None,
        None if tracked else owner.term,
        None if tracked else owner.number,
        owner.purpose,
        None if isinstance(owner, BillOmissionOwner) else owner.source_key,
        record.status,
        record.error_class,
        record.parent_count,
        record.parents_sha256,
        record.recorded_at.isoformat() if record.recorded_at else None,
    )


def read_catalog(row: sqlite3.Row) -> SourceCatalogSnapshot:
    try:
        record = SourceCatalogSnapshot.model_validate_json(row["record_json"])
        if source_omission_canonical(json.loads(row["record_json"])) != source_omission_canonical(
            record.model_dump(mode="json")
        ):
            raise ValueError("catalog snapshot JSON contains unsupported fields or values")
        if record.recorded_at is None or catalog_values(record) != tuple(
            row[c] for c in CATALOG_COLUMNS
        ):
            raise ValueError("catalog indexed columns differ from its immutable snapshot")
        return record
    except (ValueError, TypeError, KeyError, IndexError, OverflowError) as exc:
        raise SourceCatalogRowError("invalid_source_catalog_snapshot") from exc


def read_attempt(connection: sqlite3.Connection, row: sqlite3.Row) -> SourceCatalogAttempt:
    try:
        record = SourceCatalogAttempt.model_validate_json(row["record_json"])
        if source_omission_canonical(json.loads(row["record_json"])) != source_omission_canonical(
            record.model_dump(mode="json")
        ):
            raise ValueError("catalog attempt JSON contains unsupported fields or values")
        if record.recorded_at is None or attempt_values(record) != tuple(
            row[c] for c in ATTEMPT_COLUMNS
        ):
            raise ValueError("attempt indexed columns differ from its immutable evidence")
        catalog = connection.execute(
            "SELECT * FROM source_catalog_snapshots WHERE catalog_id=?", (record.catalog_id,)
        ).fetchone()
        if catalog is None:
            raise ValueError("catalog attempt snapshot is missing")
        read_catalog(catalog)
        if (
            isinstance(record.owner, BillOmissionOwner)
            and connection.execute(
                "SELECT 1 FROM bills WHERE term=? AND number=?",
                (record.owner.term, record.owner.number),
            ).fetchone()
            is None
        ):
            raise ValueError("catalog attempt tracked owner is missing")
        links = connection.execute(
            "SELECT * FROM source_catalog_attempt_parents WHERE attempt_id=? ORDER BY ordinal",
            (record.attempt_id,),
        ).fetchall()
        expected = [
            (i, p.artifact_sha256, p.artifact_read_id, p.extraction_id)
            for i, p in enumerate(record.parents)
        ]
        if expected != [
            tuple(
                link[c] for c in ("ordinal", "artifact_sha256", "artifact_read_id", "extraction_id")
            )
            for link in links
        ]:
            raise ValueError("catalog attempt SQL links differ from its retained manifest")
        for parent in record.parents:
            artifact = connection.execute(
                "SELECT * FROM artifacts WHERE sha256=?", (parent.artifact_sha256,)
            ).fetchone()
            if artifact is None or type(artifact["byte_size"]) is not int:
                raise ValueError("catalog attempt artifact is unavailable")
            body = ArtifactMetadata(
                sha256=artifact["sha256"],
                byte_size=artifact["byte_size"],
                recorded_at=dt.datetime.fromisoformat(artifact["recorded_at"]),
            )
            if parent.artifact_read_id is not None:
                receipt_row = connection.execute(
                    "SELECT * FROM artifact_reads WHERE read_id=?", (parent.artifact_read_id,)
                ).fetchone()
                if receipt_row is None or type(receipt_row["byte_size"]) is not int:
                    raise ValueError("catalog attempt read is unavailable")
                receipt = ArtifactReadReceipt(
                    sha256=receipt_row["artifact_sha256"],
                    byte_size=receipt_row["byte_size"],
                    requested_url=receipt_row["requested_url"],
                    recorded_at=dt.datetime.fromisoformat(receipt_row["recorded_at"]),
                )
                if (receipt.read_id, receipt.sha256, receipt.byte_size) != (
                    parent.artifact_read_id,
                    parent.artifact_sha256,
                    body.byte_size,
                ):
                    raise ValueError("catalog attempt read belongs to another body")
            if parent.extraction_id is not None:
                result_row = connection.execute(
                    "SELECT * FROM extraction_results WHERE extraction_id=?",
                    (parent.extraction_id,),
                ).fetchone()
                if result_row is None:
                    raise ValueError("catalog attempt extraction is unavailable")
                result = ExtractionResult.model_validate_json(result_row["result_json"])
                if (result.result_id, result.artifact_sha256, result_row["artifact_sha256"]) != (
                    parent.extraction_id,
                    parent.artifact_sha256,
                    parent.artifact_sha256,
                ):
                    raise ValueError("catalog attempt extraction belongs to another body")
        return record
    except (ValueError, TypeError, KeyError, IndexError, OverflowError, sqlite3.Error) as exc:
        raise SourceCatalogRowError("invalid_source_catalog_attempt") from exc


def catalog_attempt_issues(connection: sqlite3.Connection) -> Iterator[tuple[str, str]]:
    expected = {}
    for statement in SOURCE_CATALOG_MIGRATION.split(";"):
        tokens = statement.split()
        if tokens:
            expected[tokens[2]] = " ".join(tokens)
    actual = {
        row["name"]: " ".join(row["sql"].split())
        for row in connection.execute("SELECT name,sql FROM sqlite_master WHERE sql IS NOT NULL")
        if row["name"] in expected
    }
    if actual != expected:
        yield "source_catalog_schema_mismatch", "source_catalog_attempts"
        return
    for row in connection.execute("SELECT * FROM source_catalog_snapshots ORDER BY catalog_id"):
        try:
            read_catalog(row)
        except SourceCatalogRowError as exc:
            yield str(exc), "source_catalog_snapshots/" + row["catalog_id"]
    for row in connection.execute("SELECT * FROM source_catalog_attempts ORDER BY attempt_id"):
        try:
            read_attempt(connection, row)
        except SourceCatalogRowError as exc:
            yield str(exc), "source_catalog_attempts/" + row["attempt_id"]
    for row in connection.execute(
        "SELECT p.attempt_id FROM source_catalog_attempt_parents p"
        " LEFT JOIN source_catalog_attempts a USING(attempt_id) WHERE a.attempt_id IS NULL"
    ):
        yield "orphan_source_catalog_parent", "source_catalog_attempt_parents/" + row["attempt_id"]


def validate_catalog_attempts(connection: sqlite3.Connection) -> None:
    previous = connection.row_factory
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if (
            not TABLES.intersection(tables)
            and connection.execute("PRAGMA user_version").fetchone()[0] < 65
        ):
            return
        if not tables >= TABLES:
            raise SourceCatalogRowError("source_catalog_table_missing")
        for code, _ in catalog_attempt_issues(connection):
            raise SourceCatalogRowError(code)
    finally:
        connection.row_factory = previous
