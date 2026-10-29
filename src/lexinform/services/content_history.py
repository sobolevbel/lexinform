"""Preserve single-message editorial history without using a current Bill as historical context."""

from lexinform.models import (
    Bill,
    ContentSnapshot,
    ContentVersion,
    DeliveryPlan,
    Publication,
    PublicationKind,
    PublicationStatus,
    StatusChange,
    amendments_stage,
)
from lexinform.models.content import editorial_bill_snapshot
from lexinform.ports import BillRepository, Clock

FORMATTER_VERSION = "telegram-format-v2"


def restore_content_delivery(repo: BillRepository, content: ContentVersion) -> DeliveryPlan:
    snapshots: dict[str, ContentSnapshot] = {}
    for kind, identity in (
        ("bill", content.bill_snapshot_id),
        ("change", content.change_snapshot_id),
        ("plan", content.plan_snapshot_id),
    ):
        snapshot = repo.get_content_snapshot(identity)
        if snapshot is None or snapshot.kind != kind:
            raise ValueError("Editorial history has lost its semantic snapshot")
        snapshots[kind] = snapshot
    plan = DeliveryPlan.model_validate_json(snapshots["plan"].payload_json)
    return plan.model_copy(
        update={
            "bill_json": snapshots["bill"].payload_json,
            "change_json": snapshots["change"].payload_json,
            "rendered_text": content.rendered_text or "",
        }
    )


def record_publication_content(
    repo: BillRepository,
    clock: Clock,
    publication: Publication,
    *,
    formatter_version: str | None = None,
    language: str | None = None,
) -> ContentVersion | None:
    if publication.id is None:
        raise ValueError("Editorial history requires a saved physical publication")
    existing = repo.get_publication_content(publication.id)
    if existing is not None:
        current = repo.publication_by_id(publication.id)
        if current is not None and current.delivery is not None:
            repo.bind_publication_content(publication.id, existing.content_id)
        return existing
    plan = publication.delivery
    if (
        publication.kind not in (PublicationKind.STATUS_UPDATE, PublicationKind.AMENDMENTS_REVISION)
        or plan is None
        or not plan.bill_json
        or not plan.change_json
        or plan.publication_ids
        or plan.continuation_ids
        or plan.continuation_root is not None
        or plan.absorbed_by is not None
        or repo.digest_owner(publication.id) is not None
        or (not plan.rendered_text and publication.status is not PublicationStatus.SENT)
    ):
        return None
    root_id = publication.id
    previous: ContentVersion | None = None
    if publication.kind is PublicationKind.AMENDMENTS_REVISION:
        if plan.revision_of is None:
            return None
        root_id = plan.revision_of
        original = repo.publication_by_id(root_id)
        if original is None:
            return None
        previous = repo.get_publication_content(root_id)
        if previous is None:
            previous = record_publication_content(repo, clock, original)
        if previous is None:
            return None
        for revision in reversed(repo.list_amendment_revisions(root_id)):
            if (
                revision.id is not None
                and revision.id < publication.id
                and revision.status is PublicationStatus.SENT
            ):
                older = repo.get_publication_content(revision.id)
                if older is None:
                    older = record_publication_content(repo, clock, revision)
                if older is None:
                    return None
                previous = older
    change = StatusChange.model_validate_json(plan.change_json)
    if (
        publication.kind is PublicationKind.STATUS_UPDATE
        and amendments_stage(change.new_stages) is None
    ):
        return None
    bill = Bill.model_validate_json(plan.bill_json)
    computations = tuple(
        sorted(
            {
                record.computation_id
                for record in (bill.analysis, change.amendments, *change.supplements)
                if record is not None and record.computation_id is not None
            }
        )
    )
    bill_snapshot = editorial_bill_snapshot(plan.bill_json)
    change_snapshot = ContentSnapshot(kind="change", payload_json=plan.change_json)
    semantic_plan = plan.model_copy(
        update={"bill_json": "", "change_json": "", "rendered_text": ""}
    )
    plan_snapshot = ContentSnapshot(kind="plan", payload_json=semantic_plan.model_dump_json())
    version = ContentVersion(
        root_publication_id=root_id,
        previous_version_id=previous.content_id if previous else None,
        reason="amendments_revision" if previous else "original",
        language=language,
        bill_snapshot_id=bill_snapshot.snapshot_id,
        change_snapshot_id=change_snapshot.snapshot_id,
        plan_snapshot_id=plan_snapshot.snapshot_id,
        rendered_text=plan.rendered_text or None,
        formatter_version=formatter_version if plan.rendered_text else None,
        computation_ids=computations,
        status_change_id=change.id,
        recorded_at=clock.now(),
    )
    with repo.atomic():
        for snapshot in (bill_snapshot, change_snapshot, plan_snapshot):
            repo.save_content_snapshot(snapshot)
        repo.save_content_version(version)
        repo.bind_publication_content(publication.id, version.content_id)
    return version
