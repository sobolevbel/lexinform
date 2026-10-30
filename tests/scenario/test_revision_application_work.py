import datetime as dt
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from pydantic import ValidationError
from tools.work_doctor import audit

from lexinform.adapters import sqlite_repo
from lexinform.adapters.document_work import validate_document_work
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    AmendmentRevisionApplicationWork,
    AmendmentsRecord,
    ComputationResult,
    ContentSnapshot,
    DeliveryPlan,
    DocumentWork,
    Publication,
    PublicationKind,
    PublicationStatus,
    ResultApplication,
    ResultApplicationTarget,
    TokenUsage,
    computation_id,
)
from lexinform.models.revision_work import decode_document_work
from tests.fakes import make_amendments
from tests.harness import World
from tests.scenario.test_amendments_command import SENATE, published_world
from tests.scenario.test_request_doctor import snapshot

MARKER = "NEVER-EMIT-REVISION-APPLICATION-CONTEXT"


@pytest.fixture
def work_case() -> tuple[World, AmendmentRevisionApplicationWork]:
    world, original = published_world()
    assert original.id is not None and original.delivery is not None, (
        "the public fixture stores a sent original and its frozen delivery"
    )
    now = world.clock.now()
    request_key = "a" * 64
    request_sha = "b" * 64
    source_url = "https://api.test/retained-amendments.pdf"
    result_id = computation_id("openai", "retained-amendments", request_key)
    result = ComputationResult(
        schema_version=2,
        provider="openai",
        batch_id="retained-amendments",
        custom_id=request_key,
        purpose="amendments",
        term=10,
        number="1929",
        generation=None,
        collected_at=now,
        request_sha256=request_sha,
        validation="validated",
        record=AmendmentsRecord(
            amendments=make_amendments(summary=MARKER),
            model="controlled",
            prompt_version="controlled",
            source_url=source_url,
            source_kind="senate_amendments",
            created_at=now,
            computation_id=result_id,
            request_sha256=request_sha,
        ),
        model="controlled",
        usage=TokenUsage(batch_input=17, batch_output=9),
    )
    world.repo.save_computation_result(result)
    delivery = original.delivery.model_copy(
        update={
            "revision_of": original.id,
            "edit_message_id": original.message_id,
            "amendment_key": "c" * 64,
            "bill_json": "",
            "change_json": "",
            "rendered_text": "",
        }
    )
    content = world.repo.get_publication_content(original.id)
    assert content is not None and original.message_id is not None, (
        "the actual sent update has an editorial binding and physical target"
    )
    plan = ContentSnapshot(kind="plan", payload_json=delivery.model_dump_json())
    world.repo.save_content_snapshot(plan)
    return world, AmendmentRevisionApplicationWork(
        term=10,
        number="1929",
        result_id=result_id,
        request_key=request_key,
        request_sha256=request_sha,
        original_publication_id=original.id,
        channel_id=original.channel_id,
        message_id=original.message_id,
        source_url=source_url,
        bill_snapshot_id=content.bill_snapshot_id,
        change_snapshot_id=content.change_snapshot_id,
        plan_snapshot_id=plan.snapshot_id,
        created_at=now,
        next_at=now,
    )


def legacy_work(world: World, work: AmendmentRevisionApplicationWork) -> DocumentWork:
    original = world.repo.publication_by_id(work.original_publication_id)
    assert original is not None and original.status_change_id is not None, (
        "the original amendments update has its stored legislative event"
    )
    return DocumentWork(
        key=f"amendments/{original.status_change_id}",
        term=10,
        number="1929",
        change_id=original.status_change_id,
        bill_json=world.bill("1929").model_dump_json(),
        detail_json=world.gateway.details["1929"].model_dump_json(),
        stage_json=SENATE.model_dump_json(),
        created_at=world.clock.now(),
        next_at=world.clock.now(),
    )


