import base64
import hashlib
import json
import zlib
from contextlib import closing
from datetime import timedelta

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.bill_context import continuation_snapshot
from lexinform.models import (
    Bill,
    BillStatus,
    ContentSnapshot,
    ContentVersion,
    DeliveryPlan,
    Publication,
    PublicationKind,
    PublicationStatus,
)
from lexinform.models.content import (
    PackedOriginalBill,
    original_bill_context,
    original_delivery_bill_snapshot,
)
from lexinform.models.original_delivery_content import (
    OriginalDeliveryEvidence,
    OriginalDeliveryPart,
    OriginalDeliveryPlan,
    ReviewNoticeKind,
    original_delivery_context,
)
from lexinform.models.reviews import (
    DocumentReview,
    DocumentReviewRecord,
    ReviewContext,
    ReviewDocument,
)
from lexinform.services.content_inspection import inspect_content
from lexinform.services.original_delivery_content import record_original_delivery_content
from tests.computations import NOW
from tests.harness import summary


def test_original_notice_snapshot_omits_operational_document_bodies() -> None:
    record = {
        "context": {
            "documents": [
                {"identity": "official", "sha256": "pin", "text": "source", "body": "raw"}
            ]
        },
        "review": {"summary": "retained result"},
    }
    raw = json.dumps(
        {
            "ready_analysis": {"text": "prepared text"},
            "consultation_review": record,
            "act_application_review": record,
        }
    )
    snapshot = original_delivery_bill_snapshot(raw)
    value = json.loads(snapshot.payload_json)
    assert "ready_analysis" not in value
    for role in ("consultation_review", "act_application_review"):
        assert value[role]["context"]["documents"] == [
            {"identity": "official", "sha256": "pin", "text": "", "body": ""}
        ]
        assert value[role]["review"] == record["review"]
    assert original_delivery_bill_snapshot(snapshot.payload_json) == snapshot


