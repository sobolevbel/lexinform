"""Editorial versions outlive operational delivery plans and reuse exact semantic snapshots."""

import hashlib
import json
import re
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, PositiveInt, model_validator


class ContentSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["bill", "change", "plan"]
    payload_json: str

    @model_validator(mode="after")
    def json_object(self) -> Self:
        payload = json.loads(self.payload_json)
        if not isinstance(payload, dict):
            raise ValueError("a content snapshot requires a JSON object")
        if self.kind == "plan" and any(
            payload.get(field) for field in ("bill_json", "change_json", "rendered_text")
        ):
            raise ValueError("semantic plan snapshots reference their bodies separately")
        return self

    @property
    def snapshot_id(self) -> str:
        encoded = self.kind + "\n" + self.payload_json
        return "content-snapshot-v1:" + hashlib.sha256(encoded.encode()).hexdigest()


def editorial_bill_snapshot(raw: str) -> ContentSnapshot:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("a content snapshot requires a JSON object")
    if payload.get("review_work") or payload.get("ready_analysis"):
        payload.pop("review_work", None)
        payload.pop("ready_analysis", None)
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return ContentSnapshot(kind="bill", payload_json=raw)


class ContentVersion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    root_publication_id: int = Field(gt=0)
    previous_version_id: str | None = Field(default=None, pattern=r"^content-v1:[0-9a-f]{64}$")
    reason: Literal["original", "amendments_revision"]
    language: str | None = Field(default=None, min_length=1)
    bill_snapshot_id: str = Field(pattern=r"^content-snapshot-v1:[0-9a-f]{64}$")
    change_snapshot_id: str = Field(pattern=r"^content-snapshot-v1:[0-9a-f]{64}$")
    plan_snapshot_id: str = Field(pattern=r"^content-snapshot-v1:[0-9a-f]{64}$")
    rendered_text: str | None = Field(default=None, min_length=1)
    formatter_version: str | None = Field(default=None, min_length=1)
    source_observation_ids: tuple[str, ...] = ()
    computation_ids: tuple[str, ...] = ()
    application_ids: tuple[PositiveInt, ...] = ()
    status_change_id: PositiveInt | None = None
    recorded_at: AwareDatetime

    @model_validator(mode="after")
    def predecessor(self) -> Self:
        if (self.reason == "original") != (self.previous_version_id is None):
            raise ValueError("an original has no predecessor; a revision requires one")
        return self

    @property
    def content_id(self) -> str:
        payload = self.model_dump(mode="json", exclude={"recorded_at"})
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return "content-v1:" + hashlib.sha256(encoded.encode()).hexdigest()


class ContentVersionSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    content_id: str
    root_publication_id: int
    previous_version_id: str | None
    reason: Literal["original", "amendments_revision"]
    language: str | None
    bill_snapshot_id: str
    change_snapshot_id: str
    plan_snapshot_id: str
    formatter_version: str | None
    source_observation_ids: tuple[str, ...]
    computation_ids: tuple[str, ...]
    application_ids: tuple[int, ...]
    status_change_id: int | None
    recorded_at: AwareDatetime
    rendered_text_known: bool
    rendered_utf8_bytes: int | None
    rendered_sha256: str | None
    source_observation_ids_opaque_count: int = 0
    computation_ids_opaque_count: int = 0
    unrecognized_labels: tuple[str, ...] = ()

    @classmethod
    def from_version(cls, version: ContentVersion) -> Self:
        source_ids = tuple(
            value
            for value in version.source_observation_ids
            if re.fullmatch(r"source-observation-v1:[0-9a-f]{64}", value)
        )
        result_ids = tuple(
            value
            for value in version.computation_ids
            if re.fullmatch(r"result-v1:[0-9a-f]{64}", value)
        )
        unknown = tuple(
            name
            for name, value in (
                ("language", version.language),
                ("formatter_version", version.formatter_version),
            )
            if value is not None and not re.fullmatch(r"[A-Za-z0-9_.:+-]{1,128}", value)
        )
        text = version.rendered_text.encode() if version.rendered_text is not None else None
        return cls(
            content_id=version.content_id,
            root_publication_id=version.root_publication_id,
            previous_version_id=version.previous_version_id,
            reason=version.reason,
            language=None if "language" in unknown else version.language,
            bill_snapshot_id=version.bill_snapshot_id,
            change_snapshot_id=version.change_snapshot_id,
            plan_snapshot_id=version.plan_snapshot_id,
            formatter_version=None if "formatter_version" in unknown else version.formatter_version,
            source_observation_ids=source_ids,
            computation_ids=result_ids,
            application_ids=version.application_ids,
            status_change_id=version.status_change_id,
            recorded_at=version.recorded_at,
            rendered_text_known=text is not None,
            rendered_utf8_bytes=len(text) if text is not None else None,
            rendered_sha256=hashlib.sha256(text).hexdigest() if text is not None else None,
            source_observation_ids_opaque_count=len(version.source_observation_ids)
            - len(source_ids),
            computation_ids_opaque_count=len(version.computation_ids) - len(result_ids),
            unrecognized_labels=unknown,
        )


class ContentHistoryView(BaseModel):
    model_config = ConfigDict(frozen=True)

    root_publication_id: int | None = None
    version_count: int | None = None
    summaries: tuple[ContentVersionSummary, ...] = ()
    inspected_id: str | None = None
    original_id: str | None = None
    current_id: str | None = None
    integrity: Literal["valid", "unavailable"] = "valid"
    ambiguous: bool = False

    def _summary(self, identity: str | None) -> ContentVersionSummary | None:
        return next((summary for summary in self.summaries if summary.content_id == identity), None)

    @property
    def inspected(self) -> ContentVersionSummary | None:
        return self._summary(self.inspected_id)

    @property
    def original(self) -> ContentVersionSummary | None:
        return self._summary(self.original_id)

    @property
    def current(self) -> ContentVersionSummary | None:
        return self._summary(self.current_id)
