"""Resolve confirmed grouped history for an offline member-only HTML preview."""

from dataclasses import dataclass

from lexinform.grouped_update_preview import (
    GroupedUpdateRender,
    UpdateOwner,
    preview_grouped_update,
)
from lexinform.models import PublicationStatus
from lexinform.ports import BillRepository
from lexinform.services.content_inspection import inspect_content


@dataclass(frozen=True)
class OfflineGroupedUpdatePreview:
    html: str
    baseline_content_id: str
    baseline_sha256: str
    candidate_sha256: str


def preview_retained_grouped_update(
    repo: BillRepository,
    root_publication_id: int,
    candidate: GroupedUpdateRender,
    *,
    owner: UpdateOwner,
    expected_content_id: str,
    expected_sha256: str,
) -> OfflineGroupedUpdatePreview | None:
    """Read the exact confirmed root and preview one already-rendered candidate."""
    try:
        root = repo.publication_by_id(root_publication_id)
        if (
            root is None
            or root.id != root_publication_id
            or root.status is not PublicationStatus.SENT
            or root.message_id is None
            or root.message_id <= 0
        ):
            return None
        history = inspect_content(repo, root)
        if (
            history is None
            or history.integrity != "valid"
            or history.ambiguous
            or history.root_publication_id != root_publication_id
            or history.current_id != expected_content_id
        ):
            return None
        version = repo.get_content_version(expected_content_id)
        if (
            version is None
            or version.root_publication_id != root_publication_id
            or version.content_id != expected_content_id
            or version.rendered_text is None
            or version.language is None
            or version.grouped_stage_evidence is None
            or (witness := version.grouped_stage_evidence.render_witness) is None
        ):
            return None
        baseline = witness.bind(version.rendered_text, version.language)
        html = preview_grouped_update(
            baseline, candidate, owner=owner, expected_sha256=expected_sha256
        )
    except ValueError:
        return None
    if html is None:
        return None
    return OfflineGroupedUpdatePreview(html, version.content_id, baseline.sha256, candidate.sha256)