@pytest.mark.parametrize(
    "kind", [PublicationKind.CONSULTATION_REVIEW, PublicationKind.COMMISSION_REVIEW]
)
@pytest.mark.parametrize("packed", [False, True])
def test_direct_review_history_reads_exact_render_after_cleanup(
    kind: ReviewNoticeKind, packed: bool
) -> None:
    process = summary("RCL/123", "Projekt ustawy" + ("x" * 150_000 if packed else ""))
    context = ReviewContext(
        number=process.number,
        title=process.title,
        purpose=kind.value,
        documents=(
            ReviewDocument(
                identity="recorded",
                url="https://example.test/recorded.pdf",
                role="commission_position"
                if kind is PublicationKind.COMMISSION_REVIEW
                else "position",
                text="Full operational text",
                body="Commission name",
                sha256="a" * 64,
                normalized_sha256="b" * 64,
            ),
        ),
    )
    record = DocumentReviewRecord(
        review=DocumentReview(
            purpose=context.purpose,
            complete=True,
            summary="Saved result",
            consultation_findings=(),
            effective_rules=(),
            unresolved=(),
        ),
        context=context,
        created_at=NOW,
    )
    field = kind.value
    bill = Bill(
        summary=process, status=BillStatus.ANALYZED, first_seen_at=NOW, last_checked_at=NOW
    ).model_copy(update={field: record})
    raw = bill.model_dump_json()
    ref = context.memo_key(process.term)
    frozen = DeliveryPlan(
        bill_json=raw, rendered_text="<b>Saved review</b>", reply_to=10, today=NOW.date()
    )
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        root = repo.create_publication(
            Publication(
                term=process.term,
                number=process.number,
                channel_id="fixture",
                kind=kind,
                status=PublicationStatus.QUEUED,
                created_at=NOW,
                ref=ref,
                delivery=frozen,
            )
        )
        projected = original_delivery_bill_snapshot(raw, review_notice=True)
        archived = projected
        if packed:
            body = projected.payload_json.encode()
            envelope = PackedOriginalBill(
                sha256=hashlib.sha256(body).hexdigest(),
                packed=base64.b64encode(zlib.compress(body)).decode("ascii"),
            )
            archived = ContentSnapshot(kind="bill", payload_json=envelope.model_dump_json())
        document = json.loads(projected.payload_json)[field]["context"]["documents"][0]
        assert document["text"] == ""
        assert document["body"] == (
            "Commission name" if kind is PublicationKind.COMMISSION_REVIEW else ""
        )
        assert document["sha256"] == context.documents[0].sha256
        parts = [
            OriginalDeliveryPart(id=root, html=frozen.rendered_text, disable_notification=False)
        ]
        child = None
        child_plan = DeliveryPlan(
            bill_json=continuation_snapshot(bill),
            rendered_text="<i>Second part</i>",
            continuation_parent=root,
            continuation_root=root,
            continuation_index=2,
            policy_version="attention-v1",
        )
        if kind is PublicationKind.COMMISSION_REVIEW:
            child = repo.create_publication(
                Publication(
                    term=process.term,
                    number=process.number,
                    channel_id="fixture",
                    kind=PublicationKind.MESSAGE_PART,
                    status=PublicationStatus.QUEUED,
                    created_at=NOW,
                    ref=f"message/{root}/2",
                    delivery=child_plan,
                )
            )
            frozen = frozen.model_copy(update={"continuation_ids": (child,)})
            assert repo.replace_unattempted_delivery(root, frozen)
            parts.append(
                OriginalDeliveryPart(
                    id=child, html=child_plan.rendered_text, disable_notification=False
                )
            )
        plan = OriginalDeliveryPlan(root=original_delivery_context(frozen), parts=tuple(parts))
        snapshot = ContentSnapshot(kind="plan", payload_json=plan.model_dump_json())
        evidence = OriginalDeliveryEvidence(
            kind=kind,
            channel_id="fixture",
            ref=ref,
            bill_context="editorial-notice-v1",
            packed_bill=True if packed else None,
        )
        version = ContentVersion(
            root_publication_id=root,
            reason="original",
            language="ru",
            formatter_version="fixture-v1",
            recorded_at=NOW,
            bill_snapshot_id=archived.snapshot_id,
            plan_snapshot_id=snapshot.snapshot_id,
            original_delivery_evidence=evidence,
        )
        wrong = version.model_copy(
            update={"original_delivery_evidence": evidence.model_copy(update={"ref": "other"})}
        )
        with pytest.raises(ValueError, match="original_delivery_review_scope"), repo.atomic():
            repo.save_original_delivery_content(archived, snapshot, wrong)
        stored = repo.publication_by_id(root)
        assert stored is not None, "the freeze retains its queued publication"
        origin = stored.model_copy(
            update={"delivery": frozen.model_copy(update={"rendered_text": ""})}
        )
        with repo.atomic():
            record_original_delivery_content(
                repo,
                origin.model_copy(update={"ref": "other"}),
                stored,
                language="ru",
                formatter_version="fixture-v1",
            )
            assert repo.get_publication_content(root) is None
            record_original_delivery_content(
                repo, origin, stored, language="ru", formatter_version="fixture-v1"
            )
        assert repo.get_publication_content(root) == version
        repo.mark_publication(root, PublicationStatus.SENT, message_id=11, sent_at=NOW)
        if child is not None:
            parent = repo.publication_by_id(root)
            assert parent is not None, "the root was just sent"
            partial = inspect_content(repo, parent)
            assert partial is not None and partial.current_id is None
            assert repo.replace_unattempted_delivery(
                child, child_plan.model_copy(update={"reply_to": 11})
            )
            repo.mark_publication(child, PublicationStatus.SENT, message_id=12, sent_at=NOW)
        cutoff = NOW + timedelta(days=181)
        assert repo.prune_history(before=cutoff, memo_before=cutoff)["delivery_payloads"] == len(
            parts
        )
        dump = repo.dump()
        assert "Full operational text" not in dump
        repo.restore(dump)
        assert repo.get_publication_content(root) == version
        assert repo.get_content_snapshot(version.plan_snapshot_id) == snapshot
        preserved = repo.get_content_snapshot(version.bill_context_id)
        assert preserved is not None, "the original bill context survives cleanup"
        assert original_bill_context(preserved, packed=packed, review_notice=True) == projected
        confirmed = repo.publication_by_id(root)
        assert confirmed is not None, "retention preserves the root identity"
        history = inspect_content(repo, confirmed)
        assert history is not None and history.current_id == version.content_id
