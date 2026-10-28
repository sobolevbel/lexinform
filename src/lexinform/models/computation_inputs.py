"""Frozen request scope is independent of the row receiving its result."""

from pydantic import BaseModel, ConfigDict, Field


class ComputationInputBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    term: int = Field(gt=0)
    number: str = Field(min_length=1)
    reuse_key: str = Field(min_length=1)
