"""Bounded owner evidence hashes observed metadata without retaining unsafe source fields."""

from lexinform.models.metadata_omissions import MetadataUrlOmissions
from lexinform.models.source_omissions import (
    MAX_SOURCE_OMISSION_PARENTS,
    SourceMetadataOmission,
    SourceOmissionOwner,
    SourceOmissionParent,
    source_omission_canonical,
    source_omission_sha256,
)


def source_omission_record(
    owner: SourceOmissionOwner,
    *,
    source_view_sha256: str,
    diagnostics: MetadataUrlOmissions,
    parents: tuple[SourceOmissionParent, ...],
) -> SourceMetadataOmission:
    ordered = sorted(
        {source_omission_canonical(item.model_dump(mode="json")): item for item in parents}.items()
    )
    manifest = [item.model_dump(mode="json") for _, item in ordered]
    retained = manifest[:MAX_SOURCE_OMISSION_PARENTS]
    values = {
        "version": 1,
        "owner": owner.model_dump(mode="json"),
        "source_view_encoding": "canonical-json-ascii-v1",
        "source_view_sha256": source_view_sha256,
        "diagnostics": diagnostics.model_dump(mode="json"),
        "parent_scope": "known_available",
        "parent_count": len(manifest),
        "parents_sha256": source_omission_sha256(manifest),
    }
    while True:
        payload = {
            **values,
            "parents": retained,
            "parent_coverage": "partial" if len(retained) < len(manifest) else "complete",
        }
        try:
            return SourceMetadataOmission.model_validate(
                {**payload, "omission_id": "source-omission-v1:" + source_omission_sha256(payload)}
            )
        except ValueError:
            if not retained:
                raise
            retained.pop()


def source_omission_owner(
    record: SourceMetadataOmission, owner: SourceOmissionOwner
) -> SourceMetadataOmission:
    payload = record.model_dump(mode="json", exclude={"omission_id", "recorded_at"})
    payload["owner"] = owner.model_dump(mode="json")
    return SourceMetadataOmission.model_validate(
        {**payload, "omission_id": "source-omission-v1:" + source_omission_sha256(payload)}
    )
