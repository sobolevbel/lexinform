"""Immutable logical members and physical HTML of one sitting delivery."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

from lexinform.models.observations import DeliveryPlan
from lexinform.telegram_text import MESSAGE_LIMIT, utf16_length

MAX_SITTING_HISTORY_BYTES = 128 * 1024
MAX_SITTING_HISTORY_ROWS = 32
PLAN_BODIES = frozenset(("bill_json", "rendered_text", "group_text"))


class SittingContentMember(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: PositiveInt
    term: int
    number: str
    kind: Literal["agenda", "agenda_cancelled"]
    ref: str
    bill_snapshot_id: str
    plan: DeliveryPlan

    @model_validator(mode="after")
    def validate_plan(self) -> Self:
        if any(getattr(self.plan, field) for field in PLAN_BODIES):
            raise ValueError("sitting member plan references separate bodies")
        return self


def sitting_content_context(plan: DeliveryPlan) -> DeliveryPlan:
    return plan.model_copy(update=dict.fromkeys(PLAN_BODIES, ""))


class SittingContentPart(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: PositiveInt
    html: str
    disable_notification: bool

    @model_validator(mode="after")
    def bounded_html(self) -> Self:
        if not self.html.strip() or utf16_length(self.html) > MESSAGE_LIMIT:
            raise ValueError("invalid physical sitting HTML")
        return self


class SittingContentPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    channel_id: str
    members: tuple[SittingContentMember, ...] = Field(
        min_length=1, max_length=MAX_SITTING_HISTORY_ROWS
    )
    parts: tuple[SittingContentPart, ...] = Field(min_length=1, max_length=MAX_SITTING_HISTORY_ROWS)

    @property
    def bindings(self) -> tuple[int, ...]:
        return tuple(member.id for member in self.members) + tuple(
            part.id for part in self.parts[1:]
        )

    @model_validator(mode="after")
    def topology(self) -> Self:
        root = self.members[0]
        physical = tuple(part.id for part in self.parts)
        logical = tuple(member.id for member in self.members)
        if (
            root.id != physical[0]
            or len(set(self.bindings)) != len(self.bindings)
            or (len(logical) > 1 and len(physical) > 1)
            or len(self.model_dump_json().encode()) > MAX_SITTING_HISTORY_BYTES
        ):
            raise ValueError("invalid sitting history topology")
        for member in self.members:
            plan = member.plan
            if (
                plan.sitting_group != (logical if plan.sitting_group else ())
                or (len(logical) > 1 and not plan.sitting_group)
                or plan.continuation_ids != physical[1:]
                or (plan.sitting_group and len(physical) > 1)
                or plan.render_origin is not None
                or plan.edit_message_id is not None
                or plan.revision_of is not None
                or plan.continuation_root is not None
                or plan.continuation_parent is not None
                or plan.destination != "direct"
            ):
                raise ValueError("invalid sitting member plan topology")
        return self
