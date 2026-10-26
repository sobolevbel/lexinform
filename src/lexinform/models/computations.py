"""Immutable primary batch results and their collection/projection decisions."""

import datetime as dt
import hashlib
import json
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lexinform.models.analysis import AnalysisRecord, TokenUsage
from lexinform.models.batch import BatchProvider
from lexinform.models.enums import BillStatus

CollectionReason = Literal[
    "current_generation",
    "generation_changed",
    "inactive_status",
    "bill_missing",
    "no_validated_result",
]
ApplicationReason = CollectionReason | Literal["batch_result", "memo_reuse"]


def computation_id(provider: BatchProvider, batch_id: str, custom_id: str) -> str:
    identity = json.dumps([provider, batch_id, custom_id], separators=(",", ":"))
    return "result-v1:" + hashlib.sha256(identity.encode()).hexdigest()


class ComputationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    provider: BatchProvider
    batch_id: str
    custom_id: str
    purpose: Literal["analysis", "reanalysis"]
    term: int
    number: str
    generation: int
    collected_at: dt.datetime
    request_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$", exclude_if=lambda value: value is None
    )
    validation: Literal["validated", "error"]
    record: AnalysisRecord | None
    model: str | None
    usage: TokenUsage
    error: str | None = None
    context_exceeded: bool = False

    @property
    def result_id(self) -> str:
        return computation_id(self.provider, self.batch_id, self.custom_id)

    @model_validator(mode="after")
    def validated_record(self) -> Self:
        if (self.record is not None) != (self.validation == "validated"):
            raise ValueError("validation must describe the retained record")
        if self.record is not None and self.record.computation_id != self.result_id:
            raise ValueError("the retained record must refer to its computation")
        if self.record is not None and self.record.request_sha256 != self.request_sha256:
            raise ValueError("the retained record must refer to its original request")
        return self


class ResultApplication(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    result_id: str
    term: int
    number: str
    generation: int
    phase: Literal["collection", "projection"] = "collection"
    outcome: Literal["ready", "superseded", "failed", "applied"]
    reason: ApplicationReason
    observed_generation: int | None
    observed_status: BillStatus | None
    created_at: dt.datetime

    @model_validator(mode="after")
    def application_phase(self) -> Self:
        projection = self.phase == "projection"
        if projection != (self.outcome == "applied"):
            raise ValueError("only a projection receipt confirms application")
        if projection != (self.reason in ("batch_result", "memo_reuse")):
            raise ValueError("the application reason must describe its phase")
        return self
