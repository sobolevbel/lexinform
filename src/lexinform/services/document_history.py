"""Calling transactions record known physical metadata independently of body retention."""

import datetime as dt
from dataclasses import dataclass
from typing import Literal

from lexinform.models import (
    ArtifactIdentity,
    ArtifactMetadata,
    ArtifactReadIdentity,
    ArtifactReadReceipt,
    DocumentSelectionRecord,
    ExtractionResult,
    SelectionArtifactReference,
    SelectionExtractionReference,
    SourceObservation,
    TextDocument,
)
from lexinform.models.artifacts import SelectionPartScope
from lexinform.ports import BillRepository


@dataclass(frozen=True)
class DocumentSourceRead:
    document: TextDocument
    selection: DocumentSelectionRecord
    artifacts: tuple[ArtifactIdentity, ...]
    reads: tuple[ArtifactReadIdentity, ...] = ()
    extractions: tuple[ExtractionResult, ...] = ()


def record_source_read(
    repo: BillRepository,
    source: DocumentSourceRead,
    *,
    term: int,
    number: str,
    purpose: Literal["prefilter", "analysis", "review"],
    recorded_at: dt.datetime,
) -> None:
    """Record a consumer's source metadata inside its caller-owned transaction."""
    observation = SourceObservation(
        term=term,
        number=number,
        purpose=purpose,
        document=source.document,
        selection_id=source.selection.selection_id,
        recorded_at=recorded_at,
    )
    record_document_source(
        repo,
        source.selection,
        observation,
        source.artifacts,
        reads=source.reads,
        extractions=source.extractions,
    )


def record_artifacts(
    repo: BillRepository, artifacts: tuple[ArtifactIdentity, ...], *, recorded_at: dt.datetime
) -> None:
    """Write complete-body metadata inside the caller's transaction."""
    for artifact in artifacts:
        repo.save_artifact_metadata(
            ArtifactMetadata(
                sha256=artifact.sha256, byte_size=artifact.byte_size, recorded_at=recorded_at
            )
        )


def record_artifact_reads(
    repo: BillRepository, reads: tuple[ArtifactReadIdentity, ...], *, recorded_at: dt.datetime
) -> None:
    """Persist known complete-body receipts inside the calling transaction."""
    record_artifacts(repo, reads, recorded_at=recorded_at)
    for read in reads:
        repo.save_artifact_read(
            ArtifactReadReceipt(
                sha256=read.sha256,
                byte_size=read.byte_size,
                requested_url=read.requested_url,
                recorded_at=recorded_at,
            )
        )


def record_document_source(
    repo: BillRepository,
    selection: DocumentSelectionRecord,
    observation: SourceObservation,
    artifacts: tuple[ArtifactIdentity, ...],
    *,
    reads: tuple[ArtifactReadIdentity, ...] = (),
    extractions: tuple[ExtractionResult, ...] = (),
) -> None:
    """Write the source and known outer-body links inside the caller's transaction."""
    record_artifacts(repo, artifacts, recorded_at=observation.recorded_at)
    record_artifact_reads(repo, reads, recorded_at=observation.recorded_at)
    repo.save_source_observation(selection, observation)
    for extraction in extractions:
        repo.save_extraction_result(extraction, recorded_at=observation.recorded_at)
        repo.save_selection_extraction(
            SelectionExtractionReference(
                selection_id=selection.selection_id,
                extraction_id=extraction.result_id,
                artifact_sha256=extraction.artifact_sha256,
            )
        )
    scopes: tuple[SelectionPartScope, ...] = ("parts", "observed_parts")
    for scope in scopes:
        parts = (
            selection.selection.parts if scope == "parts" else selection.selection.observed_parts
        )
        for index, part in enumerate(parts):
            if not part.source_sha256 or repo.get_artifact_metadata(part.source_sha256) is None:
                continue
            repo.save_selection_artifact(
                SelectionArtifactReference(
                    selection_id=selection.selection_id,
                    part_scope=scope,
                    part_index=index,
                    artifact_sha256=part.source_sha256,
                )
            )
