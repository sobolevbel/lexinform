"""Repository, doctor and restore share the exact Senate record and physical-parent checks."""

import hashlib
import sqlite3

from lexinform.models import (
    ArtifactMetadata,
    ArtifactReadReceipt,
    DocumentSelectionRecord,
    ExtractionResult,
    SourceObservation,
)
from lexinform.models.senate_inspection import SenateInspectionLink, SenateInspectionRecord


class SenateInspectionRowError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def senate_inspection_links(
    connection: sqlite3.Connection, inspection_id: str
) -> tuple[SenateInspectionLink, ...]:
    return tuple(
        SenateInspectionLink.model_validate(dict(row))
        for row in connection.execute(
            "SELECT descriptor_index,descriptor_sha256,requested_url_sha256,"
            "observation_id,selection_id,"
            "artifact_sha256,artifact_bytes,reader_text_sha256,artifact_read_id,extraction_id"
            " FROM senate_inspection_sources WHERE inspection_id=? ORDER BY descriptor_index",
            (inspection_id,),
        )
    )


def verify_senate_body(connection: sqlite3.Connection, link: SenateInspectionLink) -> None:
    row = connection.execute(
        "SELECT * FROM artifacts WHERE sha256=?", (link.artifact_sha256,)
    ).fetchone()
    if row is None:
        raise ValueError("Senate body metadata is missing")
    artifact = ArtifactMetadata.model_validate(dict(row))
    if artifact.byte_size != link.artifact_bytes:
        raise ValueError("Senate body size differs from its exact artifact parent")
    if link.artifact_read_id is not None:
        row = connection.execute(
            "SELECT * FROM artifact_reads WHERE read_id=?", (link.artifact_read_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Senate read parent is missing")
        read = ArtifactReadReceipt(
            sha256=row["artifact_sha256"],
            byte_size=row["byte_size"],
            requested_url=row["requested_url"],
            recorded_at=row["recorded_at"],
        )
        if (read.read_id, read.sha256, read.byte_size) != (
            link.artifact_read_id,
            link.artifact_sha256,
            link.artifact_bytes,
        ):
            raise ValueError("Senate read parent differs from its exact requested body")
        if hashlib.sha256(read.requested_url.encode()).hexdigest() != link.requested_url_sha256:
            raise ValueError("Senate read parent differs from its exact requested URL hash")
    if link.extraction_id is not None:
        row = connection.execute(
            "SELECT * FROM extraction_results WHERE extraction_id=?", (link.extraction_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Senate extraction parent is missing")
        extraction = ExtractionResult.model_validate_json(row["result_json"])
        if (extraction.result_id, extraction.artifact_sha256) != (
            link.extraction_id,
            link.artifact_sha256,
        ) or extraction.artifact_sha256 != row["artifact_sha256"]:
            raise ValueError("Senate extraction must identify its exact outer body and metadata")


def verify_senate_source(
    connection: sqlite3.Connection, record: SenateInspectionRecord, link: SenateInspectionLink
) -> None:
    verify_senate_body(connection, link)
    if link.observation_id is None:
        return
    row = connection.execute(
        "SELECT * FROM source_observations WHERE observation_id=?", (link.observation_id,)
    ).fetchone()
    if row is None:
        raise ValueError("Senate source observation is missing")
    observation = SourceObservation.model_validate_json(row["observation_json"])
    expected = (
        observation.observation_id,
        observation.term,
        observation.number,
        observation.purpose,
        observation.selection_id,
        observation.document.url,
        observation.document.kind,
    )
    if expected != tuple(
        row[key]
        for key in (
            "observation_id",
            "term",
            "number",
            "purpose",
            "selection_id",
            "source_url",
            "source_kind",
        )
    ):
        raise ValueError("Senate source columns differ from their immutable observation")
    if (
        observation.term,
        observation.number,
        observation.purpose,
        observation.selection_id,
        observation.document.kind,
    ) != (
        record.owner.term,
        record.owner.number,
        "analysis",
        link.selection_id,
        "senate_amendments",
    ):
        raise ValueError("Senate source owner or selection differs from its inspection link")
    if hashlib.sha256(observation.document.url.encode()).hexdigest() != link.requested_url_sha256:
        raise ValueError("Senate source must match the exact requested URL hash")
    row = connection.execute(
        "SELECT * FROM document_selections WHERE selection_id=?", (link.selection_id,)
    ).fetchone()
    if row is None:
        raise ValueError("Senate source selection is missing")
    selection = DocumentSelectionRecord.model_validate_json(row["record_json"])
    if (
        selection.selection_id,
        selection.selection.question,
        selection.selection.policy_version,
        selection.selection.state,
    ) != tuple(row[key] for key in ("selection_id", "question", "policy_version", "state")):
        raise ValueError("Senate source selection columns differ from their identity")
    physical = selection.selection
    if (
        physical.question not in ("senate_resolution_source", "senate_candidate_inspection")
        or physical.state != "unverified"
        or len(physical.parts) != 1
    ):
        raise ValueError("Senate candidate links require an unverified physical source snapshot")
    part = physical.parts[0]
    if (
        part.members
        or part.url != observation.document.url
        or part.source_sha256 != link.artifact_sha256
        or part.role != "unknown"
    ):
        raise ValueError("Senate source must bind its actual complete outer body")
    if link.reader_text_sha256 is not None and part.text_sha256 != link.reader_text_sha256:
        raise ValueError("Senate reader-text identity differs from its physical source snapshot")
    if (
        connection.execute(
            "SELECT 1 FROM selection_artifacts WHERE selection_id=? AND part_scope='parts'"
            " AND part_index=0 AND artifact_sha256=?",
            (link.selection_id, link.artifact_sha256),
        ).fetchone()
        is None
    ):
        raise ValueError("Senate selection has lost its exact outer artifact link")
    if link.extraction_id is not None and (
        connection.execute(
            "SELECT 1 FROM selection_extractions WHERE selection_id=? AND extraction_id=?",
            (link.selection_id, link.extraction_id),
        ).fetchone()
        is None
    ):
        raise ValueError("Senate source has lost its exact extraction link")


def verify_senate_inspection(
    connection: sqlite3.Connection,
    record: SenateInspectionRecord,
    links: tuple[SenateInspectionLink, ...],
) -> None:
    record.verify_links(links)
    if (
        connection.execute(
            "SELECT 1 FROM bills WHERE term=? AND number=?",
            (record.owner.term, record.owner.number),
        ).fetchone()
        is None
    ):
        raise ValueError("Senate inspection requires its known Bill owner")
    for link in links:
        verify_senate_source(connection, record, link)


def read_senate_inspection(
    connection: sqlite3.Connection, row: sqlite3.Row
) -> SenateInspectionRecord:
    try:
        record = SenateInspectionRecord.model_validate_json(row["record_json"])
        if record.recorded_at is None:
            raise ValueError("persisted Senate inspections require their recording time")
        expected = (
            record.inspection_id,
            record.semantic_sha256,
            record.owner.term,
            record.owner.number,
            record.resolution_print.term,
            record.resolution_print.number,
            record.coverage,
            record.descriptor_count,
            record.link_count,
            record.links_sha256,
            record.recorded_at.isoformat(),
        )
        if expected != tuple(
            row[key]
            for key in (
                "inspection_id",
                "semantic_sha256",
                "owner_term",
                "owner_number",
                "resolution_term",
                "resolution_number",
                "coverage",
                "descriptor_count",
                "link_count",
                "links_sha256",
                "recorded_at",
            )
        ):
            raise SenateInspectionRowError("senate_inspection_columns_mismatch")
    except SenateInspectionRowError:
        raise
    except (ValueError, TypeError, IndexError) as exc:
        raise SenateInspectionRowError("invalid_senate_inspection") from exc
    try:
        verify_senate_inspection(
            connection, record, senate_inspection_links(connection, record.inspection_id)
        )
    except (ValueError, TypeError, IndexError, sqlite3.Error) as exc:
        raise SenateInspectionRowError("senate_inspection_parent_mismatch") from exc
    return record


def validate_senate_inspections(connection: sqlite3.Connection) -> None:
    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    required = {"senate_inspections", "senate_inspection_sources"}
    if not required <= tables:
        if (
            required.intersection(tables)
            or connection.execute("PRAGMA user_version").fetchone()[0] >= 61
        ):
            raise ValueError("Senate inspection schema tables are missing")
        return
    if (
        connection.execute(
            "SELECT 1 FROM senate_inspection_sources AS link"
            " LEFT JOIN senate_inspections AS parent USING(inspection_id)"
            " WHERE parent.inspection_id IS NULL LIMIT 1"
        ).fetchone()
        is not None
    ):
        raise ValueError("Senate inspection source link has no recorded parent")
    factory = connection.row_factory
    connection.row_factory = sqlite3.Row
    try:
        for row in connection.execute("SELECT * FROM senate_inspections"):
            read_senate_inspection(connection, row)
    finally:
        connection.row_factory = factory
