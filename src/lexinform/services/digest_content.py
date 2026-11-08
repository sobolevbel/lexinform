"""Capture bounded digest content during its original atomic physical freeze."""

import hashlib
import logging

from pydantic import ValidationError

from lexinform.models import ContentSnapshot, ContentVersion, Digest
from lexinform.models.digest_content import (
    DigestContentBudgetError,
    DigestContentPart,
    DigestContentPlan,
    DigestHistoryProfile,
    DigestRenderedPart,
    DigestRenderedPartRef,
    DigestRenderedPlan,
    digest_event_owner_sha256,
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
        part_snapshots: tuple[ContentSnapshot, ...] = ()
        profile: DigestHistoryProfile = plan.profile
    except ValidationError as exc:
        if not any(
            isinstance(issue.get("ctx", {}).get("error"), DigestContentBudgetError)
            for issue in exc.errors()
        ):
            log.warning(
                "digest content history omitted: publication=%s reason=invalid", identities[0]
            )
            return
        try:
            rendered, part_snapshots = _rendered_history(
                repo, week, root.channel_id, root.ref, parts
            )
        except ValidationError:
            log.warning(
                "digest content history omitted: publication=%s reason=invalid", identities[0]
            )
            return
        snapshot = ContentSnapshot(kind="plan", payload_json=rendered.model_dump_json())
        profile = rendered.profile
    version = ContentVersion(
        root_publication_id=identities[0],
        reason="original",
        language=language,
        plan_snapshot_id=snapshot.snapshot_id,
        formatter_version=FORMATTER_VERSION,
        digest_profile=profile,
        recorded_at=root.created_at,
    )
    if part_snapshots:
        repo.save_digest_content(snapshot, version, part_snapshots=part_snapshots)
    else:
        repo.save_digest_content(snapshot, version)


def _rendered_history(
    repo: BillRepository,
    week: Digest,
    channel_id: str,
    delivery_ref: str,
    parts: list[DigestContentPart],
) -> tuple[DigestRenderedPlan, tuple[ContentSnapshot, ...]]:
    snapshots: list[ContentSnapshot] = []
    refs: list[DigestRenderedPartRef] = []
    for part in parts:
        sources = {event.id: event for event in repo.digest_sources(part.publication_id)}
        if sources.keys() != set(part.event_ids):
            raise ValueError("digest rendered parts require exact event sources")
        owners = [
            (
                identity,
                sources[identity].term,
                sources[identity].number,
                sources[identity].kind.value,
            )
            for identity in part.event_ids
        ]
        payload = DigestRenderedPart(
            publication_id=part.publication_id,
            text=part.text,
            approve_label=part.approve_label,
            event_ids=part.event_ids,
            event_owner_sha256=digest_event_owner_sha256(owners),
        )
        snapshot = ContentSnapshot(kind="plan", payload_json=payload.model_dump_json())
        snapshots.append(snapshot)
        refs.append(
            DigestRenderedPartRef(
                publication_id=part.publication_id, snapshot_id=snapshot.snapshot_id
            )
        )
    return DigestRenderedPlan(
        channel_id=channel_id,
        delivery_ref=delivery_ref,
        week_ref=week.ref,
        since=week.since,
        until=week.until,
        upstream_digest_sha256=hashlib.sha256(week.model_dump_json().encode()).hexdigest(),
        parts=tuple(refs),
    ), tuple(snapshots)
