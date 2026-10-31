import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Never

import pytest
from tools.computation_doctor import audit

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.input_receipts import primary_input_receipt
from lexinform.models import (
    AnalysisRecord,
    BatchIntent,
    BatchItemMeta,
    Bill,
    BillStatus,
    ComputationInputBinding,
    ComputationResult,
    ContentSnapshot,
    DocumentSelectionRecord,
    LlmBatch,
    LlmBatchItem,
    ProcessSummary,
    ResultApplication,
    SourceObservation,
    Stage,
    StatusChange,
    TextDocument,
    computation_id,
)
from lexinform.services.content_sources import ContentSourceAudit, inspect_primary_content_sources
from tests.computations import retained_result
from tests.unit.test_input_receipts import NOW, SOURCE, meta, request, selection


@dataclass(frozen=True)
class Case:
    repo: SqliteBillRepository
    path: Path
    bill: Bill
    change: StatusChange
    bill_id: str
    change_id: str
    paid: ComputationResult
    item: LlmBatchItem
    selected: DocumentSelectionRecord
    observation: SourceObservation

    def inspect(self) -> ContentSourceAudit:
        return inspect_primary_content_sources(
            self.repo, bill_snapshot_id=self.bill_id, change_snapshot_id=self.change_id
        )

    def execute(self, sql: str, parameters: tuple[object, ...] = ()) -> None:
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute(sql, parameters)
            connection.commit()


def snapshots(repo: SqliteBillRepository, bill: Bill, change: StatusChange) -> tuple[str, str]:
    frozen_bill = ContentSnapshot(kind="bill", payload_json=bill.model_dump_json())
    frozen_change = ContentSnapshot(kind="change", payload_json=change.model_dump_json())
    repo.save_content_snapshot(frozen_bill)
    repo.save_content_snapshot(frozen_change)
    return frozen_bill.snapshot_id, frozen_change.snapshot_id


@pytest.fixture
def case(tmp_path: Path) -> Iterator[Case]:
    """The synthetic paid-input chain supplies its retained original queue witness."""
    path = tmp_path / "source.db"
    repo = SqliteBillRepository(path)
    repo.migrate()
    source = selection()
    selected = DocumentSelectionRecord.from_selection(source)
    frozen = request()
    binding = ComputationInputBinding(term=10, number="3039", reuse_key="frozen-key")
    receipt = primary_input_receipt(frozen, source, projection=None, known_selection=selected)
    original = retained_result("analysis")
    assert isinstance(original.record, AnalysisRecord), "the primary fixture carries an analysis"
    record = original.record.model_copy(
        update={
            "request_sha256": frozen.payload_sha256,
            "computation_id": computation_id("openai", original.batch_id, frozen.custom_id),
            "input_chars": len(SOURCE),
        }
    )
    paid = ComputationResult.model_validate_json(
        original.model_copy(
            update={
                "custom_id": frozen.custom_id,
                "record": record,
                "request_sha256": frozen.payload_sha256,
                "input_binding": binding,
                "generation": 2,
                "collected_at": NOW,
            }
        ).model_dump_json()
    )
    item = LlmBatchItem(
        batch_id=paid.batch_id,
        custom_id=paid.custom_id,
        call_kind="analysis",
        term=10,
        number="3039",
        consumed_at=NOW,
        meta=meta(receipt).model_copy(
            update={
                "request_sha256": frozen.payload_sha256,
                "input_binding": binding,
                "generation": 2,
                "queued_at": NOW,
            }
        ),
    )
    repo.save_llm_batch(
        LlmBatch(
            batch_id=paid.batch_id,
            provider="openai",
            call_kind="analysis",
            submitted_at=NOW,
            status="ended",
            request_count=1,
            estimated_cost_usd=0.01,
        ),
        [item],
    )
    repo.mark_llm_batch_item_consumed(paid.batch_id, paid.custom_id, consumed_at=NOW)
    repo.save_computation_result(paid)
    repo.save_result_application(
        ResultApplication(
            result_id=paid.result_id,
            term=10,
            number="3039",
            generation=7,
            phase="projection",
            outcome="applied",
            reason="batch_result",
            observed_generation=7,
            observed_status=BillStatus.ANALYZED,
            created_at=NOW,
        )
    )
    observation = SourceObservation(
        term=10,
        number="3039",
        purpose="analysis",
        selection_id=selected.selection_id,
        document=TextDocument(url=source.parts[0].url, kind="print"),
        recorded_at=NOW,
    )
    repo.save_source_observation(selected, observation)
    bill = Bill(
        summary=ProcessSummary(
            term=10,
            number="3039",
            title="Frozen project",
            document_type="projekt ustawy",
            change_date=NOW,
        ),
        status=BillStatus.ANALYZED,
        analysis=record,
        analysis_generation=7,
        first_seen_at=NOW,
        last_checked_at=NOW,
    )
    change = StatusChange(
        term=10,
        number="3039",
        old_fingerprint=None,
        new_fingerprint="captured",
        new_stages=[
            Stage(
                stage_name="Committee",
                stage_type="CommitteeReport",
                report_file="3039-A.pdf",
                proposal="poprawki",
            )
        ],
        detected_at=NOW,
    )
    bill_id, change_id = snapshots(repo, bill, change)
    yield Case(repo, path, bill, change, bill_id, change_id, paid, item, selected, observation)
    repo.close()


