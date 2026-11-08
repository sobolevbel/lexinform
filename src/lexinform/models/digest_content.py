"""Bounded immutable digest input and exact ordered physical parts."""

import datetime as dt
import hashlib
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from lexinform.json_storage import canonical_json
from lexinform.models.digest import Digest, week_bounds
from lexinform.models.physical_inventory import Sha256
from lexinform.packed_content import MAX_PACKED_BASE64_CHARS, unpack_content
from lexinform.telegram_text import MESSAGE_LIMIT, utf16_length

MAX_DIGEST_CONTENT_BYTES = 65_536

type DigestRenderedProfile = Literal["digest-rendered-v1", "digest-rendered-semantic-v1"]
type DigestHistoryProfile = Literal[
    "digest-content-v1", "digest-rendered-v1", "digest-rendered-semantic-v1"
]
RENDERED_DIGEST_PROFILES = ("digest-rendered-v1", "digest-rendered-semantic-v1")
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


def digest_event_owner_sha256(owners: list[tuple[int, int, str, str]]) -> str:
    facts: list[JsonValue] = [
        [identity, term, number, kind] for identity, term, number, kind in owners
    ]
    return hashlib.sha256(canonical_json(facts).encode()).hexdigest()


class DigestRenderedPart(DigestContentPart):
    profile: Literal["digest-rendered-part-v1"] = "digest-rendered-part-v1"
    event_owner_sha256: Sha256

    @model_validator(mode="after")
    def serialized_bound(self) -> Self:
        if len(self.model_dump_json().encode()) > 32_768:
            raise ValueError("digest rendered part exceeds its serialized byte bound")
        return self


class DigestRenderedPartRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    publication_id: DigestSqlId
    snapshot_id: str = Field(pattern=r"^content-snapshot-v1:[0-9a-f]{64}$")


class DigestRenderedPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: DigestRenderedProfile = "digest-rendered-v1"
    channel_id: str = Field(min_length=1, max_length=256)
    delivery_ref: str = Field(min_length=1, max_length=128)
    week_ref: str = Field(pattern=r"^[0-9]{4}-W[0-9]{2}$")
    since: dt.date
    until: dt.date
    upstream_semantics: Literal["not_retained", "retained"] = "not_retained"
    upstream_semantic_reason: Literal["aggregate_budget"] = "aggregate_budget"
    upstream_digest_sha256: Sha256
    upstream_digest_zlib: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_PACKED_BASE64_CHARS,
        exclude_if=lambda value: value is None,
    )
    parts: tuple[DigestRenderedPartRef, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def week_and_parts_agree(self) -> Self:
        semantic = self.profile == "digest-rendered-semantic-v1"
        if semantic != (self.upstream_semantics == "retained") or semantic != (
            self.upstream_digest_zlib is not None
        ):
            raise ValueError("digest rendered profile differs from its retained semantics")
        draft_of = self.delivery_ref.removeprefix(self.week_ref + ":draft:")
        if (self.since, self.until) != week_bounds(self.week_ref) or (
            self.delivery_ref != self.week_ref
            and (
                draft_of == self.delivery_ref
                or not draft_of.isascii()
                or not draft_of.isdecimal()
                or draft_of.startswith("0")
                or int(draft_of) >= 2**63
            )
        ):
            raise ValueError("digest rendered history requires its exact week and delivery ref")
        if len({part.publication_id for part in self.parts}) != len(self.parts) or len(
            {part.snapshot_id for part in self.parts}
        ) != len(self.parts):
            raise ValueError("digest rendered history requires distinct ordered parts")
        return self

    def retained_digest(self) -> Digest | None:
        packed = self.upstream_digest_zlib
        if packed is None:
            return None
        try:
            raw = unpack_content(packed, self.upstream_digest_sha256)
            digest = Digest.model_validate_json(raw)
        except ValueError as exc:
            raise ValueError("invalid digest compressed semantics") from exc
        if digest.model_dump_json().encode() != raw or (digest.ref, digest.since, digest.until) != (
            self.week_ref,
            self.since,
            self.until,
        ):
            raise ValueError("digest compressed semantics differs from its recorded week")
        return digest
