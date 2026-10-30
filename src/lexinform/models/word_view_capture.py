"""Transient Word view status binds the actual legacy body and note manifests."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lexinform.models.documents import WordDetailedExtraction
from lexinform.models.extractions import WordViewStatus


class WordViewExtraction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    legacy: WordDetailedExtraction
    status: WordViewStatus | None

    @model_validator(mode="after")
    def finished_matches_known_legacy_walk(self) -> Self:
        if (
            self.status is not None
            and self.status.known_walk == "finished"
            and (
                self.legacy.body.body_state != "read"
                or any(part.state != "read" for part in self.legacy.notes.parts)
            )
        ):
            raise ValueError("Finished Word view status requires its observed legacy walk")
        return self


class WordPartViewStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    part_index: int = Field(strict=True, ge=0)
    input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    body_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    notes_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: WordViewStatus