def test_exact_frozen_chain_is_bounded_quiet_and_independent_of_current_row(
    case: Case, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> Never:
        raise AssertionError("the audit must not read a current bill or an owner history")

    for name in ("get", "list_source_observations", "list_result_applications"):
        monkeypatch.setattr(case.repo, name, forbidden)
    before = case.repo.dump()
    first = case.inspect()
    assert first == case.inspect()
    assert first.state == "known" and first.source_observation_ids == (
        case.observation.observation_id,
    )
    assert first.scope == "frozen_primary_context/analysis" and len(first.encoded().encode()) <= 256
    assert SOURCE not in first.encoded() and "example.test" not in first.encoded()
    assert case.repo.dump() == before


def test_input_cleanup_and_public_restore_preserve_the_metadata_chain(case: Case) -> None:
    original = case.inspect()
    assert original.state == "known"
    case.repo.restore(case.repo.dump())
    assert case.inspect() == original


@pytest.mark.parametrize("invalid_usage", [False, True])
def test_frozen_projection_accepts_only_existing_paid_or_zero_accounting_policy(
    case: Case, invalid_usage: bool
) -> None:
    assert case.bill.analysis is not None, "the frozen fixture has its primary paid record"
    values = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    if invalid_usage:
        values["input_tokens"] = 7
    record = case.bill.analysis.model_copy(update=values)
    bill_id, change_id = snapshots(
        case.repo, case.bill.model_copy(update={"analysis": record}), case.change
    )
    result = inspect_primary_content_sources(
        case.repo, bill_snapshot_id=bill_id, change_snapshot_id=change_id
    )
    assert result.state == ("invalid" if invalid_usage else "known")


def test_existing_unstamped_primary_intent_stays_unknown_and_preserves_frozen_payload(
    case: Case,
) -> None:
    source = selection()
    frozen = request()
    before = frozen.model_dump_json()
    receipt = primary_input_receipt(frozen, source, projection=None, known_selection=case.selected)
    receipt_before = receipt.model_dump_json()
    legacy_meta = meta(receipt).model_copy(
        update={"generation": 2, "input_binding": case.paid.input_binding}
    )
    legacy_json = legacy_meta.model_dump_json()
    assert "queued_at" not in legacy_json
    assert BatchItemMeta.model_validate_json(legacy_json).model_dump_json() == legacy_json
    assert legacy_meta.model_copy(update={"queued_at": None}).model_dump_json() == legacy_json
    intent = BatchIntent(request=frozen, meta=legacy_meta, provider="openai", created_at=NOW)
    assert isinstance(intent.meta, BatchItemMeta), "the primary intent preserves its metadata type"
    assert intent.meta.queued_at is None
    item = LlmBatchItem.from_intent(case.paid.batch_id, intent)
    assert isinstance(item.meta, BatchItemMeta), "submission preserves primary metadata"
    assert item.meta.queued_at is None and "queued_at" not in item.meta.model_dump_json()
    assert intent.request.model_dump_json() == before and frozen.model_dump_json() == before
    assert item.meta.prepared_input_receipt is not None, "submission preserves its input receipt"
    assert item.meta.prepared_input_receipt.model_dump_json() == receipt_before
    case.execute("UPDATE llm_batch_items SET meta_json=?", (item.meta.model_dump_json(),))
    result = case.inspect()
    assert result.state == "unknown" and result.reason == "queue_time_unknown"


def test_supplied_pre_source_queue_time_is_preserved_and_remains_unknown(case: Case) -> None:
    created = NOW - timedelta(days=1)
    intent = BatchIntent.from_request(
        request=request(),
        meta=case.item.meta.model_copy(update={"input_binding": None, "queued_at": None}),
        provider="openai",
        created_at=created,
    )
    assert intent.meta.queued_at == intent.created_at == created
    case.execute("UPDATE llm_batch_items SET meta_json=?", (intent.meta.model_dump_json(),))
    result = case.inspect()
    assert result.state == "unknown" and result.reason == "observation_after_queue"


@pytest.mark.parametrize("queued", [None, NOW.replace(tzinfo=None).isoformat()])
def test_missing_or_naive_queue_time_cannot_use_collection_or_submission_fallback(
    case: Case, queued: str | None
) -> None:
    case.execute(
        "UPDATE llm_batch_items SET meta_json=json_set(meta_json,'$.queued_at',?)", (queued,)
    )
    result = case.inspect()
    assert result.state == "unknown" and result.reason == "queue_time_unknown"


def test_observation_after_queue_before_collection_cannot_backfill(case: Case) -> None:
    queued = case.paid.collected_at - timedelta(days=2)
    case.execute(
        "UPDATE llm_batch_items SET meta_json=json_set(meta_json,'$.queued_at',?)",
        (queued.isoformat(),),
    )
    case.execute("DELETE FROM source_observations")
    case.repo.save_source_observation(
        case.selected,
        case.observation.model_copy(update={"recorded_at": queued + timedelta(days=1)}),
    )
    before = case.repo.dump()
    result = case.inspect()
    assert result.state == "unknown" and result.reason == "observation_after_queue"
    assert case.repo.dump() == before


def test_queue_after_collection_is_a_known_chronology_contradiction(case: Case) -> None:
    case.execute(
        "UPDATE llm_batch_items SET meta_json=json_set(meta_json,'$.queued_at',?)",
        ((case.paid.collected_at + timedelta(days=1)).isoformat(),),
    )
    result = case.inspect()
    assert result.state == "invalid" and result.reason == "queue_after_collection"


@pytest.mark.parametrize(
    "context", ["missing", "kind", "owner", "stage", "legacy", "generation", "observational_change"]
)
def test_only_the_exact_initial_frozen_context_has_a_known_chain(case: Case, context: str) -> None:
    bill, change = case.bill, case.change
    if context == "missing":
        result = inspect_primary_content_sources(
            case.repo,
            bill_snapshot_id="content-snapshot-v1:" + "f" * 64,
            change_snapshot_id=case.change_id,
        )
        assert result.state == "unknown" and result.reason == "context_missing"
        return
    if context == "kind":
        result = inspect_primary_content_sources(
            case.repo, bill_snapshot_id=case.bill_id, change_snapshot_id=case.bill_id
        )
        assert result.state == "invalid" and result.reason == "context_identity"
        return
    if context == "owner":
        change = change.model_copy(update={"number": "wrong"})
    elif context == "stage":
        change = change.model_copy(update={"new_stages": []})
    elif context == "generation":
        bill = bill.model_copy(update={"analysis_generation": 2})
    else:
        assert bill.analysis is not None, "the initial frozen context includes its paid analysis"
        analysis = bill.analysis.model_copy(
            update={"computation_id": None}
            if context == "legacy"
            else {"source_url": "https://example.test/reobserved"}
        )
        bill = bill.model_copy(update={"analysis": analysis})
    bill_id, change_id = snapshots(case.repo, bill, change)
    result = inspect_primary_content_sources(
        case.repo, bill_snapshot_id=bill_id, change_snapshot_id=change_id
    )
    assert result.state == ("invalid" if context == "owner" else "unknown")
    assert not result.source_observation_ids


@pytest.mark.parametrize(
    "missing", ["result", "batch", "item", "consumed", "projection", "selection", "observation"]
)
def test_missing_retained_edges_are_unknown(case: Case, missing: str) -> None:
    commands = {
        "result": "DELETE FROM computation_results",
        "batch": "DELETE FROM llm_batches",
        "item": "DELETE FROM llm_batch_items",
        "consumed": "UPDATE llm_batch_items SET consumed_at=NULL",
        "projection": "DELETE FROM result_applications",
        "selection": "DELETE FROM document_selections",
        "observation": "DELETE FROM source_observations",
    }
    case.execute(commands[missing])
    result = case.inspect()
    assert result.state == "unknown" and not result.source_observation_ids


@pytest.mark.parametrize("field", ["prepared_input_receipt", "input_binding", "request_sha256"])
def test_legacy_item_metadata_does_not_invent_a_chain(case: Case, field: str) -> None:
    data = case.item.meta.model_dump(mode="json")
    data.pop(field)
    case.execute("UPDATE llm_batch_items SET meta_json=?", (json.dumps(data),))
    assert case.inspect().state == "unknown"


@pytest.mark.parametrize(
    "fault",
    [
        "provider",
        "paid_index",
        "paid_identity",
        "paid_record",
        "projection_json",
        "item_owner",
        "binding",
        "request",
        "chars",
        "selection",
        "observation",
    ],
)
def test_known_metadata_contradictions_are_invalid(case: Case, fault: str) -> None:
    if fault == "provider":
        case.execute("UPDATE llm_batches SET provider='anthropic'")
    elif fault == "paid_index":
        case.execute("UPDATE computation_results SET number='wrong'")
    elif fault == "paid_identity":
        case.execute(
            "UPDATE computation_results SET result_json=json_set(result_json,'$.batch_id','wrong',"
            "'$.record.computation_id',?)",
            (computation_id("openai", "wrong", case.paid.custom_id),),
        )
    elif fault == "paid_record":
        case.execute(
            "UPDATE computation_results SET result_json=json_set(result_json,"
            "'$.record.prompt_version','wrong')"
        )
    elif fault == "projection_json":
        case.execute(
            "UPDATE result_applications SET application_json=json_set(application_json,'$.generation',8)"
        )
    elif fault == "item_owner":
        case.execute("UPDATE llm_batch_items SET number='wrong'")
    elif fault == "binding":
        case.execute(
            "UPDATE llm_batch_items SET meta_json=json_set(meta_json,'$.input_binding.number','wrong')"
        )
    elif fault == "request":
        case.execute(
            "UPDATE llm_batch_items SET meta_json=json_set(meta_json,'$.request_sha256',?,'$.prepared_input_receipt.request_sha256',?)",
            ("b" * 64, "b" * 64),
        )
    elif fault == "chars":
        case.execute("UPDATE llm_batch_items SET meta_json=json_set(meta_json,'$.input_chars',999)")
    elif fault == "selection":
        case.execute(
            "UPDATE document_selections SET record_json=json_set(record_json,'$.selection.parts[0].role','unknown')"
        )
    else:
        case.execute(
            "UPDATE source_observations SET observation_json=json_set(observation_json,'$.purpose','review')"
        )
    before = case.repo.dump()
    assert case.inspect().state == "invalid"
    assert case.repo.dump() == before


def test_duplicate_selection_observations_remain_ambiguous_without_picking_newest(
    case: Case,
) -> None:
    alias = case.observation.model_copy(
        update={"document": TextDocument(url="https://example.test/alias", kind="print")}
    )
    case.repo.save_source_observation(case.selected, alias)
    case.repo.save_source_observation(
        case.selected,
        alias.model_copy(
            update={"document": TextDocument(url="https://example.test/third", kind="print")}
        ),
    )
    assert (
        len(case.repo.source_observations_for_selection(10, "3039", case.selected.selection_id))
        == 2
    )
    result = case.inspect()
    assert result.state == "unknown" and result.reason == "observation_ambiguous"
    assert not result.source_observation_ids


def test_foreign_observations_and_other_purposes_cannot_substitute_for_the_exact_scope(
    case: Case,
) -> None:
    case.execute("DELETE FROM source_observations")
    for observation in (
        case.observation.model_copy(update={"number": "3040"}),
        case.observation.model_copy(update={"purpose": "review"}),
    ):
        case.repo.save_source_observation(case.selected, observation)
    assert case.inspect().reason == "observation_missing"


def test_a_later_observation_cannot_backfill_the_original_paid_input(case: Case) -> None:
    case.execute("DELETE FROM source_observations")
    later = case.observation.model_copy(update={"recorded_at": NOW.replace(year=NOW.year + 1)})
    case.repo.save_source_observation(case.selected, later)
    before = case.repo.dump()
    result = case.inspect()
    assert result.state == "unknown" and result.reason == "observation_after_queue"
    assert case.repo.dump() == before


def test_legacy_collection_time_without_timezone_does_not_invent_chronology(case: Case) -> None:
    case.execute(
        "UPDATE computation_results SET result_json=json_set(result_json,'$.collected_at',?)",
        (NOW.replace(tzinfo=None).isoformat(),),
    )
    result = case.inspect()
    assert result.state == "unknown" and result.reason == "collection_time_unknown"


def test_transferred_analysis_remains_unknown_even_with_an_explicit_new_owner_receipt(
    case: Case,
) -> None:
    bill = case.bill.model_copy(
        update={"summary": case.bill.summary.model_copy(update={"number": "3040"})}
    )
    change = case.change.model_copy(update={"number": "3040"})
    bill_id, change_id = snapshots(case.repo, bill, change)
    case.repo.save_result_application(
        ResultApplication(
            result_id=case.paid.result_id,
            term=10,
            number="3040",
            generation=7,
            phase="projection",
            outcome="applied",
            reason="retained_result",
            observed_generation=7,
            observed_status=BillStatus.ANALYZED,
            created_at=NOW,
        )
    )
    result = inspect_primary_content_sources(
        case.repo, bill_snapshot_id=bill_id, change_snapshot_id=change_id
    )
    assert result.state == "unknown" and result.reason == "foreign_owner"
    assert case.repo.get_computation_result(case.paid.result_id) == case.paid


def test_selection_with_members_cannot_claim_the_loose_source_receipt_profile(case: Case) -> None:
    parts = (case.selected.selection.parts[0].model_copy(update={"members": ("inside.pdf",)}),)
    selected = DocumentSelectionRecord(
        selection=case.selected.selection.model_copy(update={"parts": parts})
    )
    observation = case.observation.model_copy(update={"selection_id": selected.selection_id})
    case.repo.save_source_observation(selected, observation)
    case.execute(
        "UPDATE llm_batch_items SET meta_json=json_set(meta_json,'$.prepared_input_receipt.source_selection_id',?)",
        (selected.selection_id,),
    )
    result = case.inspect()
    assert result.state == "invalid" and result.reason == "selection_input"


@pytest.mark.parametrize(
    "error", [sqlite3.OperationalError("read failed"), KeyboardInterrupt(), SystemExit(3)]
)
def test_storage_and_control_flow_errors_are_not_suppressed(
    case: Case, monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    def fail(snapshot_id: str) -> Never:
        raise error

    monkeypatch.setattr(case.repo, "get_content_snapshot", fail)
    with pytest.raises(type(error)) as caught:
        case.inspect()
    assert caught.value is error


def projected_receipt(case: Case, *, projected_generation: int = 19) -> ResultApplication:
    original = case.repo.list_result_applications(10, "3039")[0]
    return original.model_copy(
        update={"generation": 3, "projected_generation": projected_generation}
    )


@pytest.mark.parametrize("wrong_target", [False, True])
def test_explicit_future_projection_witness_matches_only_its_actual_target(
    case: Case, wrong_target: bool
) -> None:
    application = projected_receipt(case, projected_generation=20 if wrong_target else 19)
    case.repo.save_result_application(application)
    bill_id, change_id = snapshots(
        case.repo, case.bill.model_copy(update={"analysis_generation": 19}), case.change
    )
    before = case.repo.dump()
    result = inspect_primary_content_sources(
        case.repo, bill_snapshot_id=bill_id, change_snapshot_id=change_id
    )
    assert result.state == ("unknown" if wrong_target else "known")
    assert case.repo.dump() == before
    case.repo.restore(before)
    assert (
        inspect_primary_content_sources(
            case.repo, bill_snapshot_id=bill_id, change_snapshot_id=change_id
        )
        == result
    )
    assert case.repo.get_computation_result(case.paid.result_id) == case.paid


def test_old_application_cannot_be_enriched_with_a_later_projection_witness(case: Case) -> None:
    old = case.repo.list_result_applications(10, "3039")[0]
    before = case.repo.dump()
    with pytest.raises(ValueError, match="immutable"):
        case.repo.save_result_application(old.model_copy(update={"projected_generation": 19}))
    assert case.repo.dump() == before
    assert case.repo.list_result_applications(10, "3039")[0] == old


def test_projection_witness_is_rejected_for_a_retained_secondary_result(case: Case) -> None:
    secondary = retained_result("amendments")
    case.repo.save_computation_result(secondary)
    application = projected_receipt(case).model_copy(update={"result_id": secondary.result_id})
    before = case.repo.dump()
    with pytest.raises(ValueError, match="validated primary"):
        case.repo.save_result_application(application)
    assert case.repo.dump() == before


def test_known_projection_witness_corruption_is_shared_by_restore_and_doctor(case: Case) -> None:
    application = projected_receipt(case)
    case.repo.save_result_application(application)
    before = case.repo.dump()
    case.execute(
        "UPDATE result_applications SET application_json=json_set(application_json,'$.generation',4) WHERE generation=3"
    )
    audit_path = case.path.with_name("projection-doctor.sqlite")
    with (
        closing(sqlite3.connect(case.path)) as source,
        closing(sqlite3.connect(audit_path)) as copy,
    ):
        source.backup(copy)
    report = audit(audit_path)
    assert any(item["code"] == "application_columns_mismatch" for item in report["findings"])
    with closing(SqliteBillRepository(":memory:")) as target:
        target.migrate()
        target.restore(before)
        retained = target.dump()
        with pytest.raises(ValueError, match="immutable identity"):
            target.restore(case.repo.dump())
        assert target.dump() == retained
