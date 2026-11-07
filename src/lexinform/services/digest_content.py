"""Capture bounded digest content during its original atomic physical freeze."""

import logging

from pydantic import ValidationError

from lexinform.models import ContentSnapshot, ContentVersion, Digest
from lexinform.models.digest_content import (
    DigestContentBudgetError,
    DigestContentPart,
    DigestContentPlan,
)
from lexinform.ports import BillRepository
from lexinform.services.content_history import FORMATTER_VERSION

log = logging.getLogger(__name__)


def record_digest_content(
    repo: BillRepository, week: Digest, identities: tuple[int, ...], *, language: str | None
) -> None:
    if not identities:
        return
    if language not in ("ru", "en"):
        log.warning("digest content history omitted: publication=%s reason=language", identities[0])
        return
    parts = []
    root = repo.publication_by_id(identities[0])
    if root is None or root.ref is None:
        log.warning("digest content history omitted: publication=%s reason=invalid", identities[0])
        return
    try:
        for identity in identities:
            part = repo.publication_by_id(identity)
            if part is None or part.delivery is None:
                log.warning(
                    "digest content history omitted: publication=%s reason=invalid", identities[0]
                )
                return
            parts.append(
                DigestContentPart(
                    publication_id=identity,
                    text=part.delivery.rendered_text,
                    event_ids=part.delivery.digest_event_ids,
                    approve_label=part.delivery.approve_label,
                )
            )
        plan = DigestContentPlan(
            channel_id=root.channel_id, delivery_ref=root.ref, digest=week, parts=tuple(parts)
        )
        snapshot = ContentSnapshot(kind="plan", payload_json=plan.model_dump_json())
        version = ContentVersion(
            root_publication_id=identities[0],
            reason="original",
            language=language,
            plan_snapshot_id=snapshot.snapshot_id,
            formatter_version=FORMATTER_VERSION,
            digest_profile="digest-content-v1",
            recorded_at=root.created_at,
        )
    except ValidationError as exc:
        code = (
            "budget"
            if any(
                isinstance(issue.get("ctx", {}).get("error"), DigestContentBudgetError)
                for issue in exc.errors()
            )
            else "invalid"
        )
        log.warning("digest content history omitted: publication=%s reason=%s", identities[0], code)
        return
    repo.save_digest_content(snapshot, version)
