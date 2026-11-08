import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from tools.content_history_doctor import audit

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import Bill, Publication, PublicationKind
from lexinform.models.original_delivery_content import OriginalDeliveryPlan
from lexinform.models.reviews import (
    DocumentReview,
    DocumentReviewRecord,
    ReviewContext,
    ReviewDocument,
)
from lexinform.services import original_delivery_content
from lexinform.services.content_inspection import inspect_content
from tests.checkpoint_state import saved_state
from tests.harness import ELI, World, act
from tests.scenario.test_notice_remote_checkpoints import deliver, plan, queued


def watcher(monkeypatch: pytest.MonkeyPatch, *, multipart: bool) -> tuple[World, Publication]:
    world = World(profile="production", reader_attention="off")
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run()
    world.batch.resolve()
    world.run()
    statute = act()
    world.gateway.acts[ELI] = statute
    world.publish_act("3039")
    if multipart:
        render = world.publisher.render_delivery

        def long_act(publication: Publication) -> str:
            text = render(publication)
            return (
                text + "\n" + "Срок 30 дней; 200 PLN; старые заявления. " * 240
                if publication.kind is PublicationKind.ACT_PUBLISHED
                else text
            )

        monkeypatch.setattr(world.publisher, "render_delivery", long_act)
    world.clock.advance(days=1)
    assert world.run().acts_published == 1
    kind = PublicationKind.ACT_PUBLISHED if multipart else PublicationKind.IN_FORCE
    if not multipart:
        assert statute.entry_into_force is not None
        world.clock.current = datetime.combine(statute.entry_into_force, world.clock.now().timetz())
        assert world.run().in_force_posted == 1
    publication = world.publication("3039", kind)
    assert publication is not None, "the normal watcher publishes the notice"
    return world, publication


@pytest.mark.parametrize("multipart", [False, True])
def test_original_history_survives_cleanup(
    monkeypatch: pytest.MonkeyPatch, multipart: bool, tmp_path: Path
) -> None:
    world, publication = watcher(monkeypatch, multipart=multipart)
    assert publication.id is not None and publication.delivery is not None
    ids = (publication.id, *publication.delivery.continuation_ids)
    assert (len(ids) > 1) == multipart
    rows = [world.repo.publication_by_id(identity) for identity in ids]
    assert all(row is not None and row.message_id is not None for row in rows)
    expected_html = [
        message.text
        for message in world.publisher.sent
        if message.kind is publication.kind or message.kind is PublicationKind.MESSAGE_PART
    ]
    before = world.repo.dump()
    database = tmp_path / "notices.db"
    with closing(SqliteBillRepository(database)) as repo:
        repo.migrate()
        repo.restore(before)
    report = audit(database)
    assert report["status"] == "recorded" and not report["findings"]
    histories = {row["root_publication_id"]: row for row in report["histories"]}
    assert histories[publication.id]["current_content_id"] is not None
    if multipart:
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("DELETE FROM publication_content WHERE publication_id=?", (ids[-1],))
        corrupted = audit(database, publication_id=publication.id)
        assert corrupted["status"] == "failed"
        assert "invalid_original_delivery_history" in {row["code"] for row in corrupted["findings"]}
        assert corrupted["histories"][0]["current_content_id"] is None
    else:
        announcement = world.publication("3039", PublicationKind.ACT_PUBLISHED)
        assert announcement is not None and announcement.id is not None
        assert histories[announcement.id]["current_content_id"] is not None
    if multipart:
        child_id = ids[1]
        assert publication.message_id is not None
        correct = f'"reply_to":{publication.message_id},'
        wrong = f'"reply_to":{publication.message_id + 10000},'
        prefix = f'INSERT INTO "publications" VALUES({child_id},'
        lines = before.splitlines(keepends=True)
        indices = [index for index, line in enumerate(lines) if line.startswith(prefix)]
        assert len(indices) == 1 and correct in lines[indices[0]]
        lines[indices[0]] = lines[indices[0]].replace(correct, wrong, 1)
        with saved_state(before) as durable:
            with pytest.raises(ValueError, match="invalid_content_history"):
                durable.repo.restore("".join(lines))
            assert durable.repo.dump() == before
    cutoff = world.clock.now() + timedelta(days=999)
    world.repo.prune_history(before=cutoff, memo_before=cutoff)
    sql = world.repo.dump()
    cleaned = tmp_path / "retained-notices.db"
    with closing(SqliteBillRepository(cleaned)) as repo:
        repo.migrate()
        repo.restore(sql)
    retained = audit(cleaned, publication_id=publication.id)
    assert retained["status"] == "recorded" and not retained["findings"]
    assert (
        retained["histories"][0]["current_content_id"]
        == histories[publication.id]["current_content_id"]
    )
    for _ in range(2):
        with saved_state(sql) as durable:
            assert durable.repo.dump() == sql
            for identity, previous in zip(ids, rows, strict=True):
                assert previous is not None, "the fixture sent every physical slot"
                physical = durable.repo.publication_by_id(identity)
                assert physical is not None and physical.delivery is None
                assert physical.message_id == previous.message_id
                history = durable.repo.get_publication_content(identity)
                assert history is not None and history.original_delivery_evidence is not None
                view = inspect_content(durable.repo, physical)
                assert view is not None and view.current_id == history.content_id
                snapshot = durable.repo.get_content_snapshot(history.plan_snapshot_id)
                assert snapshot is not None, "history retains the exact physical plan"
                frozen = OriginalDeliveryPlan.model_validate_json(snapshot.payload_json)
                assert frozen.bindings == ids
                assert [part.html for part in frozen.parts] == expected_html


