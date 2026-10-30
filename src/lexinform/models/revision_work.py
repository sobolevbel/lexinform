"""Read frozen amendment application work without activating a producer or delivery."""

import hashlib
import json
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from lexinform.models.documents import DocumentWork

_LIFECYCLE = {"state", "next_at", "attempts", "problem", "revision_publication_id"}


class AmendmentRevisionApplicationWork(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["amendment_revision_application"] = "amendment_revision_application"
    schema_version: Literal[1] = 1
    term: int = Field(ge=1)
    number: str = Field(min_length=1, max_length=256)
    result_id: str = Field(pattern=r"^result-v1:[0-9a-f]{64}$")
    request_key: str = Field(min_length=1, max_length=256)
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    original_publication_id: int = Field(gt=0)
    channel_id: str = Field(min_length=1, max_length=256)
    message_id: int = Field(gt=0)
    source_url: str = Field(min_length=1, max_length=4096)
    bill_snapshot_id: str = Field(pattern=r"^content-snapshot-v1:[0-9a-f]{64}$")
    change_snapshot_id: str = Field(pattern=r"^content-snapshot-v1:[0-9a-f]{64}$")
    plan_snapshot_id: str = Field(pattern=r"^content-snapshot-v1:[0-9a-f]{64}$")
    created_at: AwareDatetime
    next_at: AwareDatetime
    state: Literal["waiting", "blocked", "complete"] = "waiting"
    attempts: int = Field(default=0, ge=0)
    problem: str = Field(default="", max_length=256)
    revision_publication_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def lifecycle(self) -> Self:
        if (self.state == "complete") != (self.revision_publication_id is not None):
            raise ValueError("only completed revision work names its publication")
        if self.next_at < self.created_at:
            raise ValueError("revision work cannot be scheduled before its creation")
        if len(self.model_dump_json().encode()) > 8192:
            raise ValueError("revision work exceeds its 8192-byte metadata limit")
        return self

    def frozen_values(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude=_LIFECYCLE)

    @property
    def key(self) -> str:
        raw = json.dumps([self.result_id, self.original_publication_id], separators=(",", ":"))
        return "amendment-revision-application-v1:" + hashlib.sha256(raw.encode()).hexdigest()


def decode_document_work(raw: str) -> DocumentWork | AmendmentRevisionApplicationWork:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("document work requires a JSON object")
    if "kind" not in value:
        return DocumentWork.model_validate(value)
    if value["kind"] == "amendment_revision_application":
        return AmendmentRevisionApplicationWork.model_validate(value)
    raise ValueError("unknown document work kind")
