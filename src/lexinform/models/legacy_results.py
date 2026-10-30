"""Exact legacy values and observed locations without inferred computation provenance."""

import hashlib
from datetime import datetime
from typing import Self, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from lexinform.json_storage import canonical_json, read_json

LEGACY_ANSWER_FIELDS = frozenset(
    {"analysis", "amendments", "digest", "comparison", "proposal", "review"}
)
LEGACY_REFERENCE_FIELDS = frozenset(
    {"legacy_result_ref", "projection_overrides", "projection_removed", "projection_upcast"}
)


def legacy_json(raw: str) -> JsonValue:
    return read_json(raw)


def canonical_legacy_json(value: JsonValue) -> str:
    return canonical_json(value)


def has_legacy_marker(value: JsonValue) -> bool:
    if isinstance(value, dict):
        return bool(LEGACY_REFERENCE_FIELDS.intersection(value)) or any(
            has_legacy_marker(child) for child in value.values()
        )
    return isinstance(value, list) and any(has_legacy_marker(child) for child in value)


class LegacyResultValue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    value_json: str

    @property
    def value_id(self) -> str:
        return "legacy-value-v1:" + hashlib.sha256(self.value_json.encode()).hexdigest()

    @model_validator(mode="after")
    def exact_record(self) -> Self:
        value = legacy_json(self.value_json)
        if (
            not isinstance(value, dict)
            or not {"model", "prompt_version", "created_at"} <= value.keys()
        ):
            raise ValueError("a legacy value requires its saved record metadata")
        if not LEGACY_ANSWER_FIELDS.intersection(value):
            raise ValueError("a legacy value requires a saved answer field")
        if value.get("computation_id") is not None:
            raise ValueError("a recorded computation reference is not a legacy import")
        if has_legacy_marker(value):
            raise ValueError("an immutable legacy value cannot contain projection references")
        if canonical_legacy_json(value) != self.value_json:
            raise ValueError("a legacy value must use canonical exact JSON")
        return self


class LegacyResultOccurrence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    value_id: str = Field(pattern=r"^legacy-value-v1:[0-9a-f]{64}$")
    source_table: str = Field(min_length=1)
    source_key_json: str
    source_column: str = Field(min_length=1)
    source_path: str
    container_term: int | None = None
    container_number: str | None = None
    purpose: str | None = None

    @model_validator(mode="after")
    def locator(self) -> Self:
        key = legacy_json(self.source_key_json)
        if (
            not isinstance(key, list)
            or not key
            or any(not isinstance(part, (str, int)) or isinstance(part, bool) for part in key)
            or canonical_legacy_json(key) != self.source_key_json
        ):
            raise ValueError("a source key requires a nonempty canonical JSON array")
        if (self.container_term is None) != (self.container_number is None):
            raise ValueError("a known containing owner requires both term and number")
        return self


class LegacyResultImport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    imported_at: datetime
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    values: tuple[LegacyResultValue, ...]
    occurrences: tuple[LegacyResultOccurrence, ...]

    @property
    def manifest_sha256(self) -> str:
        identifiers: list[JsonValue] = [value.value_id for value in self.values]
        occurrences: list[JsonValue] = [
            canonical_legacy_json(cast(JsonValue, item.model_dump(mode="json")))
            for item in self.occurrences
        ]
        manifest: JsonValue = {
            "source_sha256": self.source_sha256,
            "values": sorted(identifiers, key=str),
            "occurrences": sorted(occurrences, key=str),
        }
        return hashlib.sha256(canonical_legacy_json(manifest).encode()).hexdigest()

    @model_validator(mode="after")
    def complete_manifest(self) -> Self:
        if not self.values:
            raise ValueError("a legacy import requires at least one observed value")
        identifiers = {value.value_id for value in self.values}
        if len(identifiers) != len(self.values):
            raise ValueError("an import must deduplicate its exact values")
        if {item.value_id for item in self.occurrences} != identifiers:
            raise ValueError("every imported value needs its observed location")
        locations = {
            (
                item.source_table,
                item.source_key_json,
                item.source_column,
                item.source_path,
            )
            for item in self.occurrences
        }
        if len(locations) != len(self.occurrences):
            raise ValueError("an import must deduplicate its occurrences")
        return self
