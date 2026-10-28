"""Inspect available editorial metadata without changing a delivery or its recovery state."""

from lexinform.content_versions import confirmed_content_version, content_delivery_mismatch
from lexinform.models import (
    ContentHistoryView,
    ContentSnapshot,
    ContentVersionSummary,
    Publication,
    PublicationKind,
    PublicationStatus,
)
from lexinform.ports import BillRepository


def inspect_content(repo: BillRepository, publication: Publication) -> ContentHistoryView | None:
    if publication.id is None:
        return None
    try:
        return _inspect(repo, publication)
    except ValueError:
        return ContentHistoryView(integrity="unavailable")


def _inspect(repo: BillRepository, publication: Publication) -> ContentHistoryView | None:
    assert publication.id is not None, "inspect_content requires a persisted publication identity"
    selected = repo.get_publication_content(publication.id)
    if selected is None:
        return None
    root_id = selected.root_publication_id
    versions = repo.list_content_versions(root_id)
    root = repo.publication_by_id(root_id)
    original = repo.get_publication_content(root_id)
    inspected = ContentVersionSummary.from_version(selected)
    summaries = {inspected.content_id: inspected}
    if original is not None:
        summaries[original.content_id] = ContentVersionSummary.from_version(original)
    base = ContentHistoryView(
        root_publication_id=root_id,
        version_count=len(versions),
        summaries=tuple(summaries.values()),
        inspected_id=selected.content_id,
        original_id=original.content_id if original else None,
    )
    if root is None or original is None or original.previous_version_id is not None:
        return base.model_copy(update={"integrity": "unavailable"})
    snapshots: dict[str, ContentSnapshot] = {}
    by_id = {version.content_id: version for version in versions}
    for version in versions:
        for identity, kind in (
            (version.bill_snapshot_id, "bill"),
            (version.change_snapshot_id, "change"),
            (version.plan_snapshot_id, "plan"),
        ):
            if identity not in snapshots:
                snapshot = repo.get_content_snapshot(identity)
                if snapshot is None:
                    return base.model_copy(update={"integrity": "unavailable"})
                snapshots[identity] = snapshot
            if snapshots[identity].kind != kind:
                return base.model_copy(update={"integrity": "unavailable"})
        if version.previous_version_id is not None and version.previous_version_id not in by_id:
            return base.model_copy(update={"integrity": "unavailable"})
    confirmed: set[str] = set()
    for physical in repo.list_content_publications(root_id):
        if physical.id is None:
            continue
        bound = repo.get_publication_content(physical.id)
        if bound is None:
            continue
        if bound.root_publication_id != root_id or (
            physical.term,
            physical.number,
            physical.channel_id,
        ) != (root.term, root.number, root.channel_id):
            return base.model_copy(update={"integrity": "unavailable"})
        if (physical.id == root_id) != (bound.previous_version_id is None):
            return base.model_copy(update={"integrity": "unavailable"})
        if physical.id != root_id and physical.kind is not PublicationKind.AMENDMENTS_REVISION:
            return base.model_copy(update={"integrity": "unavailable"})
        plan = physical.delivery
        if plan is not None and content_delivery_mismatch(bound, snapshots, plan) is not None:
            return base.model_copy(update={"integrity": "unavailable"})
        if (
            physical.id != root_id
            and plan is not None
            and (plan.revision_of != root_id or plan.edit_message_id != root.message_id)
        ):
            return base.model_copy(update={"integrity": "unavailable"})
        if physical.status is PublicationStatus.SENT and physical.message_id is not None:
            if physical.id != root_id and physical.message_id != root.message_id:
                return base.model_copy(update={"integrity": "unavailable"})
            confirmed.add(bound.content_id)
    confirmation = confirmed_content_version(
        {version.content_id: version.previous_version_id for version in versions}, confirmed
    )
    current = by_id.get(confirmation.current_id) if confirmation.current_id is not None else None
    if current is not None:
        summaries[current.content_id] = ContentVersionSummary.from_version(current)
    return base.model_copy(
        update={
            "summaries": tuple(summaries.values()),
            "current_id": current.content_id if current else None,
            "ambiguous": confirmation.ambiguous,
            "integrity": "unavailable" if confirmation.ambiguous else "valid",
        }
    )
