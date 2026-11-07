"""Preserve single-message editorial history without using a current Bill as historical context."""

import hashlib
from typing import Literal

from lexinform.content_sources import (
    SourceContextInvalidError,
    inspect_primary_content_sources,
    supported_source_context,
)
from lexinform.content_versions import (
    act_published_plan_matches,
    card_physical_matches,
    direct_stage_plan_matches,
    grouped_stage_context,
)
from lexinform.errors import CardPrimaryEvidenceError, SupplementContentEvidenceError
from lexinform.grouped_update_preview import GroupedUpdateRender
from lexinform.models import (
    AmendmentsRecord,
    Bill,
    ContentSnapshot,
    ContentVersion,
    DeliveryPlan,
    Publication,
    PublicationKind,
    PublicationStatus,
    ResultApplicationTarget,
    StatusChange,
    SupplementRecord,
    amendments_stage,
)
from lexinform.models.content import (
    MAX_ACT_PUBLISHED_CONTENT_BYTES,
    MAX_DIRECT_STAGE_CONTENT_BYTES,
    MAX_GROUPED_STAGE_CONTENT_BYTES,
    ActPublishedContentEvidence,
    DirectStageContentEvidence,
    GroupedStageContentEvidence,
    GroupedStageMember,
    SupplementContentEvidence,
    card_bill_snapshot,
    editorial_bill_snapshot,
)
from lexinform.models.grouped_update_witness import GroupedRenderWitness
from lexinform.ports import BillRepository, Clock
from lexinform.telegram_text import MESSAGE_LIMIT, utf16_length

FORMATTER_VERSION = "telegram-format-v2"


