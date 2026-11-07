import hashlib
import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest
from tools.content_history_doctor import audit

from lexinform.models import ContentSnapshot, ContentVersion
from lexinform.models.digest_content import (
    DigestRenderedPart,
    DigestRenderedPartRef,
    DigestRenderedPlan,
    digest_event_owner_sha256,
)
from lexinform.services.content_history import FORMATTER_VERSION, restore_content_delivery
from lexinform.services.content_inspection import inspect_content_page
from lexinform.telegram_text import MESSAGE_LIMIT, utf16_length
from tests.checkpoint_state import saved_state
from tests.scenario.test_digest_remote_checkpoints import (
    REF,
    pending_event,
    saved_digest,
    service_for,
)


def test_native_budget_gap_and_rendered_history_after_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    world, event = pending_event()
    assert event.id is not None, "the tracked event has its durable source ID"
    service = service_for(world)
    week = service.build(REF)
    assert week.cards, "the tracked bill has a published card"
    large = week.model_copy(
        update={"cards": (week.cards[0].model_copy(update={"title": "x" * 70_000}),)}
    )
    monkeypatch.setattr(service, "build", lambda *args, **kwargs: large)
    (native,) = world.publisher.render_digest_parts(large)
    assert utf16_length(native.text) <= MESSAGE_LIMIT and native.event_ids == (event.id,)
    assert "x" * 71 not in native.text
    assert service.publish(REF).published
    root = saved_digest(world)
    assert root.id is not None and root.ref is not None and root.delivery is not None
    assert root.delivery.rendered_text == native.text
    assert world.repo.get_publication_content(root.id) is None
    baseline = world.repo.dump()
    expiry = world.clock.now() + timedelta(days=180)
    with saved_state(baseline) as ordinary:
        ordinary.repo.prune_history(before=expiry, memo_before=expiry)
        expired = ordinary.repo.publication_by_id(root.id)
        assert expired is not None and expired.delivery is None
        assert ordinary.repo.get_publication_content(root.id) is None

    part = DigestRenderedPart(
        publication_id=root.id,
        text=native.text,
        event_ids=native.event_ids,
        event_owner_sha256=digest_event_owner_sha256(
            [(event.id, event.term, event.number, event.kind.value)]
        ),
    )
    part_snapshot = ContentSnapshot(kind="plan", payload_json=part.model_dump_json())
    plan = DigestRenderedPlan(
        channel_id=root.channel_id,
        delivery_ref=root.ref,
        week_ref=large.ref,
        since=large.since,
        until=large.until,
        upstream_digest_sha256=hashlib.sha256(large.model_dump_json().encode()).hexdigest(),
        parts=(
            DigestRenderedPartRef(publication_id=root.id, snapshot_id=part_snapshot.snapshot_id),
        ),
    )
    plan_snapshot = ContentSnapshot(kind="plan", payload_json=plan.model_dump_json())
    version = ContentVersion(
        root_publication_id=root.id,
        reason="original",
        language="ru",
        plan_snapshot_id=plan_snapshot.snapshot_id,
        formatter_version=FORMATTER_VERSION,
        digest_profile="digest-rendered-v1",
        recorded_at=root.created_at,
    )
    world.repo.save_content_snapshot(part_snapshot)
    world.repo.save_content_snapshot(plan_snapshot)
    world.repo.save_content_version(version)
    with closing(sqlite3.connect(":memory:")) as database:
        unbound = world.repo.dump()
        database.executescript(unbound)
        database.execute(
            "INSERT INTO publication_content VALUES(?,?)", (root.id, version.content_id)
        )
        initial = "\n".join([*database.iterdump(), unbound.splitlines()[-1]])
    with saved_state(initial) as first:
        first.repo.prune_history(before=expiry, memo_before=expiry)
        clean = first.repo.dump()
        with saved_state(clean) as restored:
            assert restored.repo.dump() == clean
            publication = restored.repo.publication_by_id(root.id)
            assert publication is not None and publication.delivery is None
            assert restored.repo.get_publication_content(root.id) == version
            assert restored.repo.get_content_snapshot(part_snapshot.snapshot_id) == part_snapshot
            assert [item.id for item in restored.repo.digest_sources(root.id)] == [event.id]
            page = inspect_content_page(restored.repo, publication, page=1)
            assert page.version is not None and page.version.digest_exact_parts_available
            assert page.version.digest_upstream_semantics == "not_retained"
            assert page.version.full_context_available is False
            with pytest.raises(ValueError, match="Digest history cannot restore a Bill delivery"):
                restore_content_delivery(restored.repo, version)
            doctor_path = tmp_path / "digest.db"
            with closing(sqlite3.connect(doctor_path)) as doctor_db:
                doctor_db.executescript(clean)
            report = audit(doctor_path, publication_id=root.id)
            assert report["summary"]["digest_exact_rendered_parts"] == 1
            assert report["summary"]["digest_upstream_semantics_not_retained"] == 1
            for mutation, identity in (
                ("DELETE FROM content_snapshots WHERE snapshot_id=?", part_snapshot.snapshot_id),
                ("UPDATE publications SET number='other' WHERE id=?", event.id),
            ):
                with closing(sqlite3.connect(":memory:")) as corrupt:
                    corrupt.executescript(clean)
                    corrupt.execute(mutation, (identity,))
                    broken = "\n".join([*corrupt.iterdump(), clean.splitlines()[-1]])
                with pytest.raises(ValueError, match="invalid_content_history"):
                    restored.repo.restore(broken)
                assert restored.repo.dump() == clean
