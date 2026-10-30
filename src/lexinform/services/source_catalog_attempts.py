"""Caller-owned persistence binds actual failed source attempts after their physical parents."""

import datetime as dt
from dataclasses import dataclass

from pydantic_core import PydanticSerializationError

from lexinform.models import ArtifactIdentity, ArtifactReadIdentity, ExtractionResult
from lexinform.models.source_catalog_attempts import SourceCatalogAttempt, SourceCatalogSnapshot
from lexinform.models.source_omissions import (
    BillOmissionOwner,
    SourceReferenceOmissionOwner,
    source_omission_sha256,
)
from lexinform.ports import BillRepository
from lexinform.services.document_history import record_artifact_reads, record_artifacts


@dataclass(frozen=True)
class SourceCatalogObservation:
    catalog: SourceCatalogSnapshot
    attempt: SourceCatalogAttempt
    artifacts: tuple[ArtifactIdentity, ...]
    reads: tuple[ArtifactReadIdentity, ...]
    extractions: tuple[ExtractionResult, ...]
    order: tuple[int, ...]


def serializable_catalog(catalog: SourceCatalogSnapshot) -> SourceCatalogSnapshot:
    catalog = SourceCatalogSnapshot.model_validate(catalog)
    while True:
        try:
            return SourceCatalogSnapshot.model_validate_json(catalog.model_dump_json())
        except PydanticSerializationError:
            if not catalog.descriptors:
                raise
            values = catalog.model_dump(mode="json", exclude={"catalog_id", "recorded_at"})
            values["descriptors"] = [
                entry.model_dump(mode="json") for entry in catalog.descriptors[:-1]
            ]
            values["coverage"] = "partial"
            catalog = SourceCatalogSnapshot.model_validate(
                {**values, "catalog_id": "source-catalog-v1:" + source_omission_sha256(values)}
            )


def _bind_attempt(repo: BillRepository, attempt: SourceCatalogAttempt) -> SourceCatalogAttempt:
    owner = attempt.owner
    enrolled = repo.bill_exists(owner.term, owner.number)
    owner_type = BillOmissionOwner if enrolled else SourceReferenceOmissionOwner
    actual = owner_type(term=owner.term, number=owner.number, purpose=owner.purpose)
    if actual == owner:
        return attempt
    values = attempt.model_dump(mode="json", exclude={"attempt_id", "recorded_at"})
    values["owner"] = actual.model_dump(mode="json")
    return SourceCatalogAttempt.model_validate(
        {**values, "attempt_id": "source-attempt-v1:" + source_omission_sha256(values)}
    )


def record_catalog_observation(
    repo: BillRepository, observation: SourceCatalogObservation, *, recorded_at: dt.datetime
) -> None:
    record_artifacts(repo, observation.artifacts, recorded_at=recorded_at)
    record_artifact_reads(repo, observation.reads, recorded_at=recorded_at)
    for result in observation.extractions:
        repo.save_extraction_result(result, recorded_at=recorded_at)
    repo.save_source_catalog_snapshot(observation.catalog, recorded_at=recorded_at)
    repo.save_source_catalog_attempt(
        _bind_attempt(repo, observation.attempt), recorded_at=recorded_at
    )
