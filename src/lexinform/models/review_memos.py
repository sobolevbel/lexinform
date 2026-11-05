"""Finite body-free review memo references preserve paid and projection metadata."""

import datetime as dt
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ReviewMemoProjection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    accounting: Literal["paid", "reuse"] = "paid"
    baseline_day: dt.date | None = None
    source_key: str = Field(default="", max_length=256)
    checked_at: dt.datetime | None = None
    source_fingerprint: str = Field(default="", max_length=256)
    canonical_dates: tuple[dt.date, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def representable_clock(self) -> Self:
        offset = self.checked_at.utcoffset() if self.checked_at is not None else None
        if offset is not None and offset.total_seconds() % 60:
            raise ValueError("unsupported review memo projection clock")
        return self


class ReviewMemoReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    modern_review_ref: str = Field(pattern=r"^result-v1:[0-9a-f]{64}$")
    version: Literal[1] = 1
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    retained_record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    projection: ReviewMemoProjection = Field(default_factory=ReviewMemoProjection)

    @field_validator("version", mode="before")
    @classmethod
    def exact_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("review memo version must be an integer")
        return value
