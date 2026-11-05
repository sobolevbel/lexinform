"""Editorial confirmation and frozen-context checks shared by writers and inspectors."""

import re
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from typing import Literal

from lexinform.models.bill import Publication
from lexinform.models.content import (
    ContentSnapshot,
    ContentVersion,
    card_bill_snapshot,
    editorial_bill_snapshot,
)
from lexinform.models.enums import PublicationKind, PublicationStatus
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
    if version.change_snapshot_id is None and plan.change_json:
        return "body"
    bill = (
        card_bill_snapshot(plan.bill_json)
        if version.card_context_profile == "continuation-v1"
        else editorial_bill_snapshot(plan.bill_json)
    )
    for identity, kind, expected in (
        (version.bill_snapshot_id, "bill", bill.payload_json),
        (version.change_snapshot_id, "change", plan.change_json),
    ):
        if identity is None:
            continue
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


def content_snapshot_refs(version: ContentVersion) -> tuple[tuple[str, str], ...]:
    return tuple(
        (kind, identity)
        for kind, identity in (
            ("bill", version.bill_snapshot_id),
            ("change", version.change_snapshot_id),
            ("plan", version.plan_snapshot_id),
        )
        if identity is not None
    )


def card_plan_matches(version: ContentVersion, plan: DeliveryPlan) -> bool:
    if (
        plan.destination != "direct"
        or plan.change_json
        or plan.primary_json
        or plan.publication_ids
        or plan.continuation_ids
        or plan.continuation_root is not None
        or plan.absorbed_by is not None
        or plan.digest_ref
        or plan.digest_parts
        or plan.digest_event_ids
        or plan.continuation_index != 1
    ):
        return False
    if version.previous_version_id is None:
        return (
            plan.revision_of is None
            and plan.edit_message_id is None
            and plan.continuation_parent is None
        )
    return (
        version.reason == "card_revision"
        and plan.revision_of == version.root_publication_id
        and plan.edit_message_id is not None
        and plan.continuation_parent == version.root_publication_id
        and plan.reply_to == plan.edit_message_id
    )


def card_physical_matches(
    version: ContentVersion,
    publication: Publication,
    root: Publication | None,
    plan: DeliveryPlan,
    previous_publications: Set[int],
) -> bool:
    if (
        root is not None
        and (
            root.kind is not PublicationKind.NEW_BILL
            or root.id != version.root_publication_id
            or root.status_change_id is not None
            or (publication.term, publication.number, publication.channel_id)
            != (root.term, root.number, root.channel_id)
        )
        or publication.status_change_id is not None
        or not card_plan_matches(version, plan)
    ):
        return False
    if version.previous_version_id is None:
        return (
            publication.id == version.root_publication_id
            and publication.kind is PublicationKind.NEW_BILL
            and plan.revision_of is None
            and plan.edit_message_id is None
            and plan.continuation_parent is None
        )
    match = re.fullmatch(r"card/(\d+)/[0-9a-f]{64}-(\d+)/1", publication.ref or "")
    return (
        publication.kind is PublicationKind.MESSAGE_PART
        and version.reason == "card_revision"
        and match is not None
        and int(match[1]) == version.root_publication_id
        and int(match[2]) in previous_publications
        and plan.revision_of == version.root_publication_id
        and plan.edit_message_id is not None
        and (root is None or plan.edit_message_id == root.message_id)
        and (
            publication.status is not PublicationStatus.SENT
            or publication.message_id == plan.edit_message_id
        )
        and plan.continuation_parent == version.root_publication_id
        and plan.reply_to == plan.edit_message_id
    )


def card_confirmation_matches(
    root: Publication,
    latest: Sequence[Publication],
    bound: Mapping[int, ContentVersion],
    current_id: str | None,
    *,
    alias_count: int,
) -> bool:
    if alias_count != 1 or len(latest) != 1 or root.message_id is None:
        return False
    physical = latest[0]
    version = bound.get(physical.id) if physical.id is not None else None
    return (
        physical.status is PublicationStatus.SENT
        and physical.message_id == root.message_id
        and version is not None
        and version.content_id == current_id
    )
