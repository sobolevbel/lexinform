"""Explicit dispatch for one already-reserved grouped edit."""

from collections.abc import Callable

from lexinform.errors import StateCheckpointError
from lexinform.models import PublicationStatus
from lexinform.ports import BillRepository, Clock, StateCheckpoint, VerifiedEditOutcome


def dispatch_reserved_grouped_edit(
    repo: BillRepository,
    clock: Clock,
    edit: Callable[[int, int, str], VerifiedEditOutcome],
    checkpoint: StateCheckpoint,
    publication_id: int,
    *,
    content_id: str,
    base_content_id: str,
    approved_sha256: str,
    chat_id: int,
) -> PublicationStatus | None:
    """Dispatch an exact approved slot once through the required durable checkpoint."""
    claim = repo.claim_grouped_stage_edit(
        publication_id,
        content_id=content_id,
        base_content_id=base_content_id,
        approved_sha256=approved_sha256,
        chat_id=chat_id,
    )
    if claim is None:
        return None
    checkpoint()
    outcome = None
    try:
        if repo.grouped_stage_edit_matches(claim):
            outcome = edit(claim.chat_id, claim.message_id, claim.html)
    except Exception:
        outcome = None
    try:
        status = repo.finish_grouped_stage_edit(claim, outcome, at=clock.now())
    except Exception:
        raise StateCheckpointError("Could not persist the grouped edit outcome") from None
    if status is None:
        raise StateCheckpointError("The grouped edit lost its pending slot")
    checkpoint()
    return status
