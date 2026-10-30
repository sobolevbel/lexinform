import datetime as dt
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Literal

import pytest
from tools.work_doctor import audit

from lexinform.errors import LlmUnavailableError
from lexinform.models import (
    PRE_PRINT_PREFIX,
    RCL_PREFIX,
    WYKAZ_PREFIX,
    AmendmentRevisionApplicationWork,
    AmendmentsRecord,
    BatchStatus,
    BillStatus,
    DigestItemMeta,
    LlmBatch,
    LlmBatchItem,
    OutcomeStatus,
    Publication,
    PublicationStatus,
    ResultApplication,
)
from lexinform.services.revision_applications import AmendmentRevisionApplications
from tests.harness import World, summary
from tests.scenario.test_amendments_command import published_world
from tests.scenario.test_request_doctor import snapshot


def queued_revision() -> tuple[World, int]:
    world, original = published_world()
    assert original.id is not None, "the public fixture stores the sent correction target"
    world.command("/amendments 1929")
    world.run(track=False)
    world.batch.resolve()
    return world, original.id


def collected_work(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[World, AmendmentRevisionApplicationWork]:
    world, publication_id = queued_revision()

    def hold(service: AmendmentRevisionApplications) -> None:
        pass

    with monkeypatch.context() as patch:
        patch.setattr(AmendmentRevisionApplications, "drain", hold)
        world.analysis.collect_batches()
    work = world.repo.list_revision_application_work(publication_id=publication_id)
    assert len(work) == 1 and work[0].state == "waiting"
    assert not world.repo.list_open_llm_batches()
    return world, work[0]


@pytest.mark.parametrize("point", ["work", "consumed"])
def test_collect_fault_rolls_back_paid_work_memo_and_consumed_together(
    monkeypatch: pytest.MonkeyPatch, point: str
) -> None:
    world, publication_id = queued_revision()
    memo = world.repo.load_analysis_memo()

    def fail_work(work: AmendmentRevisionApplicationWork) -> None:
        raise RuntimeError("controlled collect work fault")

    def fail_consumed(
        batch_id: str, custom_id: str, *, consumed_at: dt.datetime, result: object = None
    ) -> None:
        raise RuntimeError("controlled collect consumed fault")

    with monkeypatch.context() as patch:
        patch.setattr(
            world.repo,
            "save_revision_application_work" if point == "work" else "mark_llm_batch_item_consumed",
            fail_work if point == "work" else fail_consumed,
        )
        with pytest.raises(RuntimeError, match="controlled collect"):
            world.analysis.collect_batches()
    assert not world.repo.list_computation_results(10, "1929")
    assert not world.repo.list_revision_application_work()
    assert world.repo.load_analysis_memo() == memo
    assert world.repo.list_open_llm_batches() and not world.analysis.calls
    world.analysis.collect_batches()
    assert len(world.repo.list_amendment_revisions(publication_id)) == 1
    assert len(world.repo.list_computation_results(10, "1929")) == len(world.analysis.calls) == 1


@pytest.mark.parametrize("point", ["publication", "receipt", "completion"])
def test_application_fault_retains_paid_consumed_work_for_provider_free_recovery(
    monkeypatch: pytest.MonkeyPatch, point: str
) -> None:
    world, publication_id = queued_revision()
    save_work = world.repo.save_revision_application_work

    def fail_publication(publication: Publication) -> int:
        raise RuntimeError("controlled application publication fault")

    def fail_receipt(application: ResultApplication) -> None:
        raise RuntimeError("controlled application receipt fault")

    def fail_completion(work: AmendmentRevisionApplicationWork) -> None:
        if work.state == "complete":
            raise RuntimeError("controlled application completion fault")
        save_work(work)

    with monkeypatch.context() as patch:
        if point == "publication":
            patch.setattr(world.repo, "create_publication", fail_publication)
        elif point == "receipt":
            patch.setattr(world.repo, "save_result_application", fail_receipt)
        else:
            patch.setattr(world.repo, "save_revision_application_work", fail_completion)
        with pytest.raises(RuntimeError, match="controlled application"):
            world.analysis.collect_batches()
    paid = world.repo.list_computation_results(10, "1929")
    calls = list(world.analysis.calls)
    work = world.repo.list_revision_application_work(publication_id=publication_id)
    assert len(paid) == len(work) == len(calls) == 1
    cached = world.repo.load_analysis_memo()[work[0].request_key]
    assert AmendmentsRecord.model_validate_json(cached) == paid[0].record
    assert calls[0].batched and calls[0].input_tokens == paid[0].usage.batch_input
    assert calls[0].output_tokens == paid[0].usage.batch_output
    assert not world.repo.list_open_llm_batches()
    assert not world.repo.list_amendment_revisions(publication_id)
    assert not world.repo.list_result_applications(10, "1929")
    original = world.repo.publication_by_id(publication_id)
    world.clock.advance(days=365)
    world.repo.prune_history(before=world.clock.now(), memo_before=world.clock.now())
    world.repo.restore(world.repo.dump())
    world.analysis.collect_batches()
    assert world.repo.list_computation_results(10, "1929") == paid
    assert world.analysis.calls == calls
    assert len(world.repo.list_amendment_revisions(publication_id)) == 1
    completed = world.repo.get_revision_application_work(work[0].key)
    assert completed is not None and completed.state == "complete", (
        "the second application commits publication, receipt and completion"
    )
    now_original = world.repo.publication_by_id(publication_id)
    assert original is not None and now_original is not None, (
        "original identities survive retention"
    )
    assert now_original.model_copy(update={"delivery": original.delivery}) == original
    stable = world.repo.dump()
    world.analysis.collect_batches()
    assert world.repo.dump() == stable and world.analysis.calls == calls


@pytest.mark.parametrize("state", ["waiting", "blocked"])
def test_pending_application_blocks_source_preparation_and_force(
    monkeypatch: pytest.MonkeyPatch, state: Literal["waiting", "blocked"]
) -> None:
    world, work = collected_work(monkeypatch)
    pending = work.model_copy(
        update={"state": state, "next_at": work.next_at + dt.timedelta(days=30)}
    )
    world.repo.save_revision_application_work(pending)

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("pending work must block before source reads and preparation")

    monkeypatch.setattr(world.analysis, "senate_amendments_document", refuse)
    monkeypatch.setattr(world.batch, "prepare_request", refuse)
    outcome = world.container.amendments_service().request(
        world.bill("1929"), publication_id=work.original_publication_id, force_id="later-force"
    )
    assert outcome.status is OutcomeStatus.QUEUED
    assert len(world.batch.submitted) == 1
    assert world.repo.get_revision_application_work(work.key) == pending


def test_provider_poll_outage_cannot_starve_local_pending_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, work = collected_work(monkeypatch)
    paid = world.repo.list_computation_results(10, "1929")
    calls = list(world.analysis.calls)
    now = world.clock.now()
    world.repo.save_llm_batch(
        LlmBatch(
            batch_id="unavailable-unrelated",
            provider="anthropic",
            call_kind="name",
            submitted_at=now,
            request_count=1,
            model="controlled",
            estimated_cost_usd=0,
            status="submitted",
        ),
        [
            LlmBatchItem(
                batch_id="unavailable-unrelated",
                custom_id="unrelated",
                term=10,
                number="1929",
                call_kind="name",
                meta=DigestItemMeta(memo_key="unrelated"),
            )
        ],
    )

    def outage(batch_id: str) -> BatchStatus:
        raise LlmUnavailableError("controlled provider poll outage")

    monkeypatch.setattr(world.batch, "poll", outage)
    with pytest.raises(LlmUnavailableError, match="controlled provider poll outage"):
        world.analysis.collect_batches()
    completed = world.repo.get_revision_application_work(work.key)
    assert completed is not None and completed.state == "complete", (
        "the caller drains persisted work before unrelated provider polling"
    )
    assert world.repo.list_computation_results(10, "1929") == paid
    assert world.analysis.calls == calls
    assert len(world.repo.list_amendment_revisions(work.original_publication_id)) == 1


@pytest.mark.parametrize("status", [BillStatus.ANALYSIS_PENDING, BillStatus.SKIPPED_PREFILTER])
def test_primary_reset_or_skip_preserves_original_historical_correction(
    monkeypatch: pytest.MonkeyPatch, status: BillStatus
) -> None:
    world, work = collected_work(monkeypatch)
    paid = world.repo.get_computation_result(work.result_id)
    world.repo.reset_bill(10, "1929", status)
    world.analysis.collect_batches()
    assert world.repo.get_computation_result(work.result_id) == paid
    assert len(world.repo.list_amendment_revisions(work.original_publication_id)) == 1
    assert world.bill("1929").status is status


def test_changed_physical_target_blocks_without_rebasing_or_paying_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    world, work = collected_work(monkeypatch)
    path = snapshot(world, tmp_path)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "UPDATE publications SET message_id=message_id+1 WHERE id=?",
            (work.original_publication_id,),
        )
        connection.commit()
        raw = (
            "\n".join(connection.iterdump())
            + f"\nPRAGMA user_version = {world.repo.schema_version};\n"
        )
    world.repo.restore(raw)
    paid = world.repo.get_computation_result(work.result_id)
    world.analysis.collect_batches()
    blocked = world.repo.get_revision_application_work(work.key)
    assert (
        blocked is not None and blocked.state == "blocked" and blocked.problem == "target_changed"
    )
    assert blocked.message_id == work.message_id
    assert not world.repo.list_amendment_revisions(work.original_publication_id)
    assert world.repo.get_computation_result(work.result_id) == paid
    world.repo.restore(world.repo.dump())


