import datetime as dt
import gzip
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    AmendmentRevisionApplicationWork,
    Amendments,
    AmendmentsRecord,
    Bill,
    BillStatus,
    ComputationResult,
    ContentSnapshot,
    ContentVersion,
    DeliveryPlan,
    DocumentWork,
    ProcessSummary,
    Publication,
    PublicationKind,
    PublicationStatus,
    ResultApplication,
    ResultApplicationTarget,
    StatusChange,
    TokenUsage,
    computation_id,
)

NOW = dt.datetime(2026, 10, 27, tzinfo=dt.UTC)
type CompletedState = tuple[str, Publication, AmendmentRevisionApplicationWork, dt.datetime]


def bind_version(
    repo: SqliteBillRepository,
    identity: int,
    plan: DeliveryPlan,
    root: int,
    previous: ContentVersion | None = None,
) -> ContentVersion:
    bill = ContentSnapshot(kind="bill", payload_json=plan.bill_json)
    change = ContentSnapshot(kind="change", payload_json=plan.change_json)
    semantic = plan.model_copy(update={"bill_json": "", "change_json": "", "rendered_text": ""})
    context = ContentSnapshot(kind="plan", payload_json=semantic.model_dump_json())
    for snapshot in (bill, change, context):
        repo.save_content_snapshot(snapshot)
    version = ContentVersion(
        root_publication_id=root,
        previous_version_id=previous.content_id if previous else None,
        reason="amendments_revision" if previous else "original",
        language="ru",
        formatter_version="fixture-v1",
        rendered_text=plan.rendered_text,
        bill_snapshot_id=bill.snapshot_id,
        change_snapshot_id=change.snapshot_id,
        plan_snapshot_id=context.snapshot_id,
        recorded_at=NOW,
    )
    repo.save_content_version(version)
    repo.bind_publication_content(identity, version.content_id)
    return version


@pytest.fixture(scope="module")
def completed_fixture() -> CompletedState:
    repo = SqliteBillRepository(":memory:")
    try:
        repo.migrate()
        bill = Bill(
            summary=ProcessSummary(
                term=10,
                number="1929",
                title="Projekt",
                document_type="ustawa",
                change_date=NOW,
            ),
            status=BillStatus.ANALYZED,
            first_seen_at=NOW,
            last_checked_at=NOW,
        )
        change = StatusChange(
            term=10,
            number="1929",
            old_fingerprint=None,
            new_fingerprint="revision-fixture",
            new_stages=[],
            detected_at=NOW,
        )
        change_id = repo.add_status_change(change)
        assert change_id is not None, "the fixture creates its original legislative event"
        change = change.model_copy(update={"id": change_id})
        plan = DeliveryPlan(
            bill_json=bill.model_dump_json(),
            change_json=change.model_dump_json(),
            rendered_text="Original",
            today=NOW.date(),
        )
        original = repo.create_publication(
            Publication(
                term=10,
                number="1929",
                channel_id="@test",
                kind=PublicationKind.STATUS_UPDATE,
                status=PublicationStatus.SENT,
                status_change_id=change_id,
                created_at=NOW,
                sent_at=NOW,
                message_id=10,
                delivery=plan,
            )
        )
        first = bind_version(repo, original, plan, original)
        result_id = computation_id("openai", "revision-fixture", "revision-request")
        record = AmendmentsRecord(
            amendments=Amendments(summary="Revision", affects_foreigners=True, confidence=1),
            model="fixture",
            prompt_version="fixture",
            source_url="https://example.test/amendments.pdf",
            source_kind="senate_amendments",
            created_at=NOW,
            computation_id=result_id,
            request_sha256="a" * 64,
        )
        repo.save_computation_result(
            ComputationResult(
                schema_version=2,
                provider="openai",
                batch_id="revision-fixture",
                custom_id="revision-request",
                purpose="amendments",
                term=10,
                number="1929",
                generation=None,
                collected_at=NOW,
                validation="validated",
                record=record,
                model="fixture",
                request_sha256="a" * 64,
                usage=TokenUsage(batch_input=10, batch_output=3),
            )
        )
        target = plan.model_copy(
            update={
                "revision_of": original,
                "edit_message_id": 10,
                "bill_json": "",
                "change_json": "",
                "rendered_text": "",
            }
        )
        target_snapshot = ContentSnapshot(kind="plan", payload_json=target.model_dump_json())
        repo.save_content_snapshot(target_snapshot)
        revised = plan.model_copy(
            update={
                "revision_of": original,
                "edit_message_id": 10,
                "rendered_text": "Revision",
                "change_json": change.model_copy(update={"amendments": record}).model_dump_json(),
            }
        )
        revision_id = repo.create_publication(
            Publication(
                term=10,
                number="1929",
                channel_id="@test",
                kind=PublicationKind.AMENDMENTS_REVISION,
                ref=f"amendments/{original}/revision-request",
                status=PublicationStatus.SENT,
                created_at=NOW,
                sent_at=NOW,
                message_id=10,
                delivery=revised,
            )
        )
        bind_version(repo, revision_id, revised, original, first)
        repo.save_result_application(
            ResultApplication(
                result_id=result_id,
                term=10,
                number="1929",
                generation=None,
                target=ResultApplicationTarget(kind="publication", ref=str(revision_id)),
                phase="projection",
                outcome="applied",
                reason="retained_result",
                observed_generation=None,
                observed_status=None,
                created_at=NOW,
            )
        )
        work = AmendmentRevisionApplicationWork(
            term=10,
            number="1929",
            result_id=result_id,
            request_key="revision-request",
            request_sha256="a" * 64,
            original_publication_id=original,
            channel_id="@test",
            message_id=10,
            source_url=record.source_url,
            bill_snapshot_id=first.bill_snapshot_id,
            change_snapshot_id=first.change_snapshot_id,
            plan_snapshot_id=target_snapshot.snapshot_id,
            created_at=NOW,
            next_at=NOW,
            state="complete",
            revision_publication_id=revision_id,
        )
        repo.save_revision_application_work(work)
        revision = repo.publication_by_id(revision_id)
        assert revision is not None, "the fixture stores its completed physical revision"
        return repo.dump(), revision, work, NOW
    finally:
        repo.close()


