"""Inspect available editorial metadata without changing a delivery or its recovery state."""

from typing import NamedTuple

from lexinform.content_versions import (
    card_confirmation_matches,
    confirmed_content_version,
    confirmed_multipart_card,
    content_delivery_mismatch,
    content_snapshot_refs,
    grouped_revision_obscures_current,
)
from lexinform.models import (
    ContentHistoryView,
    ContentSnapshot,
    ContentVersion,
    ContentVersionSummary,
    Publication,
    PublicationKind,
    PublicationStatus,
)
from lexinform.models.content import (
    ContentDecisionSummary,
    ContentDeliverySummary,
    ContentHistoryPage,
    OriginalAmendmentInputStatus,
)
from lexinform.ports import BillRepository, EditorialContentHistory
from lexinform.services.amendment_input_history import original_amendment_input


def inspect_content(repo: BillRepository, publication: Publication) -> ContentHistoryView | None:
    return _read_inspection(repo, publication).history


def confirmed_card_parent(
    repo: BillRepository, owner: Publication, previous: Publication
) -> ContentVersion | None:
    history, _, inventory = _read_inspection(repo, owner)
    if (
        history is None
        or history.integrity != "valid"
        or history.current_id is None
        or previous.id is None
    ):
        return None
    candidate = (
        inventory.bindings.get(previous.id)
        if inventory is not None
        else repo.get_publication_content(previous.id)
    )
    return (
        candidate if candidate is not None and candidate.content_id == history.current_id else None
    )


class _Inspection(NamedTuple):
    history: ContentHistoryView | None
    versions: list[ContentVersion]
    inventory: EditorialContentHistory | None


def _read_inspection(repo: BillRepository, publication: Publication) -> _Inspection:
    if publication.id is None:
        return _Inspection(None, [], None)
    try:
        selected = repo.get_publication_content(publication.id)
        if selected is None:
            selected = _grouped_member_content(repo, publication)
        if selected is None:
            return _Inspection(None, [], None)
        original = selected.original_delivery_evidence
        if (
            selected.card_context_profile is not None
            or selected.multipart_card_revision is not None
            or (original is not None and original.kind is PublicationKind.NEW_BILL)
        ):
            inventory = repo.read_card_content_history(selected.root_publication_id)
        elif selected.grouped_stage_evidence is not None:
            inventory = repo.read_bound_content_history(selected.root_publication_id)
        else:
            inventory = None
        versions = (
            inventory.versions
            if inventory is not None
            else repo.list_content_versions(selected.root_publication_id)
        )
        return _Inspection(_inspect(repo, selected, versions, inventory), versions, inventory)
    except ValueError:
        return _Inspection(ContentHistoryView(integrity="unavailable"), [], None)


def _inspect_multipart_card(
    repo: BillRepository, selected: ContentVersion, inventory: EditorialContentHistory
) -> ContentHistoryView:
    root_id = selected.root_publication_id
    root = inventory.publications.get(root_id)
    original = inventory.bindings.get(root_id)
    if original is None:
        return ContentHistoryView(integrity="unavailable")
    current = (
        confirmed_multipart_card(
            root,
            repo.card_refresh_parts(root_id),
            alias_count=len(repo.card_aliases(root.channel_id, root.message_id)),
            version_for=inventory.bindings.get,
            snapshot_for=inventory.snapshots.get,
        )
        if root is not None and root.message_id is not None
        else None
    )
    versions_by_id = {item.content_id: item for item in (selected, original)}
    if current is not None:
        versions_by_id[current.content_id] = current
    return ContentHistoryView(
        root_publication_id=root_id,
        version_count=len(inventory.versions),
        inspected_id=selected.content_id,
        original_id=original.content_id,
        current_id=current.content_id if current else None,
        summaries=tuple(
            ContentVersionSummary.from_version(item) for item in versions_by_id.values()
        ),
    )


