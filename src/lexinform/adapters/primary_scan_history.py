"""Verify retained source and submitted-page identities for direct primary scans."""

import sqlite3

from lexinform.adapters.document_source_rows import DocumentSourceRows
from lexinform.models import AnalysisRecord, BatchItemMeta, ComputationResult, SourceObservation


class PrimaryScanEvidenceUnavailableError(ValueError):
    pass


def read_primary_scan_source(
    db: sqlite3.Connection, paid: ComputationResult, meta: BatchItemMeta
) -> tuple[SourceObservation, dict[str, object]]:
    """Read the scoped source and physical window of a retained primary scan."""
    scan, manifest = meta.selection.scan_input, meta.frozen_request_manifest
    if scan is None or manifest is None or not meta.source_url:
        raise PrimaryScanEvidenceUnavailableError("primary_scan_input_unavailable")
    record = paid.record
    if (
        paid.validation != "validated"
        or not isinstance(record, AnalysisRecord)
        or record.text_source != "scan"
        or record.selection.without_text() != meta.selection.without_text()
        or record.text_sha256 != scan.source_sha256
        or record.input_chars != meta.input_chars
        or manifest.provider != paid.provider
        or manifest.prompt_version != record.prompt_version
    ):
        raise ValueError("retained primary scan differs from its paid request")
    manifest.require_request(paid.purpose, paid.request_sha256)
    if len(meta.selection.parts) != 1 or scan.transformations not in ((), ("cover_letter",)):
        raise PrimaryScanEvidenceUnavailableError("primary_scan_outside_direct_pdf_scope")
    part = meta.selection.parts[0]
    last_page = scan.first_page + scan.page_count - 1
    cover_pages = len(scan.transformations)
    if (
        part.members
        or (part.url, part.source_sha256, part.pages, part.page_start, part.page_end)
        != (meta.source_url, scan.source_sha256, scan.source_pages, scan.first_page, last_page)
        or scan.first_page != 1 + cover_pages
        or last_page != scan.source_pages
        or not cover_pages
        and (scan.input_sha256, scan.input_bytes) != (scan.source_sha256, scan.source_bytes)
    ):
        raise ValueError("retained scan does not identify its direct physical page window")
    cursor = db.cursor()
    cursor.row_factory = sqlite3.Row
    rows = cursor.execute(
        """
        SELECT o.observation_id, a.byte_size AS artifact_bytes, r.byte_size AS read_bytes
        FROM source_observations o
        JOIN selection_artifacts sa USING (selection_id)
        JOIN artifacts a ON a.sha256=sa.artifact_sha256
        JOIN artifact_reads r ON r.artifact_sha256=a.sha256 AND r.requested_url=o.source_url
        WHERE o.term=? AND o.number=? AND o.purpose='analysis' AND o.source_kind=?
          AND o.source_url=? AND sa.part_scope='parts' AND sa.part_index=0 AND a.sha256=?
        LIMIT 2
        """,
        (paid.term, paid.number, meta.source_kind, meta.source_url, scan.source_sha256),
    ).fetchall()
    if len(rows) != 1:
        raise PrimaryScanEvidenceUnavailableError("unique_primary_scan_source_unavailable")
    (row,) = rows
    reader = DocumentSourceRows(db)
    source = reader.get_source_observation(row["observation_id"])
    if source is None or (selection := reader.get_document_selection(source.selection_id)) is None:
        raise ValueError("retained scan source is missing its selected document")
    if (
        row["artifact_bytes"] != scan.source_bytes
        or row["read_bytes"] != scan.source_bytes
        or len(selection.selection.parts) != 1
    ):
        raise ValueError("retained scan source differs from its artifact")
    original = selection.selection.parts[0]
    if original.members or (original.url, original.source_sha256, original.pages) != (
        meta.source_url,
        scan.source_sha256,
        scan.source_pages,
    ):
        raise ValueError("retained scan source differs from its selection")
    return source, {
        "artifact_sha256": scan.source_sha256,
        "recorded_scan_input": scan.model_dump(mode="json"),
        "retained_provider_request_manifest_sha256": manifest.manifest_sha256,
    }
