"""Frozen physical slots of an editorial card revision."""

from datetime import date
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

from lexinform.models.sitting_content import SittingContentPart

MAX_MULTIPART_CARD_BYTES = 128 * 1024
MAX_MULTIPART_CARD_PARTS = 32


class MultipartCardSlot(SittingContentPart):
    edit_message_id: PositiveInt | None


class MultipartCardPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    channel_id: str
    ref_prefix: str = Field(pattern=r"^card/[1-9][0-9]*/[a-f0-9]{64}-[1-9][0-9]*$")
    today: date
    predecessor_slots: tuple[PositiveInt, ...] = Field(
        min_length=1, max_length=MAX_MULTIPART_CARD_PARTS
    )
    slots: tuple[MultipartCardSlot, ...] = Field(min_length=2, max_length=MAX_MULTIPART_CARD_PARTS)

    @property
    def bindings(self) -> tuple[int, ...]:
        return tuple(part.id for part in self.slots)

    @model_validator(mode="after")
    def topology(self) -> Self:
        count = len(self.predecessor_slots)
        if (
            count > len(self.slots)
            or len(set(self.predecessor_slots)) != count
            or len(set(self.bindings)) != len(self.slots)
            or set(self.predecessor_slots) & set(self.bindings)
            or any(slot.edit_message_id is None for slot in self.slots[:count])
            or any(slot.edit_message_id is not None for slot in self.slots[count:])
        ):
            raise ValueError("invalid multipart revision topology")
        return self
