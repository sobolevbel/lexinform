"""Bounded immutable digest input and exact ordered physical parts."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lexinform.models.digest import Digest
from lexinform.telegram_text import MESSAGE_LIMIT, utf16_length

MAX_DIGEST_CONTENT_BYTES = 65_536
type DigestSqlId = Annotated[int, Field(gt=0, lt=2**63, strict=True)]


class DigestContentBudgetError(ValueError):
    pass


class DigestContentPart(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    publication_id: DigestSqlId
    text: str = Field(min_length=1)
    approve_label: str = Field(default="", max_length=512)
    event_ids: tuple[DigestSqlId, ...] = Field(default=(), max_length=128)

    @model_validator(mode="after")
    def telegram_bound(self) -> Self:
        if utf16_length(self.text) > MESSAGE_LIMIT or len(set(self.event_ids)) != len(
            self.event_ids
        ):
            raise ValueError("digest part exceeds Telegram or event bounds")
        return self


class DigestContentPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: Literal["digest-content-v1"] = "digest-content-v1"
    channel_id: str = Field(min_length=1, max_length=256)
    delivery_ref: str = Field(min_length=1, max_length=128)
    digest: Digest
    parts: tuple[DigestContentPart, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def distinct_members(self) -> Self:
        ids = [part.publication_id for part in self.parts]
        events = [event for part in self.parts for event in part.event_ids]
        if len(set(ids)) != len(ids) or len(set(events)) != len(events) or set(ids) & set(events):
            raise ValueError("digest requires distinct physical parts and event owners")
        supplied = {
            identity
            for entry in (*self.digest.cards, *self.digest.updates, *self.digest.highlights)
            for identity in entry.source_publication_ids
        }
        if not set(events) <= supplied:
            raise ValueError("digest events require their exact frozen semantic entries")
        if len(self.model_dump_json().encode()) > MAX_DIGEST_CONTENT_BYTES:
            raise DigestContentBudgetError("digest immutable content exceeds its aggregate bound")
        return self
