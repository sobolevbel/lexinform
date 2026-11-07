"""Capture eligible original notices inside their first physical freeze."""

import logging

from lexinform.models import ContentSnapshot, ContentVersion, Publication, PublicationKind
from lexinform.models.content import original_delivery_bill_snapshot
from lexinform.models.enums import PublicationStatus
from lexinform.models.original_delivery_content import (
    MAX_ORIGINAL_DELIVERY_BYTES,
    MAX_ORIGINAL_DELIVERY_PARTS,
    ORIGINAL_NOTICE_KINDS,
    OriginalDeliveryEvidence,
    OriginalDeliveryPart,
    OriginalDeliveryPlan,
    original_delivery_context,
)
from lexinform.ports import BillRepository

log = logging.getLogger(__name__)


def record_original_delivery_content(
    repo: BillRepository,
    origin: Publication,
    frozen: Publication,
    *,
    language: str,
    formatter_version: str,
) -> None:
    identity = origin.id
    plan = frozen.delivery
    if (
        origin.kind not in ORIGINAL_NOTICE_KINDS
        or identity is None
        or identity != frozen.id
        or origin.status is not PublicationStatus.QUEUED
        or origin.attempts
        or origin.delivery is None
        or origin.delivery.rendered_text
        or origin.status_change_id is not None
        or plan is None
        or not plan.bill_json
        or (origin.kind is PublicationKind.ACT_PUBLISHED and not plan.continuation_ids)
        or repo.digest_owner(identity) is not None
        or repo.get_publication_content(identity) is not None
    ):
        return
    if len(plan.continuation_ids) >= MAX_ORIGINAL_DELIVERY_PARTS:
        log.warning("original delivery history omitted: publication=%s reason=parts", identity)
        return
    try:
        parts = [
            OriginalDeliveryPart(
                id=identity,
                html=plan.rendered_text,
                disable_notification=plan.disable_notification,
            )
        ]
        for child_id in plan.continuation_ids:
            child = repo.publication_by_id(child_id)
            if child is None or child.delivery is None:
                raise ValueError("original freeze lost a physical slot")
            parts.append(
                OriginalDeliveryPart(
                    id=child_id,
                    html=child.delivery.rendered_text,
                    disable_notification=child.delivery.disable_notification,
                    held_change_ids=child.delivery.held_change_ids,
                )
            )
        original = OriginalDeliveryPlan(
            root=original_delivery_context(plan),
            parts=tuple(parts),
        )
        original.validate_original(origin.kind)
        bill = original_delivery_bill_snapshot(plan.bill_json)
        snapshot = ContentSnapshot(kind="plan", payload_json=original.model_dump_json())
        if (
            len(bill.payload_json.encode()) + len(snapshot.payload_json.encode())
            > MAX_ORIGINAL_DELIVERY_BYTES
        ):
            log.warning("original delivery history omitted: publication=%s reason=bytes", identity)
            return
        version = ContentVersion(
            root_publication_id=identity,
            reason="original",
            language=language,
            formatter_version=formatter_version,
            bill_snapshot_id=bill.snapshot_id,
            plan_snapshot_id=snapshot.snapshot_id,
            recorded_at=frozen.created_at,
            original_delivery_evidence=OriginalDeliveryEvidence.model_validate(
                {
                    "kind": origin.kind,
                    "channel_id": origin.channel_id,
                    "ref": origin.ref,
                    "bill_context": "editorial-notice-v1",
                }
            ),
        )
    except ValueError:
        log.warning("original delivery history omitted: publication=%s reason=profile", identity)
        return
    repo.save_original_delivery_content(bill, snapshot, version)
