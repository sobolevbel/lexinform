"""Versioned references separate paid primary records from later observation metadata."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

PRIMARY_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)

PRIMARY_OBSERVATION_FIELDS = frozenset(
    {
        "source_checked_at",
        "source_url",
        "source_kind",
        "selection",
        "text_sha256",
        "law_sha256",
        "revision",
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    }
)


class PrimaryProjectionReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    modern_primary_ref: str = Field(pattern=r"^result-v1:[0-9a-f]{64}$")
    version: Literal[1] = 1
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_schema_version: Literal[1, 2]
    retained_record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observation: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def observed_fields(self) -> Self:
        if not self.observation.keys() <= PRIMARY_OBSERVATION_FIELDS:
            raise ValueError("a primary reference can override only observation/accounting fields")
        return self