def restore_content_delivery(repo: BillRepository, content: ContentVersion) -> DeliveryPlan:
    if content.original_delivery_evidence is not None:
        raise ValueError("Original physical delivery history is not a correction input")
    if content.digest_profile is not None:
        raise ValueError("Digest history cannot restore a Bill delivery")
    if content.card_context_profile is not None:
        raise ValueError("Compact card history does not retain the full original context")
    if content.grouped_stage_evidence is not None:
        raise ValueError("Compact grouped stage history does not retain correction context")
    if content.direct_stage_evidence is not None:
        raise ValueError("Compact direct stage history does not retain the full original context")
    snapshots: dict[str, ContentSnapshot] = {}
    for kind, identity in (
        ("bill", content.bill_snapshot_id),
        ("change", content.change_snapshot_id),
        ("plan", content.plan_snapshot_id),
    ):
        if identity is None:
            continue
        snapshot = repo.get_content_snapshot(identity)
        if snapshot is None or snapshot.kind != kind:
            raise ValueError("Editorial history has lost its semantic snapshot")
        snapshots[kind] = snapshot
    plan = DeliveryPlan.model_validate_json(snapshots["plan"].payload_json)
    return plan.model_copy(
        update={
            "bill_json": snapshots["bill"].payload_json,
            "change_json": snapshots["change"].payload_json if "change" in snapshots else "",
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
    source_origin: Publication | None = None,
    application_origin: Publication | None = None,
    card_history_parent: ContentVersion | Literal["original"] | None = None,
) -> ContentVersion | None:
    if publication.id is None:
        raise ValueError("Editorial history requires a saved physical publication")
    existing = repo.get_publication_content(publication.id)
    if existing is not None:
        current = repo.publication_by_id(publication.id)
        if (
            existing.change_snapshot_id is not None
            and current is not None
            and current.delivery is not None
        ):
            repo.bind_publication_content(publication.id, existing.content_id)
        return existing
    prepared = prepare_publication_content(
        repo,
        clock,
        publication,
        formatter_version=formatter_version,
        language=language,
        card_history_parent=card_history_parent,
        application_origin=application_origin,
    )
    if prepared is None:
        return None
    version, snapshots = prepared
    try:
        with repo.atomic():
            for snapshot in snapshots:
                repo.save_content_snapshot(snapshot)
            if (
                source_origin is not None
                and source_origin.id == publication.id
                and source_origin.status is PublicationStatus.QUEUED
                and source_origin.attempts == 0
                and source_origin.delivery is not None
                and not source_origin.delivery.rendered_text
                and supported_source_context(source_origin)
                and supported_source_context(publication)
            ):
                audit = inspect_primary_content_sources(
                    repo,
                    bill_snapshot_id=version.bill_context_id,
                    change_snapshot_id=version.change_snapshot_id,
                )
                if audit.state == "invalid":
                    raise SourceContextInvalidError("Frozen primary source context is invalid")
                version = version.model_copy(
                    update={
                        "source_scope": audit.scope,
                        "source_observation_ids": audit.source_observation_ids,
                    }
                )
            if version.supplement_evidence is None:
                version = capture_content_applications(
                    repo, version, publication, origin=application_origin
                )
            if card_history_parent == "original" and publication.delivery is not None:
                bill = Bill.model_validate_json(publication.delivery.bill_json)
                reference = repo.find_card_primary_evidence(bill)
                if reference is not None:
                    marked = version.model_copy(update={"card_primary_evidence": reference})
                    try:
                        with repo.atomic():
                            repo.save_content_version(marked)
                            repo.bind_publication_content(publication.id, marked.content_id)
                    except CardPrimaryEvidenceError:
                        pass
                    else:
                        return marked
            repo.save_content_version(version)
            repo.bind_publication_content(publication.id, version.content_id)
    except SupplementContentEvidenceError:
        return None
    return version


def _first_direct_application(
    repo: BillRepository, publication: Publication, origin: Publication | None
) -> bool:
    plan = publication.delivery
    return not (
        origin is None
        or origin.id != publication.id
        or origin.status is not PublicationStatus.QUEUED
        or origin.attempts
        or origin.delivery is None
        or origin.delivery.rendered_text
        or publication.id is None
        or publication.kind
        not in (PublicationKind.STATUS_UPDATE, PublicationKind.AMENDMENTS_REVISION)
        or plan is None
        or not plan.rendered_text
        or plan.destination != "direct"
        or plan.publication_ids
        or plan.update_group
        or plan.sitting_group
        or plan.continuation_ids
        or plan.continuation_parent is not None
        or plan.continuation_root is not None
        or plan.absorbed_by is not None
        or plan.digest_event_ids
        or plan.digest_parts
        or plan.digest_ref
        or repo.digest_owner(publication.id) is not None
        or repo.get_publication_content(publication.id) is not None
    )


def capture_content_applications(
    repo: BillRepository,
    version: ContentVersion,
    publication: Publication,
    *,
    origin: Publication | None,
) -> ContentVersion:
    plan = publication.delivery
    if (
        plan is None
        or version.change_snapshot_id is None
        or not _first_direct_application(repo, publication, origin)
    ):
        return version
    change = StatusChange.model_validate_json(plan.change_json)
    if change.id is None:
        return version
    records: list[AmendmentsRecord | SupplementRecord] = (
        [change.amendments] if change.amendments is not None else []
    )
    target = ResultApplicationTarget(kind="publication", ref=str(publication.id))
    if publication.kind is PublicationKind.STATUS_UPDATE:
        target = ResultApplicationTarget(kind="status_change", ref=str(change.id))
        records.extend(change.supplements)
    identities = tuple(
        sorted(
            {
                identity
                for record in records
                if record.computation_id is not None
                for identity in repo.find_projection_application_ids(record.computation_id, target)
            }
        )
    )
    if not identities:
        return version
    return ContentVersion.model_validate_json(
        version.model_copy(
            update={
                "application_ref_profile": "amendment-projections-v1",
                "application_ids": identities,
            }
        ).model_dump_json()
    )


def prepare_publication_content(
    repo: BillRepository,
    clock: Clock,
    publication: Publication,
    *,
    formatter_version: str | None = None,
    language: str | None = None,
    card_history_parent: ContentVersion | Literal["original"] | None = None,
    application_origin: Publication | None = None,
) -> tuple[ContentVersion, tuple[ContentSnapshot, ...]] | None:
    if publication.id is None:
        raise ValueError("Editorial history requires a saved physical publication")
    if card_history_parent is not None:
        return _prepare_card_content(
            repo,
            clock,
            publication,
            parent=card_history_parent,
            language=language,
            formatter_version=formatter_version,
        )
    if publication.kind is PublicationKind.ACT_PUBLISHED:
        if repo.digest_owner(publication.id) is not None:
            return None
        return _prepare_act_published_content(
            publication,
            origin=application_origin,
            language=language,
            formatter_version=formatter_version,
        )
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
    supplement: tuple[int, SupplementContentEvidence] | None = None
    if (
        publication.kind is PublicationKind.STATUS_UPDATE
        and amendments_stage(change.new_stages) is None
    ):
        if change.amendments is None and not change.supplements:
            return _prepare_direct_stage_content(
                repo,
                clock,
                publication,
                change,
                origin=application_origin,
                formatter_version=formatter_version,
                language=language,
            )
        if (
            change.amendments is not None
            or len(change.supplements) != 1
            or change.id != publication.status_change_id
            or plan.continuation_index != 1
            or plan.edit_message_id is not None
            or plan.revision_of is not None
            or plan.primary_json
            or language is None
            or formatter_version is None
            or application_origin is None
            or application_origin.kind is not PublicationKind.STATUS_UPDATE
            or application_origin.delivery is None
            or application_origin.delivery.destination != "direct"
            or not _first_direct_application(repo, publication, application_origin)
        ):
            return None
        supplement = repo.find_supplement_content_evidence(change)
        if supplement is None:
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
    plan_snapshot = _plan_snapshot(plan)
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
        application_ref_profile="supplement-projection-v1" if supplement else None,
        application_ids=(supplement[0],) if supplement else (),
        supplement_evidence=supplement[1] if supplement else None,
        recorded_at=clock.now(),
    )
    return version, (bill_snapshot, change_snapshot, plan_snapshot)


