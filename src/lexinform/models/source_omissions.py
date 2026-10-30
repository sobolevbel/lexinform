"""Owner-bound omissions preserve addressable evidence without unsafe URLs or exception prose."""

import datetime as dt
import hashlib
import json
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lexinform.models.metadata_omissions import MetadataUrlOmissions

MAX_SOURCE_OMISSION_BYTES = 8192
MAX_SOURCE_OMISSION_PARENTS = 32
Sha256 = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]
Positive = Annotated[int, Field(strict=True, ge=1, le=2**63 - 1)]
Count = Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]
BillNumber = Annotated[str, Field(strict=True, min_length=1, max_length=128)]


def source_omission_canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


def source_omission_sha256(value: object) -> str:
    """Hash canonical JSON ASCII, including escaped surrogates, never requested URL UTF-8 bytes."""
    return hashlib.sha256(source_omission_canonical(value)).hexdigest()


class _Fact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")


class BillOmissionOwner(_Fact):
    kind: Literal["bill"] = "bill"
    term: Positive
    number: BillNumber
    purpose: Literal["prefilter", "analysis", "review"]


class RclOmissionOwner(_Fact):
    kind: Literal["rcl_letter"] = "rcl_letter"
    project_id: Positive
    stage_id: Positive | None
    stage_number: Count | None
    letter_id: Positive | None


class SenateOmissionOwner(_Fact):
    kind: Literal["senate"] = "senate"
    term: Positive
    number: BillNumber
    resolution_term: Positive
    resolution_number: BillNumber


SourceOmissionOwner = Annotated[
    BillOmissionOwner | RclOmissionOwner | SenateOmissionOwner, Field(discriminator="kind")
]


class SourceOmissionParent(_Fact):
    scope: Literal["known_available"] = "known_available"
    artifact_sha256: Sha256
    artifact_read_id: Annotated[str, Field(pattern=r"^artifact-read-v1:[0-9a-f]{64}$")] | None = (
        None
    )
    extraction_id: Annotated[str, Field(pattern=r"^extraction-v1:[0-9a-f]{64}$")] | None = None
    source_observation_id: (
        Annotated[str, Field(pattern=r"^source-observation-v1:[0-9a-f]{64}$")] | None
    ) = None
    source_relation: Literal["known", "unknown"] = "unknown"

    @model_validator(mode="after")
    def source_reference(self) -> Self:
        if (self.source_relation == "known") != (self.source_observation_id is not None):
            raise ValueError("source relation must describe an actual retained observation")
        return self


class SourceMetadataOmission(_Fact):
    omission_id: Annotated[str, Field(pattern=r"^source-omission-v1:[0-9a-f]{64}$")]
    version: Literal[1] = 1
    owner: SourceOmissionOwner
    source_view_encoding: Literal["canonical-json-ascii-v1"] = "canonical-json-ascii-v1"
    source_view_sha256: Sha256
    diagnostics: MetadataUrlOmissions
    parents: tuple[SourceOmissionParent, ...] = Field(max_length=MAX_SOURCE_OMISSION_PARENTS)
    parent_scope: Literal["known_available"] = "known_available"
    parent_coverage: Literal["complete", "partial"] = "complete"
    parent_count: Count
    parents_sha256: Sha256
    recorded_at: dt.datetime | None = None

    @field_validator("recorded_at")
    @classmethod
    def aware_time(cls, value: dt.datetime | None) -> dt.datetime | None:
        if value is None:
            return None
        if value.utcoffset() is None:
            raise ValueError("source omission recording time requires a timezone")
        try:
            return value.astimezone(dt.UTC)
        except OverflowError as exc:
            raise ValueError("source omission recording time is outside UTC range") from exc

    @classmethod
    def of(
        cls,
        *,
        owner: BillOmissionOwner | RclOmissionOwner | SenateOmissionOwner,
        source_view: object,
        diagnostics: MetadataUrlOmissions,
        parents: tuple[SourceOmissionParent, ...] = (),
        parent_count: int | None = None,
        parents_sha256: str | None = None,
    ) -> Self:
        parents = tuple(
            sorted(
                parents, key=lambda item: source_omission_canonical(item.model_dump(mode="json"))
            )
        )
        if parent_count is not None and parent_count > len(parents) and parents_sha256 is None:
            raise ValueError("partial parents require the actual full observed manifest hash")
        values = {
            "version": 1,
            "owner": owner.model_dump(mode="json"),
            "source_view_encoding": "canonical-json-ascii-v1",
            "source_view_sha256": source_omission_sha256(source_view),
            "diagnostics": diagnostics.model_dump(mode="json"),
            "parents": [item.model_dump(mode="json") for item in parents],
            "parent_scope": "known_available",
            "parent_coverage": "partial"
            if parent_count is not None and parent_count > len(parents)
            else "complete",
            "parent_count": len(parents) if parent_count is None else parent_count,
            "parents_sha256": parents_sha256
            if parents_sha256 is not None
            else source_omission_sha256([item.model_dump(mode="json") for item in parents]),
        }
        return cls.model_validate(
            {**values, "omission_id": "source-omission-v1:" + source_omission_sha256(values)}
        )

    @property
    def budget_bytes(self) -> int:
        values = self.model_dump(mode="json")
        values["recorded_at"] = (
            self.recorded_at or dt.datetime.max.replace(tzinfo=dt.UTC)
        ).isoformat()
        return len(source_omission_canonical(values))

    @model_validator(mode="before")
    @classmethod
    def integer_version(cls, value: object) -> object:
        if isinstance(value, dict) and type(value.get("version", 1)) is not int:
            raise ValueError("source omission version must be an integer")
        return value

    @model_validator(mode="after")
    def identity(self) -> Self:
        encoded = [source_omission_canonical(item.model_dump(mode="json")) for item in self.parents]
        if encoded != sorted(set(encoded)):
            raise ValueError("source omission parents must be unique and canonically ordered")
        if (self.parent_coverage == "partial") != (self.parent_count > len(self.parents)):
            raise ValueError("parent coverage must describe the retained known manifest")
        if self.parent_count < len(self.parents):
            raise ValueError("retained parents exceed the known parent universe")
        if self.parent_count == len(self.parents) and self.parents_sha256 != source_omission_sha256(
            [item.model_dump(mode="json") for item in self.parents]
        ):
            raise ValueError("complete parent manifest differs from its retained links")
        identity = source_omission_sha256(
            self.model_dump(mode="json", exclude={"omission_id", "recorded_at"})
        )
        if self.omission_id != "source-omission-v1:" + identity:
            raise ValueError(
                "source omission identity differs from its owner and retained evidence"
            )
        if self.budget_bytes > MAX_SOURCE_OMISSION_BYTES:
            raise ValueError("source omission exceeds its complete envelope byte budget")
        return self
