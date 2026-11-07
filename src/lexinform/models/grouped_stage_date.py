"""Typed operator input for a bounded grouped stage date correction."""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class GroupedStageDateCorrection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    root_publication_id: int = Field(gt=0, lt=2**63)
    member_publication_id: int = Field(gt=0, lt=2**63)
    status_change_id: int = Field(gt=0, lt=2**63)
    expected_content_id: str = Field(pattern=r"^content-v1:[0-9a-f]{64}$")
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_date: date
    corrected_date: date


class GroupedStageDateApproval(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    chat_id: int = Field(gt=-(2**52), lt=0)
    message_id: int = Field(gt=0, lt=2**63)


class GroupedStageDateCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    correction: GroupedStageDateCorrection
    approval: GroupedStageDateApproval | None = None
