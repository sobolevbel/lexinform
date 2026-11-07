import sqlite3
from contextlib import closing
from datetime import datetime, timedelta

import pytest

from lexinform.models import (
    DIGEST_NUMBER,
    DIGEST_TERM,
    ContentSnapshot,
    ContentVersion,
    DigestPart,
    Publication,
    PublicationKind,
    PublicationStatus,
)
from lexinform.models.digest_content import DigestContentPart, DigestContentPlan
from lexinform.ports import PublishResult
from lexinform.services import digest as digest_module
from lexinform.services.content_inspection import inspect_content, inspect_content_page
from tests.checkpoint_state import saved_state
from tests.scenario.test_digest_remote_checkpoints import (
    REF,
    pending_event,
    saved_digest,
    service_for,
)


def future_digest(
    monkeypatch: pytest.MonkeyPatch,
    *,
    advancing_clock: bool = False,
    repeated_draft: bool = False,
) -> tuple[str, Publication, ContentVersion, DigestContentPlan]:
    monkeypatch.setattr(digest_module, "record_digest_content", lambda *args, **kwargs: None)
    world, source = pending_event()
    assert source.id is not None, "normal tracking retains the digest event owner"
    service = service_for(world)
    week = service.build(REF)
    original = world.publisher.publish_prepared

    def render(_week: object, *, draft: bool = False) -> tuple[DigestPart, ...]:
        return (
            DigestPart(text="Saved introduction"),
            DigestPart(text="Saved event: 30 dni.", event_ids=() if draft else (source.id,)),
            DigestPart(text="Saved conclusion"),
        )

    def send(publication: Publication) -> PublishResult:
        return original(publication)

    monkeypatch.setattr(world.publisher, "render_digest_parts", render)
    monkeypatch.setattr(world.publisher, "publish_prepared", send)
    if advancing_clock:
        clock_now = world.clock.now

        def advance() -> datetime:
            value = clock_now()
            world.clock.advance(microseconds=1)
            return value

        monkeypatch.setattr(world.clock, "now", advance)
    if repeated_draft:
        draft_channel = world.container.drafts_channel_id()
        assert service.draft(REF).drafted
        first = world.repo.get_publication(
            DIGEST_TERM, DIGEST_NUMBER, PublicationKind.DIGEST, draft_channel, ref=REF
        )
        assert first is not None and first.id is not None, "normal draft has a durable root"
        ref = f"{REF}:draft:{first.id}"
        assert service.draft(REF).drafted
        root = world.repo.get_publication(
            DIGEST_TERM, DIGEST_NUMBER, PublicationKind.DIGEST, draft_channel, ref=ref
        )
        assert root is not None, "repeated draft owns its physical reference"
    else:
        assert service.publish(REF).published
        root = saved_digest(world)
    assert root.id is not None and root.delivery is not None, "normal freeze creates a digest root"
    parts = tuple(world.repo.publication_by_id(identity) for identity in root.delivery.digest_parts)
    assert all(part is not None and part.delivery is not None for part in parts)
    plan = DigestContentPlan(
        channel_id=root.channel_id,
        delivery_ref=root.ref,
        digest=week,
        parts=tuple(
            DigestContentPart(
                publication_id=part.id,
                text=part.delivery.rendered_text,
                event_ids=part.delivery.digest_event_ids,
                approve_label=part.delivery.approve_label,
            )
            for part in parts
            if part is not None and part.id is not None and part.delivery is not None
        ),
    )
    snapshot = ContentSnapshot(kind="plan", payload_json=plan.model_dump_json())
    version = ContentVersion(
        root_publication_id=root.id,
        reason="original",
        language="ru",
        plan_snapshot_id=snapshot.snapshot_id,
        formatter_version="telegram-format-v2",
        digest_profile="digest-content-v1",
        recorded_at=root.created_at,
    )
    with closing(sqlite3.connect(":memory:")) as db:
        db.executescript(world.repo.dump())
        db.execute(
            "INSERT INTO content_snapshots VALUES(?,?,?)",
            (snapshot.snapshot_id, snapshot.kind, snapshot.payload_json),
        )
        db.execute(
            "INSERT INTO content_versions VALUES(?,?,?,?,?,?,?)",
            (
                version.content_id,
                root.id,
                None,
                None,
                None,
                snapshot.snapshot_id,
                version.model_dump_json(),
            ),
        )
        db.executemany(
            "INSERT INTO publication_content VALUES(?,?)",
            [(part.publication_id, version.content_id) for part in plan.parts],
        )
        script = "\n".join([*db.iterdump(), world.repo.dump().splitlines()[-1]])
    return script, root, version, plan