def test_mixed_work_roundtrip_preserves_legacy_and_never_activates_application(
    work_case: tuple[World, AmendmentRevisionApplicationWork], tmp_path: Path
) -> None:
    world, work = work_case
    legacy = legacy_work(world, work)
    legacy_json = legacy.model_dump_json()
    before_publications = world.repo.list_amendment_revisions(work.original_publication_id)
    world.repo.save_document_work(legacy)
    world.repo.save_revision_application_work(work)
    saved = world.repo.dump()
    world.repo.save_revision_application_work(work)
    assert world.repo.dump() == saved
    assert world.repo.list_document_work(due=work.next_at) == [legacy]
    assert world.repo.list_revision_application_work(due=work.next_at) == [work]
    assert (
        world.repo.list_revision_application_work(due=work.next_at - dt.timedelta(seconds=1)) == []
    )
    world.repo.restore(saved)
    world.repo.restore(world.repo.dump())
    assert world.repo.get_revision_application_work(work.key) == work
    assert world.repo.list_document_work(due=work.next_at)[0].model_dump_json() == legacy_json
    assert "kind" not in json.loads(legacy_json)
    assert world.repo.list_amendment_revisions(work.original_publication_id) == before_publications
    path = snapshot(world, tmp_path)
    before = path.read_bytes()
    report = audit(path, observed_at=world.clock.now())
    assert report["status"] == "recorded" and report["findings"] == []
    assert report["summary"]["revision_application_waiting"] == 1
    assert MARKER not in json.dumps(report)
    assert path.read_bytes() == before


def test_frozen_identity_cannot_change_while_lifecycle_can_block_and_resume(
    work_case: tuple[World, AmendmentRevisionApplicationWork],
) -> None:
    world, work = work_case
    world.repo.save_revision_application_work(work)
    frozen = world.repo.dump()
    changed = work.model_copy(update={"created_at": work.created_at - dt.timedelta(seconds=1)})
    with pytest.raises(ValueError, match="frozen"):
        world.repo.save_revision_application_work(changed)
    assert world.repo.dump() == frozen
    blocked = work.model_copy(
        update={"state": "blocked", "attempts": 1, "problem": "target_changed"}
    )
    world.repo.save_revision_application_work(blocked)
    assert world.repo.get_revision_application_work(work.key) == blocked
    assert not world.repo.list_revision_application_work(due=work.next_at)
    with pytest.raises(ValueError, match="decrease"):
        world.repo.save_revision_application_work(work)
    resumed = blocked.model_copy(update={"state": "waiting", "problem": ""})
    world.repo.save_revision_application_work(resumed)
    assert world.repo.list_revision_application_work(due=work.next_at) == [resumed]


def test_runtime_does_not_consume_or_dispatch_inert_application_work(
    work_case: tuple[World, AmendmentRevisionApplicationWork],
) -> None:
    world, work = work_case
    world.repo.save_revision_application_work(work)
    world.clock.advance(days=30)
    world.run()
    assert world.repo.get_revision_application_work(work.key) == work
    assert not world.repo.list_amendment_revisions(work.original_publication_id)
    assert not world.repo.list_document_work(due=world.clock.now())


@pytest.mark.parametrize("field", ["request", "plan", "bill"])
def test_known_parent_association_is_required_before_work_write(
    work_case: tuple[World, AmendmentRevisionApplicationWork], field: str
) -> None:
    world, work = work_case
    if field == "request":
        changed = work.model_copy(update={"request_sha256": "d" * 64})
    else:
        identity = work.plan_snapshot_id if field == "plan" else work.bill_snapshot_id
        original = world.repo.get_content_snapshot(identity)
        assert original is not None, "the fixture stores all referenced snapshots"
        value = json.loads(original.payload_json)
        if field == "plan":
            value["edit_message_id"] = work.message_id + 1
        else:
            value["summary"]["title"] = "Different historical title"
        replacement = ContentSnapshot(kind=original.kind, payload_json=json.dumps(value))
        world.repo.save_content_snapshot(replacement)
        changed = work.model_copy(update={f"{field}_snapshot_id": replacement.snapshot_id})
    before = world.repo.dump()
    with pytest.raises(ValueError, match="mismatch"):
        world.repo.save_revision_application_work(changed)
    assert world.repo.dump() == before