@pytest.mark.parametrize(
    "number", [f"{RCL_PREFIX}1", f"{WYKAZ_PREFIX}UC104", f"{PRE_PRINT_PREFIX}1"]
)
def test_nonprocess_admission_stops_before_reading_or_preparing(
    monkeypatch: pytest.MonkeyPatch, number: str
) -> None:
    world, _ = published_world()
    current = world.bill("1929")
    bill = current.model_copy(
        update={"summary": current.summary.model_copy(update={"number": number})}
    )

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("nonprocess admission must stop before preparation")

    monkeypatch.setattr(world.analysis, "senate_amendments_document", refuse)
    monkeypatch.setattr(world.batch, "prepare_request", refuse)
    outcome = world.container.amendments_service().request(bill, force_id="force")
    assert outcome.status is OutcomeStatus.ERROR
    assert not world.batch.submitted


def test_legacy_unadmitted_snapshot_retains_paid_answer_and_explicit_problem(
    tmp_path: Path,
) -> None:
    world, _ = queued_revision()
    batch = world.repo.list_open_llm_batches()[0]
    path = snapshot(world, tmp_path)
    with closing(sqlite3.connect(path)) as connection:
        row = connection.execute(
            "SELECT meta_json FROM llm_batch_items WHERE batch_id=?", (batch.batch_id,)
        ).fetchone()
        assert row is not None, "the fixture stores the frozen legacy target metadata"
        meta = json.loads(row[0])
        bill = json.loads(meta["amendment_revision"]["delivery"]["bill_json"])
        bill["summary"]["number"] = f"{RCL_PREFIX}1"
        meta["amendment_revision"]["delivery"]["bill_json"] = json.dumps(bill)
        connection.execute(
            "UPDATE llm_batch_items SET meta_json=? WHERE batch_id=?",
            (json.dumps(meta), batch.batch_id),
        )
        connection.commit()
        raw = (
            "\n".join(connection.iterdump())
            + f"\nPRAGMA user_version = {world.repo.schema_version};\n"
        )
    world.repo.restore(raw)
    world.analysis.collect_batches()
    paid = world.repo.list_computation_results(10, "1929")
    assert len(paid) == 1 and paid[0].validation == "validated"
    assert not world.repo.list_revision_application_work()
    items = world.repo.list_unaccounted_batch_items()
    assert len(items) == 1 and isinstance(items[0].meta, DigestItemMeta)
    assert items[0].meta.revision_application_problem == "unsupported_owner"
    assert not world.repo.list_open_llm_batches()