def _inspect(
    repo: BillRepository,
    selected: ContentVersion,
    versions: list[ContentVersion],
    inventory: EditorialContentHistory | None,
) -> ContentHistoryView:
    original_evidence = selected.original_delivery_evidence
    if inventory is not None and (
        (original_evidence is not None and original_evidence.kind is PublicationKind.NEW_BILL)
        or any(version.multipart_card_revision is not None for version in versions)
    ):
        return _inspect_multipart_card(repo, selected, inventory)
    if (
        selected.digest_profile is not None
        or selected.sitting_profile is not None
        or selected.act_published_evidence is not None
        or selected.original_delivery_evidence is not None
        or (
            selected.direct_stage_evidence is not None
            and selected.direct_stage_evidence.multipart is not None
        )
    ):
        digest_parts = repo.list_content_publications(selected.root_publication_id)
        if versions != [selected] or not digest_parts:
            return ContentHistoryView(integrity="unavailable")
        physical_confirmed = all(row.status is PublicationStatus.SENT for row in digest_parts)
        return ContentHistoryView(
            root_publication_id=selected.root_publication_id,
            version_count=1,
            summaries=(ContentVersionSummary.from_version(selected),),
            inspected_id=selected.content_id,
            original_id=selected.content_id,
            current_id=selected.content_id if physical_confirmed else None,
        )
    root_id = selected.root_publication_id
    if inventory is not None:
        root = inventory.publications.get(root_id)
        original = inventory.bindings.get(root_id)
    else:
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
    snapshots: dict[str, ContentSnapshot] = inventory.snapshots if inventory is not None else {}
    by_id = {version.content_id: version for version in versions}
    for version in versions:
        for kind, identity in content_snapshot_refs(version):
            if identity not in snapshots:
                snapshot = repo.get_content_snapshot(identity) if inventory is None else None
                if snapshot is None:
                    return base.model_copy(update={"integrity": "unavailable"})
                snapshots[identity] = snapshot
            if snapshots[identity].kind != kind:
                return base.model_copy(update={"integrity": "unavailable"})
        if version.previous_version_id is not None and version.previous_version_id not in by_id:
            return base.model_copy(update={"integrity": "unavailable"})
    confirmed: set[str] = set()
    publications = (
        list(inventory.publications.values())
        if inventory is not None
        else repo.list_content_publications(root_id)
    )
    bound_by_publication: dict[int, ContentVersion] = {}
    grouped_uncertain = False
    for physical in publications:
        if physical.id is None:
            continue
        bound = (
            inventory.bindings.get(physical.id)
            if inventory is not None
            else repo.get_publication_content(physical.id)
        )
        if bound is None:
            continue
        bound_by_publication[physical.id] = bound
        if bound.root_publication_id != root_id or (
            physical.term,
            physical.number,
            physical.channel_id,
        ) != (root.term, root.number, root.channel_id):
            return base.model_copy(update={"integrity": "unavailable"})
        if (physical.id == root_id) != (bound.previous_version_id is None):
            return base.model_copy(update={"integrity": "unavailable"})
        if physical.id != root_id and physical.kind is not (
            PublicationKind.MESSAGE_PART
            if bound.change_snapshot_id is None or bound.reason == "grouped_stage_revision"
            else PublicationKind.AMENDMENTS_REVISION
        ):
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
        if bound.reason == "grouped_stage_revision" and grouped_revision_obscures_current(
            physical.status,
            retried=any(item.action == "retry" for item in repo.delivery_resolutions(physical.id)),
        ):
            grouped_uncertain = True
        if physical.status is PublicationStatus.SENT and physical.message_id is not None:
            if physical.id != root_id and physical.message_id != root.message_id:
                return base.model_copy(update={"integrity": "unavailable"})
            confirmed.add(bound.content_id)
    bound_ids = {bound.content_id for bound in bound_by_publication.values()}
    if any(
        version.reason == "grouped_stage_revision" and version.content_id not in bound_ids
        for version in versions
    ):
        return base.model_copy(update={"integrity": "unavailable"})
    confirmation = confirmed_content_version(
        {version.content_id: version.previous_version_id for version in versions}, confirmed
    )
    current_id = None if grouped_uncertain else confirmation.current_id
    if original.change_snapshot_id is None and (
        any(version.content_id not in bound_ids for version in versions)
        or any(item.id not in bound_by_publication for item in publications)
        or not card_confirmation_matches(
            root,
            repo.card_refresh_parts(root_id),
            bound_by_publication,
            current_id,
            alias_count=len(repo.card_aliases(root.channel_id, root.message_id))
            if root.message_id is not None
            else 0,
        )
    ):
        current_id = None
    current = by_id.get(current_id) if current_id is not None else None
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