def test_revision_work_is_bounded_and_does_not_accept_inline_context(
    work_case: tuple[World, AmendmentRevisionApplicationWork],
) -> None:
    _, work = work_case
    with pytest.raises(ValueError, match="8192"):
        AmendmentRevisionApplicationWork.model_validate(
            work.model_dump() | {"source_url": "🙂" * 4096}
        )
    with pytest.raises(ValueError, match="Extra inputs"):
        AmendmentRevisionApplicationWork.model_validate(work.model_dump() | {"bill_json": MARKER})


@pytest.mark.parametrize(
    ("query", "code"),
    [
        ("UPDATE document_work SET term=11", "document_owner_mismatch"),
        ("UPDATE document_work SET next_at='broken'", "invalid_document_schedule"),
        (
            "UPDATE document_work SET work_json=json_set(work_json,'$.kind','future')",
            "invalid_document_work",
        ),
        ("DELETE FROM computation_results", "revision_application_result_missing"),
        ("UPDATE computation_results SET number='4040'", "revision_application_result_mismatch"),
        (
            "DELETE FROM content_snapshots WHERE kind='plan'",
            "revision_application_snapshot_missing",
        ),
        (
            "UPDATE content_snapshots SET payload_json='{}' WHERE kind='bill'",
            "revision_application_snapshot_mismatch",
        ),
        (
            "UPDATE content_versions SET bill_snapshot_id='different'",
            "revision_application_content_mismatch",
        ),
    ],
)
def test_shared_doctor_restore_rejects_corruption_without_touching_target(
    work_case: tuple[World, AmendmentRevisionApplicationWork],
    tmp_path: Path,
    query: str,
    code: str,
) -> None:
    world, work = work_case
    world.repo.save_revision_application_work(work)
    path = snapshot(world, tmp_path)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(query)
        connection.commit()
        damaged = (
            "\n".join(connection.iterdump())
            + f"\nPRAGMA user_version = {world.repo.schema_version};\n"
        )
    before = world.repo.dump()
    with pytest.raises(ValueError, match=code):
        world.repo.restore(damaged)
    assert world.repo.dump() == before
    stored = path.read_bytes()
    report = audit(path)
    assert code in {finding["code"] for finding in report["findings"]}
    assert MARKER not in json.dumps(report)
    assert path.read_bytes() == stored


def test_unknown_kind_never_falls_through_the_legacy_decoder(
    work_case: tuple[World, AmendmentRevisionApplicationWork],
) -> None:
    world, work = work_case
    legacy = legacy_work(world, work)
    for kind in (None, "future", "amendment_revision_application"):
        raw = legacy.model_dump() | {"kind": kind}
        with pytest.raises(ValueError):
            decode_document_work(json.dumps(raw, default=str))
    with pytest.raises(ValidationError):
        DocumentWork.model_validate_json(work.model_dump_json())


@pytest.mark.parametrize("failure", [KeyboardInterrupt(), SystemExit(13)])
def test_restore_cancellation_preserves_original_exception_and_target(
    work_case: tuple[World, AmendmentRevisionApplicationWork],
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
) -> None:
    world, work = work_case
    world.repo.save_revision_application_work(work)
    before = world.repo.dump()

    def cancel(connection: sqlite3.Connection) -> None:
        raise failure

    monkeypatch.setattr(sqlite_repo, "validate_document_work", cancel)
    with pytest.raises(type(failure)) as caught:
        world.repo.restore(before)
    assert caught.value is failure and world.repo.dump() == before