def _prepare_act_published_content(
    publication: Publication,
    *,
    origin: Publication | None,
    language: str | None,
    formatter_version: str | None,
) -> tuple[ContentVersion, tuple[ContentSnapshot, ...]] | None:
    plan = publication.delivery
    if (
        origin is None
        or origin.id != publication.id
        or origin.status is not PublicationStatus.QUEUED
        or origin.attempts
        or origin.delivery is None
        or origin.delivery.rendered_text
        or publication.id is None
        or publication.status_change_id is not None
        or plan is None
        or not plan.rendered_text.strip()
        or utf16_length(plan.rendered_text) > MESSAGE_LIMIT
        or not plan.bill_json
        or plan.change_json
        or language not in ("ru", "en")
        or not formatter_version
        or len(formatter_version) > 128
        or not 0 < len(publication.channel_id) <= 256
        or len(publication.ref or "") > 512
    ):
        return None
    bill = Bill.model_validate_json(plan.bill_json)
    if bill.act is None or (bill.term, bill.number) != (publication.term, publication.number):
        return None
    bill_snapshot = editorial_bill_snapshot(plan.bill_json)
    plan_snapshot = _plan_snapshot(plan)
    if (
        not act_published_plan_matches(DeliveryPlan.model_validate_json(plan_snapshot.payload_json))
        or len(bill_snapshot.payload_json.encode())
        + len(plan_snapshot.payload_json.encode())
        + len(plan.rendered_text.encode())
        > MAX_ACT_PUBLISHED_CONTENT_BYTES
    ):
        return None
    version = ContentVersion(
        root_publication_id=publication.id,
        reason="original",
        language=language,
        formatter_version=formatter_version,
        bill_snapshot_id=bill_snapshot.snapshot_id,
        plan_snapshot_id=plan_snapshot.snapshot_id,
        rendered_text=plan.rendered_text,
        recorded_at=publication.created_at,
        act_published_evidence=ActPublishedContentEvidence(
            channel_id=publication.channel_id, ref=publication.ref
        ),
    )
    return version, (bill_snapshot, plan_snapshot)


