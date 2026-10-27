"""Known complete-body identities do not imply retained bytes or a legal document version."""

import datetime as dt
import hashlib
import json
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

SelectionPartScope = Literal["parts", "observed_parts"]


class ArtifactIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_size: int = Field(ge=0)


class ArtifactMetadata(ArtifactIdentity):
    recorded_at: dt.datetime

    @model_validator(mode="after")
    def recording_time(self) -> Self:
        if self.recorded_at.utcoffset() is None:
            raise ValueError("artifact recording time requires a timezone")
        return self


class ArtifactReadIdentity(ArtifactIdentity):
    requested_url: str = Field(min_length=1)

    @property
    def read_id(self) -> str:
        payload = self.model_dump(mode="json", exclude={"recorded_at"})
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "artifact-read-v1:" + hashlib.sha256(canonical.encode()).hexdigest()


class ArtifactReadReceipt(ArtifactReadIdentity):
    recorded_at: dt.datetime

    @model_validator(mode="after")
    def recording_time(self) -> Self:
        if self.recorded_at.utcoffset() is None:
            raise ValueError("artifact read recording time requires a timezone")
        return self


class SelectionArtifactReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    selection_id: str = Field(pattern=r"^selection-v1:[0-9a-f]{64}$")
    part_scope: SelectionPartScope
    part_index: int = Field(ge=0)
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
