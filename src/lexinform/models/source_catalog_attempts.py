"""Safe pre-return source attempts retain an actual catalog without inventing a selection."""

import datetime as dt
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lexinform.metadata_urls import METADATA_URL_POLICY_VERSION, metadata_url_decision
from lexinform.models.sejm import TextDocument
from lexinform.models.source_omissions import (
    BillOmissionOwner,
    Count,
    Sha256,
    SourceMetadataOmission,
    SourceOmissionParent,
    SourceReferenceOmissionOwner,
    source_omission_canonical,
    source_omission_sha256,
)

MAX_CATALOG_BYTES = 8192
AttemptOwner = Annotated[
    BillOmissionOwner | SourceReferenceOmissionOwner, Field(discriminator="kind")
]


class _Fact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")
    version: Literal[1] = 1
    recorded_at: dt.datetime | None = None

    @field_validator("recorded_at")
    @classmethod
    def timestamp(cls, value: dt.datetime | None) -> dt.datetime | None:
        return SourceMetadataOmission.aware_time(value)

    @model_validator(mode="before")
    @classmethod
    def integer_version(cls, value: object) -> object:
        if isinstance(value, dict) and type(value.get("version", 1)) is not int:
            raise ValueError("catalog fact version must be an integer")
        return value

    @property
    def budget_bytes(self) -> int:
        values = self.model_dump(mode="json")
        values["recorded_at"] = dt.datetime.max.replace(tzinfo=dt.UTC).isoformat()
        return len(source_omission_canonical(values))


def _safe_document_urls(document: TextDocument) -> None:
    for url in (
        document.url,
        *document.required_urls,
        *document.normative_urls,
        *document.amendment_urls,
        *document.extra_urls,
    ):
        if metadata_url_decision(url).reason is not None:
            raise ValueError("catalog descriptor URL is not admitted metadata")


class CatalogDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")
    address: tuple[Count, ...] = Field(max_length=64)
    document: TextDocument
    alternative_count: Count

    @model_validator(mode="after")
    def safe_descriptor(self) -> Self:
        doc = self.document
        if doc.alternatives:
            raise ValueError("catalog entries retain explicit child counts and separate addresses")
        _safe_document_urls(doc)
        return self


class SourceCatalogSnapshot(_Fact):
    catalog_id: Annotated[str, Field(pattern=r"^source-catalog-v1:[0-9a-f]{64}$")]
    source_view_encoding: Literal["canonical-json-ascii-v1"] = "canonical-json-ascii-v1"
    url_policy_version: Literal["metadata-url-safety-v1"] = METADATA_URL_POLICY_VERSION
    source_view_sha256: Sha256
    descriptors: tuple[CatalogDescriptor, ...] = Field(max_length=32)
    descriptor_count: Annotated[int, Field(strict=True, ge=1, le=2**63 - 1)]
    coverage: Literal["complete", "partial"]

    @classmethod
    def of(cls, document: TextDocument) -> Self:
        pending: list[tuple[tuple[int, ...], TextDocument]] = [((), document)]
        descriptors: list[CatalogDescriptor] = []
        count = 0
        while pending:
            address, current = pending.pop()
            _safe_document_urls(current)
            count += 1
            if len(descriptors) < 32:
                descriptors.append(
                    CatalogDescriptor(
                        address=address,
                        document=current.model_copy(update={"alternatives": ()}),
                        alternative_count=len(current.alternatives),
                    )
                )
            pending.extend(
                (address + (index,), child)
                for index, child in reversed(tuple(enumerate(current.alternatives)))
            )
        full_view_sha256 = source_omission_sha256(document.model_dump(mode="json"))
        while True:
            values = {
                "version": 1,
                "url_policy_version": METADATA_URL_POLICY_VERSION,
                "source_view_encoding": "canonical-json-ascii-v1",
                "source_view_sha256": full_view_sha256,
                "descriptors": [item.model_dump(mode="json") for item in descriptors],
                "descriptor_count": count,
                "coverage": "complete" if len(descriptors) == count else "partial",
            }
            values["catalog_id"] = "source-catalog-v1:" + source_omission_sha256(values)
            try:
                return cls.model_validate(values)
            except ValueError:
                if not descriptors:
                    raise
                descriptors.pop()

    @model_validator(mode="after")
    def identity(self) -> Self:
        expected: tuple[int, ...] | None = ()
        pending: list[tuple[tuple[int, ...], int, int]] = []
        outstanding = 1
        for entry in self.descriptors:
            if entry.address != expected:
                raise ValueError("catalog descriptors must be an actual ordered tree prefix")
            outstanding += entry.alternative_count - 1
            if entry.alternative_count:
                pending.append((entry.address, 0, entry.alternative_count))
            expected = None
            while pending:
                address, index, count = pending.pop()
                if index < count:
                    expected = address + (index,)
                    pending.append((address, index + 1, count))
                    break
        if outstanding == 0 and self.descriptor_count != len(self.descriptors):
            raise ValueError("closed catalog prefix cannot claim additional descriptors")
        if self.descriptor_count < len(self.descriptors) + outstanding:
            raise ValueError("catalog prefix exceeds its observed descriptor count")
        if (self.coverage == "complete") != (self.descriptor_count == len(self.descriptors)):
            raise ValueError("catalog coverage differs from its retained descriptor count")
        if self.coverage == "complete":
            if outstanding:
                raise ValueError("complete catalog has missing children")
            built: dict[tuple[int, ...], TextDocument] = {}
            for entry in reversed(self.descriptors):
                built[entry.address] = entry.document.model_copy(
                    update={
                        "alternatives": tuple(
                            built[entry.address + (index,)]
                            for index in range(entry.alternative_count)
                        )
                    }
                )
            if source_omission_sha256(built[()].model_dump(mode="json")) != self.source_view_sha256:
                raise ValueError("complete catalog differs from its observed source view")
        values = self.model_dump(mode="json", exclude={"catalog_id", "recorded_at"})
        if self.catalog_id != "source-catalog-v1:" + source_omission_sha256(values):
            raise ValueError("catalog identity differs from its retained evidence")
        if self.budget_bytes > MAX_CATALOG_BYTES:
            raise ValueError("catalog exceeds its timestamp-reserved byte envelope")
        return self


