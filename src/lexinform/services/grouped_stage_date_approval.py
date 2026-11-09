"""Preview and explicitly approve one exact grouped stage date correction."""

from collections.abc import Callable
from dataclasses import dataclass

from lexinform.grouped_update_preview import GroupedUpdateRender
from lexinform.models import ContentVersion, PublicationKind, PublicationStatus
from lexinform.models.content import GroupedStageRevisionEvidence
from lexinform.models.grouped_stage_date import GroupedStageDateCorrection
from lexinform.models.grouped_update_witness import GroupedRenderWitness
from lexinform.ports import BillRepository, Clock, StateCheckpoint, VerifiedEditOutcome
from lexinform.services.grouped_edit_dispatch import dispatch_reserved_grouped_edit
from lexinform.services.grouped_stage_date_candidate import (
    GroupedStageBulletRenderer,
    build_grouped_stage_date_candidate,
)
from lexinform.services.grouped_update_preview import preview_retained_grouped_update


@dataclass(frozen=True)
class GroupedStageDatePreview:
    correction: GroupedStageDateCorrection
    candidate_sha256: str
    chat_id: int
    message_id: int
    html: str
    before_line: str
    after_line: str


@dataclass(frozen=True)
class GroupedStageDateApprovalResult:
    publication_id: int
    status: PublicationStatus | None


def _prepare(
    repo: BillRepository,
    formatter: GroupedStageBulletRenderer,
    correction: GroupedStageDateCorrection,
) -> tuple[GroupedStageDatePreview, ContentVersion, GroupedUpdateRender] | None:
    candidate = build_grouped_stage_date_candidate(repo, formatter, correction)
    if candidate is None:
        return None
    root = repo.publication_by_id(correction.root_publication_id)
    base = repo.get_content_version(correction.expected_content_id)
    if (
        root is None
        or root.kind is not PublicationKind.STATUS_UPDATE
        or root.status is not PublicationStatus.SENT
        or root.telegram_chat_id is None
        or not -(2**52) < root.telegram_chat_id < 0
        or root.message_id is None
        or root.message_id <= 0
        or base is None
        or (evidence := base.grouped_stage_evidence) is None
    ):
        return None
    sibling = repo.publication_by_id(evidence.members[1].publication_id)
    if (
        sibling is None
        or sibling.kind is not PublicationKind.STATUS_UPDATE
        or sibling.status is not PublicationStatus.SENT
        or sibling.channel_id != root.channel_id
        or (sibling.telegram_chat_id, sibling.message_id)
        != (root.telegram_chat_id, root.message_id)
    ):
        return None
    for index, member in enumerate(evidence.members):
        if member.publication_id != correction.member_publication_id:
            continue
        preview = preview_retained_grouped_update(
            repo,
            correction.root_publication_id,
            candidate,
            owner=candidate.members[index],
            expected_content_id=correction.expected_content_id,
            expected_sha256=correction.expected_sha256,
        )
        if preview is None or preview.html != candidate.text or base.rendered_text is None:
            return None
        changes = [
            (before, after)
            for before, after in zip(
                base.rendered_text.split("\n"), candidate.text.split("\n"), strict=True
            )
            if before != after
        ]
        if len(changes) != 1:
            return None
        ((before, after),) = changes
        return (
            GroupedStageDatePreview(
                correction,
                preview.candidate_sha256,
                root.telegram_chat_id,
                root.message_id,
                preview.html,
                before,
                after,
            ),
            base,
            candidate,
        )
    return None


def preview_grouped_stage_date(
    repo: BillRepository,
    formatter: GroupedStageBulletRenderer,
    correction: GroupedStageDateCorrection,
) -> GroupedStageDatePreview | None:
    try:
        prepared = _prepare(repo, formatter, correction)
    except ValueError:
        return None
    return prepared[0] if prepared is not None else None


def approve_grouped_stage_date(
    repo: BillRepository,
    clock: Clock,
    formatter: GroupedStageBulletRenderer,
    edit: Callable[[int, int, str], VerifiedEditOutcome],
    checkpoint: StateCheckpoint | None,
    correction: GroupedStageDateCorrection,
    *,
    approved_sha256: str,
    chat_id: int,
    message_id: int,
    enabled: bool = False,
    candidate_refs_enabled: bool = False,
) -> GroupedStageDateApprovalResult | None:
    if not enabled or checkpoint is None or type(chat_id) is not int or type(message_id) is not int:
        return None
    try:
        prepared = _prepare(repo, formatter, correction)
        if prepared is None:
            return None
        preview, base, candidate = prepared
        evidence = base.grouped_stage_evidence
        if evidence is None or (preview.candidate_sha256, preview.chat_id, preview.message_id) != (
            approved_sha256,
            chat_id,
            message_id,
        ):
            return None
        revision = evidence.model_copy(
            update={
                "render_witness": GroupedRenderWitness.from_render(candidate),
                "revision": GroupedStageRevisionEvidence(
                    member_publication_id=correction.member_publication_id,
                    target_message_id=message_id,
                ),
            }
        )
        version = ContentVersion.model_validate(
            base.model_dump()
            | {
                "reason": "grouped_stage_revision",
                "previous_version_id": base.content_id,
                "rendered_text": candidate.text,
                "grouped_stage_evidence": revision.model_dump(),
                "recorded_at": clock.now(),
            }
        )
    except ValueError:
        return None
    try:
        slot = repo.reserve_grouped_stage_revision(
            version,
            expected_content_id=base.content_id,
            expected_base_sha256=correction.expected_sha256,
            approved_sha256=approved_sha256,
            expected_chat_id=chat_id,
            candidate_refs_enabled=candidate_refs_enabled,
        )
    except ValueError:
        return None
    if slot is None:
        return None
    status = dispatch_reserved_grouped_edit(
        repo,
        clock,
        edit,
        checkpoint,
        slot,
        content_id=version.content_id,
        base_content_id=base.content_id,
        approved_sha256=approved_sha256,
        chat_id=chat_id,
        allow_rejected=True,
    )
    return GroupedStageDateApprovalResult(slot, status)