@pytest.mark.parametrize(
    ("advancing_clock", "repeated_draft"), [(False, False), (True, False), (True, True)]
)
def test_digest_history_restores_after_operational_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    advancing_clock: bool,
    repeated_draft: bool,
) -> None:
    script, root, version, plan = future_digest(
        monkeypatch, advancing_clock=advancing_clock, repeated_draft=repeated_draft
    )
    if advancing_clock:
        with closing(sqlite3.connect(":memory:")) as raw:
            raw.executescript(script)
            stamps = [
                row[0]
                for row in raw.execute(
                    "SELECT created_at FROM publications WHERE kind='digest' AND id>=? ORDER BY id",
                    (plan.parts[0].publication_id,),
                )
            ]
            assert len(set(stamps)) == len(plan.parts)
    with saved_state(script) as first:
        exact = first.repo.dump()
        with saved_state(exact) as second:
            assert second.repo.dump() == exact
            for part in plan.parts:
                assert second.repo.get_publication_content(part.publication_id) == version
            view = inspect_content(second.repo, root)
            assert view is not None and view.current_id == version.content_id
            assert inspect_content_page(second.repo, root, page=1).status == "available"
            second.repo.prune_history(
                before=version.recorded_at + timedelta(days=180),
                memo_before=version.recorded_at + timedelta(days=180),
            )
            expired = second.repo.publication_by_id(plan.parts[0].publication_id)
            assert expired is not None and expired.delivery is None
            with saved_state(second.repo.dump()) as pruned:
                assert pruned.repo.get_content_version(version.content_id) == version
                history = inspect_content(pruned.repo, root)
                assert history is not None and history.current_id == version.content_id


@pytest.mark.parametrize(
    "mutation", ["order", "events", "binding", "owner", "text", "receipt", "uncertain_receipt"]
)
def test_digest_history_rejects_tampered_profile_atomically(
    mutation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    script, root, version, plan = future_digest(monkeypatch)
    with closing(sqlite3.connect(":memory:")) as db:
        db.executescript(script)
        if mutation == "order":
            db.execute(
                "UPDATE publications SET ref='other' WHERE id=?", (plan.parts[1].publication_id,)
            )
        elif mutation == "events":
            db.execute(
                "DELETE FROM digest_event_links WHERE owner_publication_id=?",
                (plan.parts[1].publication_id,),
            )
        elif mutation == "binding":
            db.execute(
                "DELETE FROM publication_content WHERE publication_id=?",
                (plan.parts[-1].publication_id,),
            )
        elif mutation == "owner":
            db.execute(
                "UPDATE publications SET channel_id='@other' WHERE id=?",
                (plan.parts[1].publication_id,),
            )
        elif mutation == "text":
            db.execute(
                "UPDATE publications SET delivery_json=json_set(delivery_json,'$.rendered_text','bad')"
                " WHERE id=?",
                (plan.parts[1].publication_id,),
            )
        elif mutation == "uncertain_receipt":
            db.execute(
                "UPDATE publications SET status='unknown' WHERE id=?",
                (plan.parts[1].publication_id,),
            )
        else:
            db.execute(
                "UPDATE publications SET message_id=? WHERE id=?",
                (root.message_id, plan.parts[1].publication_id),
            )
        broken = "\n".join([*db.iterdump(), script.splitlines()[-1]])
    with saved_state(script) as unchanged:
        exact = unchanged.repo.dump()
        with pytest.raises(ValueError, match="invalid_content_history"):
            unchanged.repo.restore(broken)
        assert unchanged.repo.dump() == exact


@pytest.mark.parametrize(
    "status",
    [
        PublicationStatus.UNKNOWN,
        PublicationStatus.PENDING,
        PublicationStatus.FAILED,
        PublicationStatus.DISMISSED,
    ],
)
def test_digest_partial_receipts_cannot_confirm_full_content(
    status: PublicationStatus, monkeypatch: pytest.MonkeyPatch
) -> None:
    script, root, version, plan = future_digest(monkeypatch)
    with closing(sqlite3.connect(":memory:")) as db:
        db.executescript(script)
        db.execute(
            "UPDATE publications SET status=?,message_id=NULL,sent_at=NULL WHERE id=?",
            (status.value, plan.parts[-1].publication_id),
        )
        changed = "\n".join([*db.iterdump(), script.splitlines()[-1]])
    with saved_state(changed) as restored:
        history = inspect_content(restored.repo, root)
        assert history is not None and history.current_id is None
        assert restored.repo.get_content_version(version.content_id) == version
