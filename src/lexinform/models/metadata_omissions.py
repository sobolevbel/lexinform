"""Partial omission examples account for observations without retaining URLs or exception prose."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lexinform.metadata_urls import METADATA_URL_POLICY_VERSION, MetadataUrlOmissionReason

MAX_METADATA_OMISSION_BYTES = 2048
MAX_METADATA_OMISSION_EXAMPLES = 8
MetadataOmissionCount = Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]
MetadataOmissionPositiveCount = Annotated[int, Field(strict=True, ge=1, le=2**63 - 1)]
MetadataOmissionSha256 = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]


class MetadataUrlReasonCount(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: MetadataUrlOmissionReason
    count: MetadataOmissionPositiveCount


class MetadataUrlOmissionExample(MetadataUrlReasonCount):
    requested_url_sha256: MetadataOmissionSha256 | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    artifact_sha256: MetadataOmissionSha256 | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @property
    def key(self) -> tuple[str, str, str]:
        return self.reason, self.requested_url_sha256 or "", self.artifact_sha256 or ""

    @model_validator(mode="after")
    def available_url_hash(self) -> Self:
        if self.reason == "invalid_unicode" and self.requested_url_sha256 is not None:
            raise ValueError("invalid Unicode has no UTF-8 URL hash")
        if self.requested_url_sha256 is None and self.reason not in (
            "invalid_unicode",
            "opaque_failure_prose",
        ):
            raise ValueError("this omission reason requires the exact UTF-8 URL hash")
        return self


class MetadataUrlOmissions(BaseModel):
    """Partial examples count omission observations, never distinct downloads or a full manifest."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    policy_version: Literal["metadata-url-safety-v1"] = METADATA_URL_POLICY_VERSION
    unit: Literal["metadata_omission_observation"] = "metadata_omission_observation"
    example_scope: Literal["partial"] = "partial"
    total: MetadataOmissionPositiveCount
    reasons: tuple[MetadataUrlReasonCount, ...] = Field(min_length=1, max_length=8)
    examples: tuple[MetadataUrlOmissionExample, ...] = Field(
        default=(), max_length=MAX_METADATA_OMISSION_EXAMPLES
    )
    omitted_observations: MetadataOmissionCount = 0

    @model_validator(mode="before")
    @classmethod
    def integer_version(cls, value: object) -> object:
        if isinstance(value, dict) and type(value.get("schema_version", 1)) is not int:
            raise ValueError("metadata omission diagnostics require an integer version")
        return value

    @model_validator(mode="after")
    def bounded_accounting(self) -> Self:
        reasons = [item.reason for item in self.reasons]
        if reasons != sorted(set(reasons)):
            raise ValueError("metadata omission reasons must be unique and ordered")
        if sum(item.count for item in self.reasons) != self.total:
            raise ValueError("metadata omission reasons do not account for the total")
        keys = [item.key for item in self.examples]
        if keys != sorted(set(keys)):
            raise ValueError("metadata omission examples must be unique and ordered")
        if sum(item.count for item in self.examples) + self.omitted_observations != self.total:
            raise ValueError("metadata omission examples do not account for the remainder")
        reason_counts = {item.reason: item.count for item in self.reasons}
        for reason in {item.reason for item in self.examples}:
            if sum(
                item.count for item in self.examples if item.reason == reason
            ) > reason_counts.get(reason, 0):
                raise ValueError("metadata omission examples exceed their reason count")
        if len(self.model_dump_json().encode()) > MAX_METADATA_OMISSION_BYTES:
            raise ValueError("metadata omission diagnostics exceed the 2 KiB aggregate cap")
        return self