def record_grouped_stage_content(
    repo: BillRepository,
    clock: Clock,
    origins: list[Publication],
    *,
    language: str,
    render: GroupedUpdateRender | None = None,
) -> None:
    """Record a qualified two-member first freeze within the caller's atomic group write."""
    if len(origins) != 2 or language not in ("ru", "en"):
        return
    snapshots: list[ContentSnapshot] = []
    witnesses: list[GroupedStageMember] = []
    common: DeliveryPlan | None = None
    for original in origins:
        identity = original.id
        before = original.delivery
        if (
            identity is None
            or original.kind is not PublicationKind.STATUS_UPDATE
            or original.status is not PublicationStatus.QUEUED
            or original.attempts
            or before is None
            or before.update_group
            or before.group_text
            or before.rendered_text
            or before.render_origin is not None
            or before.held_change_ids
            or before.with_act
            or repo.get_publication_content(identity) is not None
            or repo.digest_owner(identity) is not None
        ):
            return
        frozen = repo.publication_by_id(identity)
        if (
            frozen is None
            or frozen.delivery is None
            or frozen.status is not PublicationStatus.QUEUED
        ):
            return
        plan = frozen.delivery
        if (
            plan.reply_to is None
            or not direct_stage_plan_matches(grouped_stage_context(plan))
            or (plan.bill_json, plan.change_json) != (before.bill_json, before.change_json)
            or len(plan.update_group) != 2
            or not plan.group_text
        ):
            return
        if common is None:
            common = plan
        elif (plan.update_group, plan.group_text, plan.today) != (
            common.update_group,
            common.group_text,
            common.today,
        ):
            return
        change = StatusChange.model_validate_json(plan.change_json)
        bill = Bill.model_validate_json(plan.bill_json)
        if (
            change.id is None
            or change.id != original.status_change_id
            or (change.term, change.number) != (original.term, original.number)
            or (bill.term, bill.number) != (original.term, original.number)
            or not change.new_stages
            or amendments_stage(change.new_stages) is not None
            or change.amendments is not None
            or change.supplements
            or repo.get_status_change(change.id) != change
        ):
            return
        aliases = repo.card_aliases(original.channel_id, plan.reply_to)
        if len(aliases) != 1:
            return
        (anchor,) = aliases
        if (
            anchor.id is None
            or anchor.message_id != plan.reply_to
            or anchor.sent_at is None
            or anchor.sent_at > original.created_at
            or (anchor.term, anchor.number) != (original.term, original.number)
        ):
            return
        bill_snapshot = card_bill_snapshot(plan.bill_json)
        change_snapshot = ContentSnapshot(kind="change", payload_json=plan.change_json)
        snapshots.extend((bill_snapshot, change_snapshot))
        witnesses.append(
            GroupedStageMember(
                publication_id=identity,
                anchor_publication_id=anchor.id,
                anchor_message_id=plan.reply_to,
                status_change_id=change.id,
                bill_snapshot_id=bill_snapshot.snapshot_id,
                change_snapshot_id=change_snapshot.snapshot_id,
                original_bill_sha256=hashlib.sha256(plan.bill_json.encode()).hexdigest(),
            )
        )
    assert common is not None, "both qualified members provide frozen group plans"
    first, second = witnesses
    if (origins[0].term, origins[0].number) == (origins[1].term, origins[1].number) or tuple(
        item.publication_id for item in common.update_group
    ) != (first.publication_id, second.publication_id):
        return
    compact = ContentSnapshot(
        kind="plan", payload_json=grouped_stage_context(common).model_dump_json()
    )
    snapshots.append(compact)
    size = sum(len(snapshot.payload_json.encode()) for snapshot in snapshots) + len(
        common.group_text.encode()
    )
    if size > MAX_GROUPED_STAGE_CONTENT_BYTES:
        return
    render_witness = None
    expected_members = tuple(
        (origin.term, origin.number, member.anchor_message_id, member.status_change_id)
        for origin, member in zip(origins, witnesses, strict=True)
    )
    if render is not None and (render.text, render.members, render.today, render.language) == (
        common.group_text,
        expected_members,
        common.today,
        language,
    ):
        try:
            candidate = GroupedRenderWitness.from_render(render)
            if candidate is not None:
                candidate.bind(common.group_text, language)
                if (
                    size + len(candidate.model_dump_json().encode())
                    <= MAX_GROUPED_STAGE_CONTENT_BYTES
                ):
                    render_witness = candidate
        except ValueError:
            pass
    version = ContentVersion(
        root_publication_id=first.publication_id,
        reason="original",
        language=language,
        formatter_version=FORMATTER_VERSION,
        bill_snapshot_id=first.bill_snapshot_id,
        change_snapshot_id=first.change_snapshot_id,
        plan_snapshot_id=compact.snapshot_id,
        rendered_text=common.group_text,
        status_change_id=first.status_change_id,
        grouped_stage_evidence=GroupedStageContentEvidence(
            members=(first, second), render_witness=render_witness
        ),
        recorded_at=clock.now(),
    )
    for snapshot in snapshots:
        repo.save_content_snapshot(snapshot)
    repo.save_content_version(version)
    repo.bind_publication_content(first.publication_id, version.content_id)