def test_consumed_handoff_replaces_large_target_and_guards_its_known_work_reference(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    world, work = collected_work(monkeypatch)
    path = snapshot(world, tmp_path)
    with closing(sqlite3.connect(path)) as connection:
        row = connection.execute(
            "SELECT meta_json FROM llm_batch_items WHERE custom_id=?", (work.request_key,)
        ).fetchone()
        assert row is not None, "the consumed provider item retains a compact application handoff"
        meta = json.loads(row[0])
        assert meta["revision_application_work_key"] == work.key
        assert meta["amendment_revision"] is None
        connection.execute("DELETE FROM document_work WHERE key=?", (work.key,))
        connection.commit()
        raw = (
            "\n".join(connection.iterdump())
            + f"\nPRAGMA user_version = {world.repo.schema_version};\n"
        )
    before = world.repo.dump()
    with pytest.raises(ValueError, match="revision_application_handoff_missing"):
        world.repo.restore(raw)
    assert world.repo.dump() == before
    report = audit(path)
    assert "revision_application_handoff_missing" in {item["code"] for item in report["findings"]}


def test_new_handoff_metadata_fields_do_not_change_legacy_defaults() -> None:
    encoded = DigestItemMeta(memo_key="legacy-question").model_dump_json()
    assert "revision_application_work_key" not in encoded
    assert "revision_application_problem" not in encoded


def test_printed_work_owner_is_unchanged_by_literal_link_and_government_move_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, work = collected_work(monkeypatch)
    original = world.repo.publication_by_id(work.original_publication_id)
    world.repo.upsert_summary(summary("1930", "Related printed process"), now=world.clock.now())
    world.repo.link_bills(10, "1929", "1930")
    assert world.repo.move_government_rows(10, 11) == 0
    world.repo.restore(world.repo.dump())
    assert world.repo.publication_by_id(work.original_publication_id) == original
    assert world.repo.get_revision_application_work(work.key) == work
    world.analysis.collect_batches()
    revision = world.repo.list_amendment_revisions(work.original_publication_id)
    assert len(revision) == 1 and (revision[0].term, revision[0].number) == (10, "1929")


@pytest.mark.parametrize(
    ("phase", "failure"), [("collect", KeyboardInterrupt()), ("apply", SystemExit(19))]
)
def test_control_flow_interruptions_keep_their_identity_and_transaction_boundary(
    monkeypatch: pytest.MonkeyPatch, phase: str, failure: BaseException
) -> None:
    world, publication_id = queued_revision()

    def cancel(value: object) -> None:
        raise failure

    with monkeypatch.context() as patch:
        patch.setattr(
            world.repo,
            "save_revision_application_work" if phase == "collect" else "save_result_application",
            cancel,
        )
        with pytest.raises(type(failure)) as caught:
            world.analysis.collect_batches()
    assert caught.value is failure
    assert len(world.repo.list_computation_results(10, "1929")) == (phase == "apply")
    assert not world.repo.list_amendment_revisions(publication_id)
    assert len(world.repo.list_revision_application_work()) == (phase == "apply")


@pytest.mark.parametrize("binding", ["valid", "missing", "original"])
def test_completed_work_uses_exact_editorial_history_after_operational_payload_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, binding: str
) -> None:
    world, work = collected_work(monkeypatch)
    world.analysis.collect_batches()
    world.run()
    completed = world.repo.get_revision_application_work(work.key)
    assert completed is not None and completed.revision_publication_id is not None, (
        "application completes before the real delivery service confirms the revision"
    )
    revision = world.repo.publication_by_id(completed.revision_publication_id)
    assert revision is not None and revision.status is PublicationStatus.SENT, (
        "the fake publisher confirms this physical revision"
    )
    paid = world.repo.get_computation_result(work.result_id)
    calls = list(world.analysis.calls)
    path = snapshot(world, tmp_path)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "UPDATE publications SET delivery_json=NULL WHERE id IN (?,?)",
            (work.original_publication_id, completed.revision_publication_id),
        )
        if binding == "missing":
            connection.execute(
                "DELETE FROM publication_content WHERE publication_id=?",
                (completed.revision_publication_id,),
            )
        elif binding == "original":
            connection.execute(
                "UPDATE publication_content SET content_id=(SELECT content_id FROM publication_content"
                " WHERE publication_id=?) WHERE publication_id=?",
                (work.original_publication_id, completed.revision_publication_id),
            )
        connection.commit()
        raw = (
            "\n".join(connection.iterdump())
            + f"\nPRAGMA user_version = {world.repo.schema_version};\n"
        )
    before = world.repo.dump()
    if binding != "valid":
        with pytest.raises(ValueError, match="revision_application_publication_mismatch"):
            world.repo.restore(raw)
        assert world.repo.dump() == before
        assert "revision_application_publication_mismatch" in {
            item["code"] for item in audit(path)["findings"]
        }
        return
    world.repo.restore(raw)
    world.repo.restore(world.repo.dump())
    assert world.repo.get_revision_application_work(work.key) == completed
    assert world.repo.get_computation_result(work.result_id) == paid
    assert audit(path)["summary"]["errors"] == 0
    retained = world.repo.dump()
    world.analysis.collect_batches()
    assert world.repo.dump() == retained and world.analysis.calls == calls
