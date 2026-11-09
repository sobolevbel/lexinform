"""Capture multipart revisions only from a confirmed immutable predecessor."""

import logging

from lexinform.errors import CardPrimaryEvidenceError
from lexinform.models import Bill, ContentSnapshot, ContentVersion, Publication
from lexinform.models.content import (
    MultipartCardRevisionProfile,
    original_delivery_bill_snapshot,
    pack_original_bill_snapshot,
)
from lexinform.models.multipart_card_history import (
    MAX_MULTIPART_CARD_BYTES,
    MAX_MULTIPART_CARD_PARTS,
    MultipartCardPlan,
    MultipartCardSlot,
)
from lexinform.ports import BillRepository
from lexinform.primary_projections import primary_record_sha256
from lexinform.services.content_history import FORMATTER_VERSION

log = logging.getLogger(__name__)


def record_multipart_card_revision(
    repo: BillRepository,
    root: Publication,
    identities: tuple[int, ...],
    previous: ContentVersion,
    predecessor: tuple[Publication, ...],
    bill: Bill,
    *,
    ref: str,
    language: str | None,
) -> None:
    assert root.delivery is not None, "the original freeze retains every slot"
    if len(identities) > MAX_MULTIPART_CARD_PARTS:
        log.warning("multipart card history omitted: publication=%s reason=parts", root.id)
        return
    try:
        slots = []
        for identity in identities:
            row = repo.publication_by_id(identity)
            if row is None or row.delivery is None:
                raise ValueError("missing card slot")
            slots.append(
                MultipartCardSlot(
                    id=identity,
                    html=row.delivery.rendered_text,
                    disable_notification=row.delivery.disable_notification,
                    edit_message_id=row.delivery.edit_message_id,
                )
            )
        plan = MultipartCardPlan.model_validate(
            {
                "channel_id": root.channel_id,
                "ref_prefix": ref,
                "today": root.delivery.today,
                "predecessor_slots": tuple(row.id for row in predecessor),
                "slots": tuple(slots),
            }
        )
        context = original_delivery_bill_snapshot(bill.model_dump_json())
        snapshot = ContentSnapshot(kind="plan", payload_json=plan.model_dump_json())
        context_budget = MAX_MULTIPART_CARD_BYTES - len(snapshot.payload_json.encode())
        profile: MultipartCardRevisionProfile = "card-multipart-revision-v1"
        if len(context.payload_json.encode()) > context_budget:
            packed = pack_original_bill_snapshot(context) if context_budget > 0 else None
            if packed is None or len(packed.payload_json.encode()) > context_budget:
                log.warning("multipart card history omitted: publication=%s reason=bytes", root.id)
                return
            context = packed
            profile = "card-multipart-revision-packed-v1"
        version = ContentVersion(
            root_publication_id=previous.root_publication_id,
            previous_version_id=previous.content_id,
            reason="card_revision",
            bill_snapshot_id=context.snapshot_id,
            plan_snapshot_id=snapshot.snapshot_id,
            multipart_card_revision=profile,
            formatter_version=FORMATTER_VERSION,
            language=language,
            recorded_at=root.created_at,
        )
    except ValueError:
        log.warning("multipart card history omitted: publication=%s reason=profile", root.id)
        return
    if bill.analysis is not None:
        reference = repo.find_card_primary_evidence(bill)
        if reference is not None:
            marked = version.model_copy(
                update={
                    "card_primary_evidence": reference.model_copy(
                        update={
                            "revision_analysis_sha256": primary_record_sha256(bill.analysis),
                            "revision_projection": "current",
                        }
                    )
                }
            )
            try:
                repo.save_multipart_card_revision(context, snapshot, marked)
            except CardPrimaryEvidenceError:
                pass
            else:
                return
    repo.save_multipart_card_revision(context, snapshot, version)
