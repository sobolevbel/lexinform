"""Frozen semantic context and ordered physical parts of an original direct publication."""

from typing import Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

from lexinform.models.enums import PublicationKind
from lexinform.models.observations import DeliveryPlan
from lexinform.models.phases import Phase
from lexinform.models.sejm import Stage
from lexinform.models.sitting_content import SittingContentPart

MAX_ORIGINAL_DELIVERY_BYTES = 128 * 1024
MAX_ORIGINAL_DELIVERY_PARTS = 32

ReviewNoticeKind = Literal[PublicationKind.CONSULTATION_REVIEW, PublicationKind.COMMISSION_REVIEW]
REVIEW_NOTICE_KINDS: frozenset[PublicationKind] = frozenset(get_args(ReviewNoticeKind))

OriginalNoticeKind = Literal[
    PublicationKind.ACT_PUBLISHED,
    PublicationKind.CONSULTATION_RESULTS,
    PublicationKind.IN_FORCE,
    PublicationKind.CONSULTATION_DEADLINE,
    PublicationKind.DECISION_DEADLINE,
    PublicationKind.HEARING_DEADLINE,
]
ORIGINAL_NOTICE_KINDS: frozenset[PublicationKind] = frozenset(get_args(OriginalNoticeKind))
OriginalPublicationKind = Literal[
    OriginalNoticeKind,
    ReviewNoticeKind,
    PublicationKind.NEW_BILL,
    PublicationKind.JOINT_BILL,
]
ORIGINAL_CARD_KINDS = frozenset((PublicationKind.NEW_BILL, PublicationKind.JOINT_BILL))
ORIGINAL_DELIVERY_KINDS = ORIGINAL_NOTICE_KINDS | ORIGINAL_CARD_KINDS | REVIEW_NOTICE_KINDS
DATED_NOTICES = frozenset(
    (
        PublicationKind.IN_FORCE,
        PublicationKind.CONSULTATION_DEADLINE,
        PublicationKind.DECISION_DEADLINE,
        PublicationKind.HEARING_DEADLINE,
    )
)


class JointPrimaryEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    publication_id: PositiveInt
    bill_snapshot_id: str = Field(pattern=r"^content-snapshot-v1:[0-9a-f]{64}$")
    packed_bill: Literal[True] | None = Field(default=None, exclude_if=lambda value: value is None)


class OriginalDeliveryEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: OriginalPublicationKind
    bill_context: Literal["editorial-notice-v1", "editorial-card-original-v1"]
    channel_id: str = Field(min_length=1, max_length=256)
    ref: str | None = Field(default=None, max_length=512)
    packed_bill: Literal[True] | None = Field(default=None, exclude_if=lambda value: value is None)
    joint_primary: JointPrimaryEvidence | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    commission_review_application_id: int | None = Field(
        default=None, gt=0, lt=2**63, strict=True, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def topology(self) -> Self:
        if (self.kind is PublicationKind.JOINT_BILL) != (self.joint_primary is not None) or (
            self.kind in ORIGINAL_CARD_KINDS
        ) != (self.bill_context == "editorial-card-original-v1"):
            raise ValueError("original publication and semantic context differ")
        if (
            self.commission_review_application_id is not None
            and self.kind is not PublicationKind.COMMISSION_REVIEW
        ):
            raise ValueError("commission review application requires its own notice")
        return self


class OriginalDeliveryPart(SittingContentPart):
    held_change_ids: tuple[PositiveInt, ...] = Field(default=(), exclude_if=lambda value: not value)


def original_delivery_context(plan: DeliveryPlan, *, joint: bool = False) -> DeliveryPlan:
    update: dict[str, object] = {"bill_json": "", "rendered_text": ""}
    if joint:
        update["primary_json"] = None
    return plan.model_copy(update=update)


def _original_direct_context_matches(plan: DeliveryPlan) -> bool:
    return (
        not plan.bill_json
        and not plan.change_json
        and not plan.rendered_text
        and not plan.group_text
        and not plan.publication_ids
        and plan.continuation_root is None
        and plan.continuation_parent is None
        and plan.continuation_index == 1
        and plan.edit_message_id is None
        and plan.revision_of is None
        and plan.absorbed_by is None
        and plan.destination == "direct"
        and not plan.digest_event_ids
        and not plan.digest_parts
        and not plan.sitting_group
        and not plan.update_group
        and not plan.digest_ref
        and not plan.approve_label
    )


def original_notice_context_matches(plan: DeliveryPlan) -> bool:
    return (
        _original_direct_context_matches(plan) and plan.reply_to is not None and plan.reply_to > 0
    )


class OriginalDeliveryPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    root: DeliveryPlan
    parts: tuple[OriginalDeliveryPart, ...] = Field(
        min_length=1, max_length=MAX_ORIGINAL_DELIVERY_PARTS
    )

    @property
    def bindings(self) -> tuple[int, ...]:
        return tuple(part.id for part in self.parts)

    @model_validator(mode="after")
    def physical_order(self) -> Self:
        if (
            len(set(self.bindings)) != len(self.parts)
            or self.root.continuation_ids != self.bindings[1:]
            or self.root.disable_notification != self.parts[0].disable_notification
            or (len(self.parts) > 1 and self.root.held_change_ids)
            or self.parts[0].held_change_ids
            or any(part.held_change_ids for part in self.parts[1:-1])
            or not _original_direct_context_matches(self.root)
        ):
            raise ValueError("invalid original delivery topology")
        return self

    def validate_original(self, kind: PublicationKind) -> None:
        if kind is PublicationKind.NEW_BILL:
            if self.root.reply_to is not None or self.root.primary_json or len(self.parts) < 2:
                raise ValueError("original multipart card has invalid topology")
            return
        if kind is PublicationKind.JOINT_BILL:
            if self.root.primary_json or not original_notice_context_matches(self.root):
                raise ValueError("joint reply requires its original card target")
            return
        if (
            kind not in ORIGINAL_NOTICE_KINDS | REVIEW_NOTICE_KINDS
            or not original_notice_context_matches(self.root)
        ):
            raise ValueError("unsupported original notice")
        if kind in DATED_NOTICES | REVIEW_NOTICE_KINDS and self.root.today is None:
            raise ValueError("dated notice requires its frozen date")
        if kind is PublicationKind.DECISION_DEADLINE:
            Phase.model_validate_json(self.root.phase_json or "")
        if kind is PublicationKind.HEARING_DEADLINE:
            Stage.model_validate_json(self.root.hearing_json or "")