def _grouped_member_content(
    repo: BillRepository, publication: Publication
) -> ContentVersion | None:
    if publication.kind is not PublicationKind.STATUS_UPDATE or publication.ref is None:
        return None
    prefix, _, identity = publication.ref.partition("/")
    if (
        prefix != "updates"
        or not 1 <= len(identity) <= 19
        or not (identity.isascii() and identity.isdecimal())
    ):
        return None
    root_id = int(identity)
    if not 0 < root_id < 2**63 or publication.ref != f"updates/{root_id}":
        return None
    if root_id == publication.id:
        return None
    root = repo.get_publication_content(root_id)
    if root is None or root.grouped_stage_evidence is None:
        return None
    first, *siblings = root.grouped_stage_evidence.members
    if first.publication_id != root_id or all(
        member.publication_id != publication.id for member in siblings
    ):
        return None
    return root


def _original_input_status(
    repo: BillRepository, publication: Publication
) -> OriginalAmendmentInputStatus | None:
    if (
        publication.kind is not PublicationKind.STATUS_UPDATE
        or publication.status is not PublicationStatus.SENT
        or publication.message_id is None
    ):
        return None
    try:
        availability = original_amendment_input(repo, publication)
    except ValueError:
        return "invalid"
    if availability.state == "available":
        return "available"
    return "legacy_unavailable" if availability.reason == "legacy_input_unavailable" else None


def inspect_content_page(
    repo: BillRepository, publication: Publication, *, page: int
) -> ContentHistoryPage:
    """Page immutable editorial references under the existing confirmation and integrity policy."""
    history, versions, inventory = _read_inspection(repo, publication)
    unavailable = ContentHistoryPage(status="unavailable", page=page, history=history)
    if history is None or history.integrity != "valid" or history.root_publication_id is None:
        return unavailable
    try:
        if not 1 <= page <= len(versions):
            return ContentHistoryPage(
                status="out_of_range",
                page=page,
                pages=len(versions),
                history=history,
            )
        version = versions[page - 1]
        physical = (
            [
                item
                for identity, item in inventory.publications.items()
                if inventory.bindings.get(identity) == version
            ]
            if inventory is not None
            else repo.verified_content_publications(version)
        )
        deliveries: list[ContentDeliverySummary] = []
        for item in physical[:3]:
            assert item.id is not None, "the bound publication inventory supplies persisted IDs"
            decisions = repo.delivery_resolutions(item.id)
            deliveries.append(
                ContentDeliverySummary(
                    publication_id=item.id,
                    status=item.status,
                    message_id=item.message_id,
                    attempts=item.attempts,
                    decisions=tuple(
                        ContentDecisionSummary(
                            action=decision.action,
                            resolved_at=decision.resolved_at,
                            command_update_id=decision.command.update_id,
                        )
                        for decision in decisions[-3:]
                    ),
                    omitted_decisions=max(0, len(decisions) - 3),
                )
            )
        original = (
            next((item for item in physical if item.id == history.root_publication_id), None)
            if version.content_id == history.original_id
            else None
        )
        original_input = _original_input_status(repo, original) if original is not None else None
        original_primary = (
            True
            if original is not None
            and version.card_primary_evidence is not None
            and original.status is PublicationStatus.SENT
            and original.message_id is not None
            and original.message_id > 0
            else None
        )
        return ContentHistoryPage(
            status="available",
            page=page,
            pages=len(versions),
            history=history,
            version=ContentVersionSummary.from_version(version),
            deliveries=tuple(deliveries),
            omitted_deliveries=max(0, len(physical) - 3),
            original_amendment_input=original_input,
            original_primary_lineage=original_primary,
        )
    except ValueError:
        return unavailable
