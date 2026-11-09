"""Capture eligible original notices inside their first physical freeze."""

import logging

from lexinform.errors import CardPrimaryEvidenceError
from lexinform.models import Bill, ContentSnapshot, ContentVersion, Publication, PublicationKind
from lexinform.models.content import (
    original_delivery_bill_snapshot,
    pack_original_bill_snapshot,
    validate_review_notice,
)
from lexinform.models.enums import PublicationStatus
from lexinform.models.original_delivery_content import (
    MAX_ORIGINAL_DELIVERY_BYTES,
    MAX_ORIGINAL_DELIVERY_PARTS,
    ORIGINAL_CARD_KINDS,
    ORIGINAL_DELIVERY_KINDS,
    REVIEW_NOTICE_KINDS,
    JointPrimaryEvidence,
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
    language: str | None,
    formatter_version: str,
) -> None:
    identity = origin.id
    plan = frozen.delivery
    if (
        origin.kind not in ORIGINAL_DELIVERY_KINDS
        or identity is None
        or identity != frozen.id
        or origin.status is not PublicationStatus.QUEUED
        or origin.attempts
        or origin.delivery is None
        or origin.delivery.rendered_text
        or origin.status_change_id is not None
        or plan is None
        or not plan.bill_json
        or (
            origin.kind in (PublicationKind.ACT_PUBLISHED, PublicationKind.NEW_BILL)
            and not plan.continuation_ids
        )
        or repo.digest_owner(identity) is not None
        or repo.get_publication_content(identity) is not None
    ):
        return
    if len(plan.continuation_ids) >= MAX_ORIGINAL_DELIVERY_PARTS:
        log.warning("original delivery history omitted: publication=%s reason=parts", identity)
        return
    try:
        primary_snapshot = None
        joint_primary = None
        if origin.kind is PublicationKind.JOINT_BILL:
            if not plan.primary_json:
                raise ValueError("joint original lacks its frozen primary Bill")
            primary_bill = Bill.model_validate_json(plan.primary_json)
            primary = repo.get_publication(
                primary_bill.term,
                primary_bill.number,
                PublicationKind.NEW_BILL,
                origin.channel_id,
            )
            if (
                primary is None
                or primary.id is None
                or primary.status is not PublicationStatus.SENT
                or primary.message_id is None
                or primary.message_id != plan.reply_to
            ):
                raise ValueError("joint original lacks its confirmed primary receipt")
            primary_snapshot = original_delivery_bill_snapshot(plan.primary_json)
            joint_primary = JointPrimaryEvidence(
                publication_id=primary.id,
                bill_snapshot_id=primary_snapshot.snapshot_id,
            )
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
            root=original_delivery_context(plan, joint=joint_primary is not None),
            parts=tuple(parts),
        )
        original.validate_original(origin.kind)
        review_notice = origin.kind in REVIEW_NOTICE_KINDS
        bill = original_delivery_bill_snapshot(plan.bill_json, review_notice=review_notice)
        application_id = None
        joint_application_id = None
        card_primary = (
            repo.find_card_primary_evidence(Bill.model_validate_json(bill.payload_json))
            if origin.kind is PublicationKind.NEW_BILL
            else None
        )
        if review_notice:
            context = Bill.model_validate_json(bill.payload_json)
            review = validate_review_notice(context, origin.kind, origin.ref)
            application_id = repo.find_review_application_id(
                context, review, origin.kind, recorded_at=frozen.created_at
            )
        elif origin.kind is PublicationKind.JOINT_BILL:
            joint_application_id = repo.find_joint_comparison_application_id(
                Bill.model_validate_json(bill.payload_json), recorded_at=frozen.created_at
            )
        snapshot = ContentSnapshot(kind="plan", payload_json=original.model_dump_json())
        bill_budget = MAX_ORIGINAL_DELIVERY_BYTES - len(snapshot.payload_json.encode())
        primary_bytes = len(primary_snapshot.payload_json.encode()) if primary_snapshot else 0
        packed_bill = None
        if len(bill.payload_json.encode()) + primary_bytes > bill_budget:
            replacement = pack_original_bill_snapshot(bill)
            if replacement is not None:
                bill = replacement
                packed_bill = True
            if primary_snapshot is not None and joint_primary is not None:
                replacement = pack_original_bill_snapshot(primary_snapshot)
                if replacement is not None:
                    primary_snapshot = replacement
                    primary_bytes = len(replacement.payload_json.encode())
                    joint_primary = joint_primary.model_copy(
                        update={"bill_snapshot_id": replacement.snapshot_id, "packed_bill": True}
                    )
        if len(bill.payload_json.encode()) + primary_bytes > bill_budget:
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
                    "bill_context": "editorial-card-original-v1"
                    if origin.kind in ORIGINAL_CARD_KINDS
                    else "editorial-notice-v1",
                    "joint_primary": joint_primary,
                    "joint_comparison_application_id": joint_application_id,
                    "packed_bill": packed_bill,
                    "commission_review_application_id": application_id
                    if origin.kind is PublicationKind.COMMISSION_REVIEW
                    else None,
                    "review_application_id": application_id
                    if origin.kind is PublicationKind.CONSULTATION_REVIEW
                    else None,
                }
            ),
        )
    except ValueError:
        log.warning("original delivery history omitted: publication=%s reason=profile", identity)
        return
    if card_primary is not None:
        marked = version.model_copy(update={"card_primary_evidence": card_primary})
        try:
            repo.save_original_delivery_content(bill, snapshot, marked)
        except CardPrimaryEvidenceError:
            pass
        else:
            return
    if primary_snapshot is None:
        repo.save_original_delivery_content(bill, snapshot, version)
    else:
        repo.save_original_delivery_content(bill, snapshot, version, primary=primary_snapshot)
