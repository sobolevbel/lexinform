"""Editorial confirmation and frozen-context checks shared by writers and inspectors."""

from collections.abc import Mapping, Set
from dataclasses import dataclass
from typing import Literal

from lexinform.models.content import ContentSnapshot, ContentVersion, editorial_bill_snapshot
from lexinform.models.observations import DeliveryPlan


@dataclass(frozen=True)
class ContentConfirmation:
    current_id: str | None
    ambiguous: bool = False


def confirmed_content_version(
    parents: Mapping[str, str | None], confirmed: Set[str]
) -> ContentConfirmation:
    eligible: set[str] = set()
    for identity in confirmed:
        seen: set[str] = set()
        current: str | None = identity
        while (
            current is not None
            and current in parents
            and current in confirmed
            and current not in seen
        ):
            seen.add(current)
            current = parents[current]
        if current is None:
            eligible.add(identity)
    previous = {parents[identity] for identity in eligible}
    leaves = eligible - previous
    return ContentConfirmation(next(iter(leaves)) if len(leaves) == 1 else None, len(leaves) > 1)


ContentMismatch = Literal["text", "body", "context", "snapshot"]


def content_delivery_mismatch(
    version: ContentVersion,
    snapshots: Mapping[str, ContentSnapshot],
    plan: DeliveryPlan,
) -> ContentMismatch | None:
    if plan.rendered_text != (version.rendered_text or ""):
        return "text"
    for identity, kind, expected in (
        (version.bill_snapshot_id, "bill", editorial_bill_snapshot(plan.bill_json).payload_json),
        (version.change_snapshot_id, "change", plan.change_json),
    ):
        saved = snapshots.get(identity)
        if saved is None or saved.kind != kind:
            return "snapshot"
        if saved.payload_json != expected:
            return "body"
    saved_plan = snapshots.get(version.plan_snapshot_id)
    if saved_plan is None or saved_plan.kind != "plan":
        return "snapshot"
    semantic = plan.model_copy(update={"bill_json": "", "change_json": "", "rendered_text": ""})
    if DeliveryPlan.model_validate_json(saved_plan.payload_json) != semantic:
        return "context"
    return None