def snapshot_state(raw: str, directory: Path) -> Path:
    path = directory / "state.db"
    repo = SqliteBillRepository(path)
    try:
        repo.migrate()
        repo.restore(raw)
    finally:
        repo.close()
    return path


@pytest.mark.parametrize("status", ["queued", "failed", "pending", "unknown", "dismissed"])
def test_non_sent_revision_payloads_remain_protected(
    tmp_path: Path,
    status: str,
    completed_fixture: CompletedState,
) -> None:
    raw, revision, _, now = completed_fixture
    assert revision.id is not None, "the fixture stores its physical revision"
    path = snapshot_state(raw, tmp_path)
    with closing(sqlite3.connect(path)) as db:
        db.execute("UPDATE publications SET status=? WHERE id=?", (status, revision.id))
        db.commit()
    repo = SqliteBillRepository(path)
    try:
        before = repo.publication_by_id(revision.id)
        future = now + dt.timedelta(days=365)
        repo.prune_history(before=future, memo_before=future)
        assert repo.publication_by_id(revision.id) == before
    finally:
        repo.close()


@pytest.mark.parametrize("missing", ["binding", "work"])
def test_revision_without_positive_modern_proof_stays_protected(
    tmp_path: Path,
    missing: str,
    completed_fixture: CompletedState,
) -> None:
    raw, revision, work, now = completed_fixture
    assert revision.id is not None, "the fixture stores its physical revision"
    path = snapshot_state(raw, tmp_path)
    with closing(sqlite3.connect(path)) as db:
        if missing == "binding":
            db.execute("DELETE FROM publication_content WHERE publication_id=?", (revision.id,))
        else:
            db.execute("DELETE FROM document_work WHERE key=?", (work.key,))
        db.commit()
    repo = SqliteBillRepository(path)
    try:
        future = now + dt.timedelta(days=365)
        repo.prune_history(before=future, memo_before=future)
        assert repo.publication_by_id(revision.id) == revision
    finally:
        repo.close()


@pytest.mark.parametrize(
    "corruption", ["snapshot", "receipt", "version", "physical", "binding", "target"]
)
def test_known_revision_corruption_aborts_the_whole_prune(
    tmp_path: Path,
    corruption: str,
    completed_fixture: CompletedState,
) -> None:
    raw, revision, work, now = completed_fixture
    assert revision.id is not None, "the fixture stores its physical revision"
    path = snapshot_state(raw, tmp_path)
    with closing(sqlite3.connect(path)) as db:
        if corruption == "snapshot":
            db.execute(
                "UPDATE content_snapshots SET payload_json='{}' WHERE snapshot_id=?",
                (work.bill_snapshot_id,),
            )
        elif corruption == "receipt":
            db.execute(
                "DELETE FROM result_applications WHERE result_id=? AND phase='projection'",
                (work.result_id,),
            )
        elif corruption == "version":
            db.execute(
                "UPDATE content_versions SET root_publication_id=999999 WHERE content_id=(SELECT content_id FROM publication_content WHERE publication_id=?)",
                (revision.id,),
            )
        elif corruption == "binding":
            db.execute(
                "UPDATE publication_content SET content_id=(SELECT content_id FROM publication_content WHERE publication_id=?) WHERE publication_id=?",
                (work.original_publication_id, revision.id),
            )
        elif corruption == "target":
            db.execute("UPDATE publications SET message_id=message_id+1 WHERE id=?", (revision.id,))
        else:
            db.execute(
                "UPDATE publications SET delivery_json=json_set(delivery_json, '$.rendered_text', 'corrupt') WHERE id=?",
                (revision.id,),
            )
        db.commit()
    repo = SqliteBillRepository(path)
    try:
        before = repo.dump()
        future = now + dt.timedelta(days=365)
        with pytest.raises(ValueError):
            repo.prune_history(before=future, memo_before=future)
        assert repo.dump() == before
    finally:
        repo.close()


