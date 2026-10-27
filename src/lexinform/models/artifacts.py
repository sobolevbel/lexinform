"""Known complete-body identities do not imply retained bytes or a legal document version."""

import datetime as dt
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


class SelectionArtifactReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    selection_id: str = Field(pattern=r"^selection-v1:[0-9a-f]{64}$")
    part_scope: SelectionPartScope
    part_index: int = Field(ge=0)
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