def test_shared_validation_preserves_row_factory_and_propagates_source_sql_errors(
    work_case: tuple[World, AmendmentRevisionApplicationWork], tmp_path: Path
) -> None:
    world, work = work_case
    world.repo.save_revision_application_work(work)
    path = snapshot(world, tmp_path)
    with closing(sqlite3.connect(path)) as connection:
        assert connection.row_factory is None
        validate_document_work(connection)
        assert connection.row_factory is None
        connection.execute("DROP TABLE computation_results")
        connection.commit()
    with (
        closing(SqliteBillRepository(path)) as repository,
        pytest.raises(sqlite3.OperationalError, match="computation_results"),
    ):
        repository.get_revision_application_work(work.key)


def test_pending_work_survives_payload_cleanup_and_restore(
    work_case: tuple[World, AmendmentRevisionApplicationWork],
) -> None:
    world, work = work_case
    world.repo.save_revision_application_work(work)
    paid = world.repo.get_computation_result(work.result_id)
    world.clock.advance(days=365)
    world.repo.prune_history(before=world.clock.now(), memo_before=world.clock.now())
    world.repo.restore(world.repo.dump())
    assert world.repo.get_revision_application_work(work.key) == work
    assert world.repo.get_computation_result(work.result_id) == paid
    assert not world.repo.list_document_work(due=world.clock.now())


def test_complete_work_requires_actual_revision_and_receipt_and_cannot_reopen(
    work_case: tuple[World, AmendmentRevisionApplicationWork],
) -> None:
    world, work = work_case
    world.repo.save_revision_application_work(work)
    publication = world.repo.create_publication(
        Publication(
            term=work.term,
            number=work.number,
            kind=PublicationKind.AMENDMENTS_REVISION,
            status=PublicationStatus.QUEUED,
            channel_id=work.channel_id,
            ref=f"amendments/{work.original_publication_id}/{work.request_key}",
            created_at=world.clock.now(),
            delivery=DeliveryPlan(
                bill_json="",
                revision_of=work.original_publication_id,
                edit_message_id=work.message_id,
            ),
        )
    )
    complete = work.model_copy(update={"state": "complete", "revision_publication_id": publication})
    with pytest.raises(ValueError, match="receipt_missing"):
        world.repo.save_revision_application_work(complete)
    world.repo.save_result_application(
        ResultApplication(
            result_id=work.result_id,
            term=work.term,
            number=work.number,
            generation=None,
            target=ResultApplicationTarget(kind="publication", ref=str(publication)),
            phase="projection",
            outcome="applied",
            reason="retained_result",
            observed_generation=None,
            observed_status=None,
            created_at=world.clock.now(),
        )
    )
    world.repo.save_revision_application_work(complete)
    world.repo.restore(world.repo.dump())
    assert world.repo.get_revision_application_work(work.key) == complete
    with pytest.raises(ValueError, match="reopened"):
        world.repo.save_revision_application_work(work)


def test_absent_original_payload_is_unknown_while_retained_snapshot_refs_remain_valid(
    work_case: tuple[World, AmendmentRevisionApplicationWork], tmp_path: Path
) -> None:
    world, work = work_case
    world.repo.save_revision_application_work(work)
    path = snapshot(world, tmp_path)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "DELETE FROM publication_content WHERE publication_id=?",
            (work.original_publication_id,),
        )
        connection.execute(
            "UPDATE publications SET delivery_json=NULL WHERE id=?", (work.original_publication_id,)
        )
        connection.commit()
        raw = (
            "\n".join(connection.iterdump())
            + f"\nPRAGMA user_version = {world.repo.schema_version};\n"
        )
    world.repo.restore(raw)
    assert world.repo.get_revision_application_work(work.key) == work
    report = audit(path)
    owners = [owner for owner in report["owners"] if owner["kind"] == "revision_application"]
    assert report["status"] == "recorded" and owners[0]["original_context_witness"] == "unknown"
    assert world.repo.get_publication_content(work.original_publication_id) is None