def test_original_history_budget_preserves_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    normal, original = watcher(monkeypatch, multipart=False)
    monkeypatch.setattr(original_delivery_content, "MAX_ORIGINAL_DELIVERY_BYTES", 1)
    limited, omitted = watcher(monkeypatch, multipart=False)
    assert omitted == original
    assert limited.publisher.sent == normal.publisher.sent
    assert omitted.id is not None
    assert limited.repo.get_publication_content(omitted.id) is None


def test_original_history_restores_without_embedded_review_source() -> None:
    case = queued(PublicationKind.HEARING_DEADLINE)
    world = case.world
    frozen = plan(world, case.identity)
    bill = Bill.model_validate_json(frozen.bill_json)
    raw_source = "Embedded review source that must not enter editorial history. " * 2500
    record = DocumentReviewRecord(
        review=DocumentReview(
            purpose="consultation_review",
            complete=True,
            summary="Editorial result",
            consultation_findings=(),
            effective_rules=(),
            unresolved=(),
        ),
        context=ReviewContext(
            number=bill.number,
            title="Consultation",
            purpose="consultation_review",
            documents=(
                ReviewDocument(
                    identity="official/notice",
                    url="https://example.test/official",
                    role="commission_minutes",
                    text=raw_source,
                    body=raw_source,
                    sha256="a" * 64,
                    normalized_sha256="b" * 64,
                ),
            ),
        ),
        created_at=world.clock.now(),
    )
    offered = frozen.model_copy(
        update={
            "bill_json": bill.model_copy(update={"consultation_review": record}).model_dump_json()
        }
    )
    assert world.repo.replace_unattempted_delivery(case.identity, offered)
    assert deliver(case)
    exact_html = world.publisher.sent[-1].text
    cutoff = world.clock.now() + timedelta(days=999)
    world.repo.prune_history(before=cutoff, memo_before=cutoff)
    sql = world.repo.dump()
    assert "Embedded review source that must not enter editorial history." not in sql
    with saved_state(sql) as durable:
        physical = durable.repo.publication_by_id(case.identity)
        assert physical is not None and physical.delivery is None
        version = durable.repo.get_publication_content(case.identity)
        assert version is not None and version.original_delivery_evidence is not None
        assert version.original_delivery_evidence.bill_context == "editorial-notice-v1"
        view = inspect_content(durable.repo, physical)
        assert view is not None and view.current_id == version.content_id
        assert view.summaries[0].full_context_available is False
        snapshot = durable.repo.get_content_snapshot(version.plan_snapshot_id)
        assert snapshot is not None, "physical HTML is separate from the Bill projection"
        assert (
            OriginalDeliveryPlan.model_validate_json(snapshot.payload_json).parts[0].html
            == exact_html
        )
        context = durable.repo.get_content_snapshot(version.bill_context_id)
        assert context is not None, "the version preserves the projected semantic Bill"
        review = Bill.model_validate_json(context.payload_json).consultation_review
        assert review is not None and review.context.documents[0].identity == "official/notice"
        assert review.context.documents[0].text == review.context.documents[0].body == ""