class SourceCatalogAttempt(_Fact):
    attempt_id: Annotated[str, Field(pattern=r"^source-attempt-v1:[0-9a-f]{64}$")]
    owner: AttemptOwner
    catalog_id: Annotated[str, Field(pattern=r"^source-catalog-v1:[0-9a-f]{64}$")]
    status: Literal["read_failed"] = "read_failed"
    selection: Literal["unknown"] = "unknown"
    error_class: Annotated[str, Field(strict=True, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")]
    parents: tuple[SourceOmissionParent, ...] = Field(max_length=32)
    parent_scope: Literal["known_available"] = "known_available"
    parent_coverage: Literal["complete", "partial"]
    parent_count: Count
    parents_sha256: Sha256

    @classmethod
    def of(
        cls,
        owner: BillOmissionOwner | SourceReferenceOmissionOwner,
        catalog_id: str,
        error_class: str,
        parents: tuple[SourceOmissionParent, ...] = (),
    ) -> Self:
        if any(parent.source_relation != "unknown" for parent in parents):
            raise ValueError("pre-return attempts have no selected source observation")
        full = tuple(
            sorted(
                set(parents),
                key=lambda item: source_omission_canonical(item.model_dump(mode="json")),
            )
        )
        retained = full[:32]
        while True:
            values = {
                "version": 1,
                "owner": owner.model_dump(mode="json"),
                "catalog_id": catalog_id,
                "status": "read_failed",
                "selection": "unknown",
                "error_class": error_class,
                "parents": [item.model_dump(mode="json") for item in retained],
                "parent_scope": "known_available",
                "parent_coverage": "complete" if len(full) == len(retained) else "partial",
                "parent_count": len(full),
                "parents_sha256": source_omission_sha256(
                    [item.model_dump(mode="json") for item in full]
                ),
            }
            values["attempt_id"] = "source-attempt-v1:" + source_omission_sha256(values)
            try:
                return cls.model_validate(values)
            except ValueError:
                if not retained:
                    raise
                retained = retained[:-1]

    @model_validator(mode="after")
    def identity(self) -> Self:
        encoded = [
            source_omission_canonical(parent.model_dump(mode="json")) for parent in self.parents
        ]
        if encoded != sorted(set(encoded)):
            raise ValueError("catalog attempt parents must be unique and canonical")
        if any(parent.source_relation != "unknown" for parent in self.parents):
            raise ValueError("pre-return attempts have no selected source observation")
        if self.parent_count < len(self.parents) or (self.parent_coverage == "complete") != (
            self.parent_count == len(self.parents)
        ):
            raise ValueError("catalog attempt parent coverage differs from its known manifest")
        if self.parent_coverage == "complete" and self.parents_sha256 != source_omission_sha256(
            [parent.model_dump(mode="json") for parent in self.parents]
        ):
            raise ValueError("catalog attempt complete manifest differs from its parents")
        values = self.model_dump(mode="json", exclude={"attempt_id", "recorded_at"})
        if self.attempt_id != "source-attempt-v1:" + source_omission_sha256(values):
            raise ValueError("catalog attempt identity differs from its retained evidence")
        if self.budget_bytes > MAX_CATALOG_BYTES:
            raise ValueError("catalog attempt exceeds its timestamp-reserved byte envelope")
        return self
