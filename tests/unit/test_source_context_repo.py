from datetime import datetime

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.content_sources import SOURCE_CONTEXT_INVALID
from lexinform.models import (
    DeliveryPlan,
    ProcessDetail,
    Publication,
    PublicationKind,
    PublicationStatus,
)
from tests.scenario.test_source_context_delivery import queued


def test_reserved_upsert_mark_and_group_mutation_preserve_all_rows(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> None:
    ordinary = queued(repo, process_3039, now, error="ordinary", ref="normal")
    blocked = queued(repo, process_3039, now)
    assert blocked.id is not None and ordinary.id is not None, "queued returns stored identities"
    before = repo.dump()
    assert repo.create_publication(blocked.model_copy(update={"error": None})) == blocked.id
    assert repo.dump() == before
    with pytest.raises(ValueError, match="explicit validated repair"):
        repo.mark_publication(blocked.id, PublicationStatus.FAILED, error="generic")
    with pytest.raises(ValueError, match="source_context_invalid"):
        repo.tag_update_group((ordinary.id, blocked.id), "new-group")
    assert not repo.replace_unsent_update_delivery(
        blocked.status_change_id or 0, "@test", DeliveryPlan(bill_json="{}")
    )
    assert not repo.replace_unattempted_delivery(blocked.id, DeliveryPlan(bill_json="{}"))
    assert repo.dump() == before
    repo.mark_publication(ordinary.id, PublicationStatus.FAILED, error="ordinary")
    assert repo.publication_by_id(ordinary.id) is not None


def test_blocked_digest_owner_cannot_confirm_clean_members(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> None:
    source = queued(repo, process_3039, now, error=None)
    assert source.id is not None and source.delivery is not None, "queued stores its source payload"
    assert repo.replace_unattempted_delivery(
        source.id, source.delivery.model_copy(update={"destination": "digest_only"})
    )
    owner = Publication(
        term=0,
        number="__digest__",
        kind=PublicationKind.DIGEST,
        status=PublicationStatus.QUEUED,
        channel_id="@test",
        ref="2026-W43",
        created_at=now,
        delivery=DeliveryPlan(bill_json="", digest_event_ids=(source.id,), digest_ref="2026-W43"),
    )
    identity = repo.create_publication(owner)
    repo.reserve_digest_events(identity, (source.id,))
    repo.mark_publication(
        identity, PublicationStatus.QUEUED, error=SOURCE_CONTEXT_INVALID, count_attempt=False
    )
    before = repo.dump()
    with pytest.raises(ValueError, match="digest"):
        repo.complete_digest_events(identity, message_id=12, sent_at=now)
    assert repo.dump() == before
    assert repo.publication_by_id(source.id) == repo.digest_sources(identity)[0]
