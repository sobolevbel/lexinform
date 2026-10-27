"""Calling transactions record known physical metadata independently of body retention."""

import datetime as dt

from lexinform.models import (
    ArtifactIdentity,
    ArtifactMetadata,
    DocumentSelectionRecord,
    SelectionArtifactReference,
    SourceObservation,
)
from lexinform.models.artifacts import SelectionPartScope
from lexinform.ports import BillRepository


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


def record_document_source(
    repo: BillRepository,
    selection: DocumentSelectionRecord,
    observation: SourceObservation,
    artifacts: tuple[ArtifactIdentity, ...],
) -> None:
    """Write the source and known outer-body links inside the caller's transaction."""
    record_artifacts(repo, artifacts, recorded_at=observation.recorded_at)
    repo.save_source_observation(selection, observation)
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
