"""Pin raw replay populations to source bytes and an explicit corpus index."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RawReplayInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    official_ids: tuple[str, ...] = Field(min_length=1)
    cohorts: tuple[str, ...] = Field(min_length=1)


class RawReplayManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1]
    index_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    cases: tuple[RawReplayInput, ...] = Field(min_length=1)
    exclusions: tuple[str, ...] = Field(min_length=1)