def _prepare_direct_stage_content(
    repo: BillRepository,
    clock: Clock,
    publication: Publication,
    change: StatusChange,
    *,
    origin: Publication | None,
    formatter_version: str | None,
    language: str | None,
) -> tuple[ContentVersion, tuple[ContentSnapshot, ...]] | None:
    plan = publication.delivery
    if (
        publication.id is None
        or plan is None
        or plan.reply_to is None
        or not direct_stage_plan_matches(plan)
        or plan.held_change_ids
        or plan.with_act
        or plan.render_origin is not None
        or publication.status is not PublicationStatus.QUEUED
        or publication.attempts
        or language not in ("ru", "en")
        or not formatter_version
        or change.id is None
        or change.id != publication.status_change_id
        or (change.term, change.number) != (publication.term, publication.number)
        or len(change.new_stages) != 1
        or origin is None
        or origin.kind is not PublicationKind.STATUS_UPDATE
        or origin.delivery is None
        or origin.delivery.destination != "direct"
        or (origin.delivery.bill_json, origin.delivery.change_json)
        != (plan.bill_json, plan.change_json)
        or not _first_direct_application(repo, publication, origin)
        or repo.get_status_change(change.id) != change
    ):
        return None
    aliases = repo.card_aliases(publication.channel_id, plan.reply_to)
    if len(aliases) != 1:
        return None
    (anchor,) = aliases
    if (
        anchor.id is None
        or anchor.message_id is None
        or anchor.message_id <= 0
        or anchor.sent_at is None
        or anchor.sent_at > publication.created_at
        or (anchor.term, anchor.number) != (publication.term, publication.number)
    ):
        return None
    bill = Bill.model_validate_json(plan.bill_json)
    if (bill.term, bill.number) != (publication.term, publication.number):
        return None
    bill_snapshot = card_bill_snapshot(plan.bill_json)
    change_snapshot = ContentSnapshot(kind="change", payload_json=plan.change_json)
    plan_snapshot = _plan_snapshot(plan)
    snapshots = (bill_snapshot, change_snapshot, plan_snapshot)
    if (
        sum(len(snapshot.payload_json.encode()) for snapshot in snapshots)
        + len(plan.rendered_text.encode())
        > MAX_DIRECT_STAGE_CONTENT_BYTES
    ):
        return None
    return (
        ContentVersion(
            root_publication_id=publication.id,
            reason="original",
            language=language,
            bill_snapshot_id=bill_snapshot.snapshot_id,
            change_snapshot_id=change_snapshot.snapshot_id,
            plan_snapshot_id=plan_snapshot.snapshot_id,
            rendered_text=plan.rendered_text,
            formatter_version=formatter_version,
            status_change_id=change.id,
            direct_stage_evidence=DirectStageContentEvidence(
                anchor_publication_id=anchor.id,
                original_bill_sha256=hashlib.sha256(plan.bill_json.encode()).hexdigest(),
            ),
            recorded_at=clock.now(),
        ),
        snapshots,
    )


def _plan_snapshot(plan: DeliveryPlan) -> ContentSnapshot:
    semantic = plan.model_copy(update={"bill_json": "", "change_json": "", "rendered_text": ""})
    return ContentSnapshot(kind="plan", payload_json=semantic.model_dump_json())


def _prepare_card_content(
    repo: BillRepository,
    clock: Clock,
    publication: Publication,
    *,
    parent: ContentVersion | Literal["original"],
    language: str | None,
    formatter_version: str | None,
) -> tuple[ContentVersion, tuple[ContentSnapshot, ...]] | None:
    plan = publication.delivery
    if (
        publication.id is None
        or publication.status is not PublicationStatus.QUEUED
        or publication.attempts
        or plan is None
        or not plan.rendered_text
        or language not in ("ru", "en")
        or not formatter_version
        or repo.digest_owner(publication.id) is not None
    ):
        return None
    previous = parent if isinstance(parent, ContentVersion) else None
    root = repo.publication_by_id(previous.root_publication_id) if previous else publication
    if root is None:
        return None
    previous_ids: set[int] = set()
    if previous is not None:
        if (
            previous.change_snapshot_id is not None
            or previous.card_context_profile != "continuation-v1"
            or repo.get_content_version(previous.content_id) != previous
            or root.status is not PublicationStatus.SENT
        ):
            return None
        for physical in repo.list_content_publications(previous.root_publication_id):
            if (
                physical.id is not None
                and physical.status is PublicationStatus.SENT
                and physical.message_id == root.message_id
                and repo.get_publication_content(physical.id) == previous
            ):
                previous_ids.add(physical.id)
    bill_snapshot = card_bill_snapshot(plan.bill_json)
    plan_snapshot = _plan_snapshot(plan)
    version = ContentVersion(
        root_publication_id=previous.root_publication_id if previous else publication.id,
        previous_version_id=previous.content_id if previous else None,
        reason="card_revision" if previous else "original",
        language=language,
        bill_snapshot_id=bill_snapshot.snapshot_id,
        card_context_profile="continuation-v1",
        plan_snapshot_id=plan_snapshot.snapshot_id,
        rendered_text=plan.rendered_text,
        formatter_version=formatter_version,
        recorded_at=clock.now(),
    )
    if not card_physical_matches(version, publication, root, plan, previous_ids):
        return None
    return version, (bill_snapshot, plan_snapshot)
