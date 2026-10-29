"""Immutable source decisions keep metadata independently of paid results and raw retention."""

import datetime as dt
import hashlib
import json
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lexinform.models.documents import DocumentSelection
from lexinform.models.sejm import TextDocument


def _identity(namespace: str, payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return namespace + hashlib.sha256(encoded.encode()).hexdigest()


class DocumentSelectionRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    selection: DocumentSelection

    @model_validator(mode="after")
    def metadata_only(self) -> Self:
        if any(part.text for part in (*self.selection.parts, *self.selection.observed_parts)):
            raise ValueError("a source decision stores metadata, not extracted bodies")
        if self.selection.scan_input is not None:
            raise ValueError("a source decision is separate from the later provider input")
        return self

    @classmethod
    def from_selection(cls, selection: DocumentSelection) -> Self:
        return cls(selection=selection.without_text().model_copy(update={"scan_input": None}))

    @property
    def selection_id(self) -> str:
        return _identity("selection-v1:", self.model_dump(mode="json"))


class SourceObservationIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    term: int = Field(gt=0)
    number: str = Field(min_length=1)
    purpose: Literal["prefilter", "analysis", "review"]
    document: TextDocument
    selection_id: str = Field(pattern=r"^selection-v1:[0-9a-f]{64}$")

    @property
    def observation_id(self) -> str:
        return _identity(
            "source-observation-v1:", self.model_dump(mode="json", exclude={"recorded_at"})
        )


class SourceObservation(SourceObservationIdentity):
    recorded_at: dt.datetime

    @model_validator(mode="after")
    def recording_time(self) -> Self:
        if self.recorded_at.utcoffset() is None:
            raise ValueError("the recording time requires a timezone")
        return self