@pytest.mark.parametrize("dependency", ["continuation", "digest", "document_work"])
def test_existing_dependency_guards_protect_expired_revision_payloads(
    tmp_path: Path,
    dependency: str,
    completed_fixture: CompletedState,
) -> None:
    raw, revision, work, now = completed_fixture
    assert revision.id is not None, "the fixture stores its physical revision"
    assert revision.id is not None and revision.delivery is not None, (
        "the fixture has a complete revision before introducing dependent work"
    )
    path = snapshot_state(raw, tmp_path)
    with closing(sqlite3.connect(path)) as db:
        if dependency == "document_work":
            original = db.execute(
                "SELECT status_change_id FROM publications WHERE id=?",
                (work.original_publication_id,),
            ).fetchone()
            assert original is not None and original[0] is not None, (
                "the original belongs to a persisted status change"
            )
            db.execute(
                "UPDATE publications SET status_change_id=? WHERE id=?",
                (original[0], revision.id),
            )
            db.execute(
                "INSERT INTO document_work (key,term,number,state,next_at,work_json) VALUES (?,?,?,?,?,?)",
                (
                    "dependent",
                    10,
                    "1929",
                    "waiting",
                    now.isoformat(),
                    DocumentWork(
                        key="dependent",
                        term=10,
                        number="1929",
                        change_id=original[0],
                        bill_json="{}",
                        detail_json="{}",
                        stage_json="{}",
                        created_at=now,
                        next_at=now,
                    ).model_dump_json(),
                ),
            )
        else:
            root = revision.id if dependency == "continuation" else None
            child_plan = revision.delivery.model_copy(update={"continuation_root": root})
            child = db.execute(
                "INSERT INTO publications (term,number,channel_id,kind,status,created_at,delivery_json,ref) VALUES (10,'1929','@test','message_part','unknown',?,?,?)",
                (now.isoformat(), child_plan.model_dump_json(), "dependent/1"),
            ).lastrowid
            if dependency == "digest":
                db.execute(
                    "UPDATE publications SET delivery_json=json_set(delivery_json, '$.digest_parts',json_array(?)) WHERE id=?",
                    (child, revision.id),
                )
        db.commit()
    repo = SqliteBillRepository(path)
    try:
        before = repo.publication_by_id(revision.id)
        future = now + dt.timedelta(days=365)
        repo.prune_history(before=future, memo_before=future)
        assert repo.publication_by_id(revision.id) == before
    finally:
        repo.close()


def test_recent_completed_revision_is_not_expired(
    tmp_path: Path, completed_fixture: CompletedState
) -> None:
    raw, revision, _, now = completed_fixture
    assert revision.id is not None, "the fixture stores its physical revision"
    repo = SqliteBillRepository(snapshot_state(raw, tmp_path))
    try:
        repo.prune_history(before=now, memo_before=now)
        assert repo.publication_by_id(revision.id) == revision
    finally:
        repo.close()


def test_prune_retains_exact_history_and_restore_with_idempotent_cleanup(
    tmp_path: Path,
    completed_fixture: CompletedState,
) -> None:
    raw, revision, work, now = completed_fixture
    assert revision.id is not None, "the fixture stores its physical revision"
    assert revision.id is not None, "the fixture persists its revision identity"
    repo = SqliteBillRepository(snapshot_state(raw, tmp_path))
    try:
        version = repo.get_publication_content(revision.id)
        versions = repo.list_content_versions(work.original_publication_id)
        future = now + dt.timedelta(days=365)
        assert repo.prune_history(before=future, memo_before=future)["delivery_payloads"] == 2
        expected = revision.model_copy(update={"delivery": None})
        assert repo.publication_by_id(revision.id) == expected
        assert repo.list_amendment_revisions(work.original_publication_id) == [expected]
        assert repo.get_publication_content(revision.id) == version
        assert repo.list_content_versions(work.original_publication_id) == versions
        assert repo.get_revision_application_work(work.key) == work
        pruned = repo.dump()
        assert len(pruned.encode()) < len(raw.encode())
        assert len(gzip.compress(pruned.encode(), mtime=0)) < len(
            gzip.compress(raw.encode(), mtime=0)
        )
        assert repo.prune_history(before=future, memo_before=future)["delivery_payloads"] == 0
        assert repo.dump() == pruned
        repo.restore(pruned)
        assert repo.dump() == pruned
        assert repo.list_amendment_revisions(work.original_publication_id) == [expected]
    finally:
        repo.close()
