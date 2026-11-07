"""Bounded, body-free persisted ownership for a two-member grouped update."""

from datetime import date
from typing import Annotated, Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field

from lexinform.grouped_update_preview import (
    FragmentKind,
    GroupedUpdateRender,
    UpdateFragmentRange,
    valid_grouped_update_render,
)
from lexinform.telegram_text import MESSAGE_LIMIT

type Identifier = Annotated[int, Field(strict=True, gt=0, lt=2**63)]
type MemberIndex = Annotated[int, Field(strict=True, ge=0, le=1)]
type Offset = Annotated[int, Field(strict=True, ge=0, le=3 * MESSAGE_LIMIT)]


class GroupedRenderOwner(NamedTuple):
    term: Identifier
    number: Annotated[str, Field(min_length=1, max_length=64)]
    card_message_id: Identifier
    status_change_id: Identifier


class GroupedRenderRange(NamedTuple):
    kind: FragmentKind
    owners: Annotated[tuple[MemberIndex, ...], Field(max_length=2)]
    start: Offset
    end: Offset


class GroupedRenderWitness(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    profile: Literal["grouped-update-fragments-v1"] = "grouped-update-fragments-v1"
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    members: tuple[GroupedRenderOwner, GroupedRenderOwner]
    ranges: tuple[GroupedRenderRange, ...] = Field(min_length=3, max_length=12)
    today: date
    attention: bool = Field(strict=True)

    @classmethod
    def from_render(cls, render: GroupedUpdateRender) -> GroupedRenderWitness | None:
        owners = []
        for term, number, card, event in render.members:
            if card is None or event is None:
                return None
            owners.append(GroupedRenderOwner(term, number, card, event))
        first, second = owners
        return cls(
            text_sha256=render.sha256,
            members=(first, second),
            today=render.today,
            attention=render.attention,
            ranges=tuple(
                GroupedRenderRange(
                    span.kind,
                    tuple(render.members.index(owner) for owner in span.owners),
                    span.start,
                    span.end,
                )
                for span in render.ranges
            ),
        )

    def bind(self, text: str, language: str) -> GroupedUpdateRender:
        render = GroupedUpdateRender(
            text=text,
            members=self.members,
            ranges=tuple(
                UpdateFragmentRange(
                    span.kind,
                    tuple(self.members[index] for index in span.owners),
                    span.start,
                    span.end,
                )
                for span in self.ranges
            ),
            today=self.today,
            language=language,
            attention=self.attention,
        )
        if render.sha256 != self.text_sha256 or not valid_grouped_update_render(render):
            raise ValueError("Grouped render witness does not match its exact render and topology")
        return render
