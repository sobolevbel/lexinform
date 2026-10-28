"""Recorded source associations retain fingerprints without copying input or paid answers."""

import datetime as dt
import hashlib
import json
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lexinform.models.analysis import AnalysisRecord
from lexinform.models.enums import SourceKind


class ApplicabilitySource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str = Field(min_length=1)
    kind: SourceKind
    text_sha256: str | None = Field(
        default=None, min_length=1, exclude_if=lambda value: value is None
    )
    law_sha256: str | None = Field(
        default=None, min_length=1, exclude_if=lambda value: value is None
    )
    selection_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$", exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def known_fingerprint(self) -> Self:
        if not any((self.text_sha256, self.law_sha256, self.selection_sha256)):
            raise ValueError("a source association requires a recorded fingerprint")
        return self

    @classmethod
    def from_record(cls, record: AnalysisRecord) -> Self | None:
        if not record.source_url:
            return None
        selection = record.selection
        physical = any(
            part.sha256 or part.container_sha256
            for part in (*selection.parts, *selection.observed_parts)
        )
        selection_sha256 = None
        if physical or selection.normative_sha256:
            payload = selection.without_text().model_dump(mode="json", exclude={"scan_input"})
            encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            selection_sha256 = hashlib.sha256(encoded.encode()).hexdigest()
        if not any((record.text_sha256, record.law_sha256, selection_sha256)):
            return None
        return cls(
            url=record.source_url,
            kind=record.source_kind,
            text_sha256=record.text_sha256 or None,
            law_sha256=record.law_sha256 or None,
            selection_sha256=selection_sha256,
        )


class ResultApplicabilityObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    result_id: str = Field(pattern=r"^result-v1:[0-9a-f]{64}$")
    term: int = Field(gt=0)
    number: str = Field(min_length=1)
    source: ApplicabilitySource
    created_at: dt.datetime

    @model_validator(mode="after")
    def recording_time(self) -> Self:
        if self.created_at.utcoffset() is None:
            raise ValueError("the recording time requires a timezone")
        return self

    @property
    def observation_id(self) -> str:
        payload = self.model_dump(mode="json", exclude={"created_at"})
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return "applicability-v1:" + hashlib.sha256(encoded.encode()).hexdigest()

    @classmethod
    def from_record(
        cls, record: AnalysisRecord, *, term: int, number: str, now: dt.datetime
    ) -> Self | None:
        if record.computation_id is None:
            return None
        source = ApplicabilitySource.from_record(record)
        if source is None:
            return None
        return cls(
            result_id=record.computation_id, term=term, number=number, source=source, created_at=now
        )
