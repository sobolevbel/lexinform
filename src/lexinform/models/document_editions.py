"""Candidate normative-set identities are separate from question admission and paid inputs."""

import datetime as dt
import hashlib
import json
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_EDITION_METADATA_BYTES = 1024


class DocumentEdition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    contract: Literal["candidate-normative-set-v1"] = "candidate-normative-set-v1"
    membership_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    member_count: int = Field(ge=1)
    recorded_at: dt.datetime

    @property
    def edition_id(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json", exclude={"recorded_at"}),
            sort_keys=True,
            separators=(",", ":"),
        )
        return "edition-v1:" + hashlib.sha256(canonical.encode()).hexdigest()

    @model_validator(mode="after")
    def bounded_metadata(self) -> Self:
        if self.recorded_at.utcoffset() is None:
            raise ValueError("edition recording time requires a timezone")
        if len(self.model_dump_json().encode()) > MAX_EDITION_METADATA_BYTES:
            raise ValueError("edition metadata exceeds its serialized byte limit")
        return self


class SelectionEditionReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    selection_id: str = Field(pattern=r"^selection-v1:[0-9a-f]{64}$")
    edition_id: str = Field(pattern=r"^edition-v1:[0-9a-f]{64}$")
