import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Never
from zoneinfo import ZoneInfo

import pytest
from tools.computation_doctor import audit

from lexinform.adapters import input_receipts as input_adapter
from lexinform.adapters.input_receipts import input_snapshot_checks
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    AmendmentsContext,
    AmendmentsQuestion,
    AnalysisQuestion,
    ApplicantType,
    BatchIntent,
    BatchItemMeta,
    BatchQuestion,
    BatchRequest,
    BillContext,
    BillStatus,
    ComputationInputBinding,
    ComputationResult,
    DigestItemMeta,
    DocumentReviewRecord,
    JointBillDescription,
    JointContext,
    JointQuestion,
    LlmBatch,
    LlmBatchItem,
    LocalTimingWitness,
    NameContext,
    NameQuestion,
    ProcessDetail,
    ResultApplicationTarget,
    ReviewContext,
    ReviewInputReference,
    ReviewResultRecord,
    StatusChange,
    SupplementContext,
    SupplementQuestion,
    SupplementRecord,
)
from lexinform.models.batch import BatchKind, DocumentReviewQuestion, batch_item_meta
from lexinform.models.reviews import ReviewPurpose
from lexinform.saved_clock import saved_clock_age
from lexinform.services.result_applications import record_secondary_application
from tests.computations import KINDS, retained_result

NOW = datetime(2026, 11, 5, 12, tzinfo=UTC)


def question(kind: BatchKind) -> BatchQuestion:
    if kind in ("analysis", "reanalysis", "explanation"):
        return AnalysisQuestion(
            kind=kind,
            ctx=BillContext(
                number="3039",
                title="Frozen context",
                description=None,
                document_date=None,
                applicant_type=ApplicantType.DEPUTIES,
                text="Original supplied text",
                truncated=False,
                text_source="pdf",
            ),
        )
    if kind == "amendments":
        return AmendmentsQuestion(
            ctx=AmendmentsContext(
                number="3039",
                title="Frozen context",
                source_kind="committee_amendments",
                text="Original supplied text",
                truncated=False,
                previous_summary="Prior description",
            )
        )
    if kind == "supplement":
        return SupplementQuestion(
            ctx=SupplementContext(
                number="3039",
                title="Frozen context",
                document_title="OSR",
                source_kind="impact_assessment",
                text="Original supplied text",
                truncated=False,
                previous_summary="Prior description",
            )
        )
    if kind == "joint":
        subject = JointBillDescription(
            number="3039",
            title="Frozen context",
            applicant_type=ApplicantType.DEPUTIES,
            summary="Prior description",
        )
        return JointQuestion(ctx=JointContext(subject=subject, others=[]))
    if kind == "name":
        return NameQuestion(
            ctx=NameContext(number="3039", title="Frozen context", summary="Prior description")
        )
    return DocumentReviewQuestion(
        kind=kind,
        ctx=ReviewContext(number="3039", title="Frozen context", purpose=kind, documents=()),
    )


def intent_for(kind: BatchKind) -> BatchIntent:
    request = BatchRequest(custom_id=kind, term=10, number="3039", question=question(kind))
    frozen = request.freeze(
        payload_json=json.dumps(request.question.model_dump(mode="json"), sort_keys=True),
        model="frozen-model",
        prompt_version="frozen-prompt",
        estimated_cost_usd=0,
    )
    meta: BatchItemMeta | DigestItemMeta = (
        BatchItemMeta(
            input_chars=len(request.ctx.model_dump().get("text", "")),
            truncated=False,
            text_source="pdf",
            source_kind="print",
            source_url=None,
            revision=1,
            text_sha256=None,
            memo_key=f"timing-{kind}",
        )
        if kind in ("analysis", "reanalysis")
        else DigestItemMeta(memo_key=f"timing-{kind}")
    )
    return BatchIntent.from_request(request=frozen, meta=meta, provider="openai", created_at=NOW)


def witness_for(intent: BatchIntent) -> dict[str, Any]:
    binding = intent.meta.input_binding
    assert binding is not None, "the public new-intent constructor freezes the original input scope"
    return {
        "schema_version": 1,
        "purpose": intent.request.call_kind,
        "request_sha256": intent.request.payload_sha256,
        "input_scope": binding.model_dump(mode="json"),
        "trigger_kind": "work_requested",
        "triggered_at": (NOW - timedelta(minutes=10)).isoformat(),
        "read_kind": None if intent.request.call_kind in ("name", "joint") else "fresh",
        "read_completed_at": None
        if intent.request.call_kind in ("name", "joint")
        else (NOW - timedelta(minutes=20)).isoformat(),
    }


def timed_intent(kind: BatchKind) -> BatchIntent:
    original = intent_for(kind)
    raw = original.model_dump(mode="json")
    raw["meta"]["local_timing_witness"] = witness_for(original)
    raw["meta"]["request_sha256"] = original.request.payload_sha256
    return BatchIntent.model_validate(raw)


def timed_result(kind: BatchKind) -> ComputationResult:
    intent = intent_for(kind)
    result = retained_result(kind)
    raw = result.model_dump(mode="json")
    raw.update(
        input_binding=intent.meta.input_binding.model_dump(mode="json")
        if intent.meta.input_binding
        else None,
        request_sha256=intent.request.payload_sha256,
        collected_at=(NOW + timedelta(hours=1)).isoformat(),
        local_timing_witness=witness_for(intent),
    )
    raw["record"]["request_sha256"] = intent.request.payload_sha256
    return ComputationResult.model_validate(raw)


@pytest.mark.parametrize("kind", KINDS)
def test_all_batch_purposes_keep_timing_outside_question_request_and_input_identity(
    kind: BatchKind,
) -> None:
    original = intent_for(kind)
    timed = timed_intent(kind)
    assert timed.request.model_dump_json() == original.request.model_dump_json()
    assert timed.meta.input_binding == original.meta.input_binding
    assert timed.meta.memo_key == original.meta.memo_key
    assert (
        timed.meta.model_dump()["local_timing_witness"]["request_sha256"]
        == original.request.payload_sha256
    )
    assert BatchIntent.model_validate_json(timed.model_dump_json()) == timed
    item = LlmBatchItem.from_intent("timing-batch", timed)
    assert (
        batch_item_meta(kind, item.meta.model_dump_json()).model_dump()["local_timing_witness"]
        == item.meta.model_dump()["local_timing_witness"]
    )


@pytest.mark.parametrize("kind", KINDS)
def test_all_retained_result_purposes_roundtrip_without_a_batch_parent(
    kind: BatchKind, repo: SqliteBillRepository
) -> None:
    original = retained_result(kind)
    timed = timed_result(kind)
    assert timed.result_id == original.result_id
    repo.save_computation_result(timed)
    repo.restore(repo.dump())
    loaded = repo.get_computation_result(timed.result_id)
    assert loaded == timed
    assert loaded is not None, (
        "the immutable result remains available without operational batch rows"
    )
    assert loaded.model_dump()["local_timing_witness"]["purpose"] == kind


@pytest.mark.parametrize("kind", KINDS)
def test_absent_or_explicit_none_timing_keeps_legacy_json_and_identities(kind: BatchKind) -> None:
    original = intent_for(kind)
    assert "local_timing_witness" not in original.meta.model_dump()
    raw = original.model_dump(mode="json")
    raw["meta"]["local_timing_witness"] = None
    assert BatchIntent.model_validate(raw).model_dump_json() == original.model_dump_json()
    result = retained_result(kind)
    assert "local_timing_witness" not in result.model_dump()
    assert (
        ComputationResult.model_validate(
            {**result.model_dump(mode="json"), "local_timing_witness": None}
        ).model_dump_json()
        == result.model_dump_json()
    )


@pytest.mark.parametrize(
    "fault",
    [
        "purpose",
        "request_sha",
        "scope_number",
        "scope_key",
        "missing_scope",
        "missing_meta_sha",
        "trigger_kind",
        "trigger_naive",
        "trigger_numeric",
        "trigger_precision",
        "trigger_pair",
        "read_pair",
        "future_trigger",
        "future_read",
        "naive_queue",
        "no_frozen_payload",
        "official_source_clock",
        "oversize",
    ],
)
def test_timing_intent_rejects_uncorroborated_scope_request_events_and_clocks(fault: str) -> None:
    raw = timed_intent("amendments").model_dump(mode="json")
    witness = raw["meta"]["local_timing_witness"]
    if fault == "purpose":
        witness["purpose"] = "name"
    elif fault == "request_sha":
        witness["request_sha256"] = "b" * 64
    elif fault == "scope_number":
        witness["input_scope"]["number"] = "other"
    elif fault == "scope_key":
        witness["input_scope"]["reuse_key"] = "other"
    elif fault == "missing_scope":
        raw["meta"]["input_binding"] = None
    elif fault == "missing_meta_sha":
        raw["meta"]["request_sha256"] = None
    elif fault == "trigger_kind":
        witness["trigger_kind"] = "service_entered"
    elif fault == "trigger_naive":
        witness["triggered_at"] = "2026-11-05T11:50:00"
    elif fault == "trigger_numeric":
        witness["triggered_at"] = 1000
    elif fault == "trigger_precision":
        witness["triggered_at"] = "2026-11-05T11:50:00.1234567+00:00"
    elif fault == "trigger_pair":
        witness["triggered_at"] = None
    elif fault == "read_pair":
        witness["read_kind"] = None
    elif fault == "future_trigger":
        witness["triggered_at"] = (NOW + timedelta(seconds=1)).isoformat()
    elif fault == "future_read":
        witness["read_completed_at"] = (NOW + timedelta(seconds=1)).isoformat()
    elif fault == "naive_queue":
        raw["created_at"] = NOW.replace(tzinfo=None).isoformat()
    elif fault == "no_frozen_payload":
        raw["request"].update(payload_json=None, payload_sha256=None)
    elif fault == "official_source_clock":
        witness["source_published_at"] = NOW.isoformat()
    elif fault == "oversize":
        key = "x" * 1500
        raw["meta"]["memo_key"] = key
        raw["meta"]["input_binding"]["reuse_key"] = key
        witness["input_scope"]["reuse_key"] = key
    with pytest.raises(ValueError):
        BatchIntent.model_validate(raw)


@pytest.mark.parametrize("kind", ["fresh", "cache_reuse", "mixed"])
def test_read_completion_kinds_are_explicit_without_imposing_trigger_read_order(kind: str) -> None:
    raw = timed_intent("amendments").model_dump(mode="json")
    raw["meta"]["local_timing_witness"]["read_kind"] = kind
    loaded = BatchIntent.model_validate(raw)
    witness = loaded.meta.local_timing_witness
    assert witness is not None, "the fixture supplies a bound timing witness"
    assert witness.read_kind == kind
    assert witness.triggered_at is not None and witness.read_completed_at is not None, (
        "the fixture supplies both independent local clocks"
    )
    assert witness.read_completed_at < witness.triggered_at


@pytest.mark.parametrize("kind", ["joint", "name"])
def test_source_free_context_without_bound_read_remains_unknown(kind: BatchKind) -> None:
    raw = timed_intent(kind).model_dump(mode="json")
    raw["meta"]["local_timing_witness"].update(read_kind=None, read_completed_at=None)
    loaded = BatchIntent.model_validate(raw)
    witness = loaded.meta.local_timing_witness
    assert witness is not None, "the work trigger is still explicitly bound"
    assert witness.read_kind is None and witness.read_completed_at is None
    assert "read_kind" not in witness.model_dump()


@pytest.mark.parametrize("fault", ["missing_hash", "wrong_purpose", "missing_binding"])
def test_submitted_metadata_cannot_claim_an_unbound_timing_witness(fault: str) -> None:
    meta = timed_intent("amendments").meta.model_dump(mode="json")
    kind = "amendments"
    if fault == "missing_hash":
        meta["request_sha256"] = None
    elif fault == "wrong_purpose":
        kind = "name"
    elif fault == "missing_binding":
        meta["input_binding"] = None
    with pytest.raises(ValueError):
        batch_item_meta(kind, json.dumps(meta))


def test_typed_metadata_copy_cannot_bypass_actual_request_binding() -> None:
    original = timed_intent("amendments")
    metadata = original.meta.model_copy(update={"request_sha256": None})
    with pytest.raises(ValueError):
        BatchIntent(
            request=original.request,
            meta=metadata,
            provider=original.provider,
            created_at=original.created_at,
        )


def test_fold_future_clock_is_rejected_through_the_intent_carrier() -> None:
    zone = ZoneInfo("Europe/Warsaw")
    endpoint = datetime(2026, 10, 25, 2, 30, tzinfo=zone, fold=0)
    raw = timed_intent("amendments").model_dump()
    raw["created_at"] = endpoint
    raw["meta"]["local_timing_witness"].update(
        triggered_at=endpoint.replace(fold=1), read_kind=None, read_completed_at=None
    )
    with pytest.raises(ValueError, match="recorded endpoint"):
        BatchIntent.model_validate(raw)


@pytest.mark.parametrize(
    "endpoint",
    [
        datetime.min.replace(tzinfo=timezone(timedelta(hours=14))),
        datetime.max.replace(tzinfo=timezone(-timedelta(hours=14))),
    ],
)
def test_extreme_offset_endpoints_need_no_overflowing_utc_conversion(endpoint: datetime) -> None:
    raw = timed_intent("amendments").model_dump()
    raw["created_at"] = endpoint
    raw["meta"]["local_timing_witness"].update(
        triggered_at=endpoint, read_kind=None, read_completed_at=None
    )
    loaded = BatchIntent.model_validate(raw)
    assert loaded.meta.local_timing_witness is not None


@pytest.mark.parametrize(
    "fault", ["missing_hash", "missing_scope", "scope", "purpose", "future", "naive_collect"]
)
def test_retained_result_rejects_unbound_or_late_timing(fault: str) -> None:
    raw = timed_result("amendments").model_dump(mode="json")
    if fault == "missing_hash":
        raw["request_sha256"] = None
        raw["record"]["request_sha256"] = None
    elif fault == "missing_scope":
        raw["input_binding"] = None
    elif fault == "scope":
        raw["local_timing_witness"]["input_scope"]["term"] = 11
    elif fault == "purpose":
        raw["local_timing_witness"]["purpose"] = "name"
    elif fault == "future":
        raw["local_timing_witness"]["triggered_at"] = (NOW + timedelta(days=1)).isoformat()
    elif fault == "naive_collect":
        raw["collected_at"] = NOW.replace(tzinfo=None).isoformat()
    with pytest.raises(ValueError):
        ComputationResult.model_validate(raw)


@pytest.mark.parametrize("had_timing", [False, True])
def test_existing_result_keeps_its_original_witness_without_enrichment_or_replacement(
    repo: SqliteBillRepository, had_timing: bool
) -> None:
    timed = timed_result("amendments")
    original = timed if had_timing else timed.model_copy(update={"local_timing_witness": None})
    repo.save_computation_result(original)
    raw = timed.model_dump(mode="json")
    raw["local_timing_witness"]["triggered_at"] = (NOW - timedelta(minutes=5)).isoformat()
    replacement = ComputationResult.model_validate(raw)
    before = repo.dump()
    with pytest.raises(ValueError, match="immutable"):
        repo.save_computation_result(replacement)
    assert repo.dump() == before
    assert repo.get_computation_result(original.result_id) == original


def test_original_scope_survives_relocated_item_and_foreign_application(
    repo: SqliteBillRepository, process_3039: ProcessDetail
) -> None:
    intent = timed_intent("supplement")
    item = LlmBatchItem.from_intent("timing-batch", intent)
    moved = LlmBatchItem.model_validate(
        item.model_copy(update={"term": 11, "number": "new-owner"}).model_dump(mode="json")
    )
    assert moved.meta.local_timing_witness == intent.meta.local_timing_witness
    result = timed_result("supplement")
    repo.save_computation_result(result)
    repo.upsert_summary(
        process_3039.model_copy(update={"term": 11, "number": "new-owner"}), now=NOW
    )
    assert isinstance(result.record, SupplementRecord), "the fixture retains a supplement record"
    change_id = repo.add_status_change(
        StatusChange(
            term=11,
            number="new-owner",
            old_fingerprint=None,
            new_fingerprint="foreign-timing",
            new_stages=[],
            supplements=[result.record],
            detected_at=NOW + timedelta(hours=2),
        )
    )
    assert change_id is not None, "the foreign target is a newly persisted event"
    record_secondary_application(
        repo,
        11,
        "new-owner",
        result.result_id,
        target=ResultApplicationTarget(kind="status_change", ref=str(change_id)),
        now=NOW + timedelta(hours=2),
    )
    repo.restore(repo.dump())
    loaded = repo.get_computation_result(result.result_id)
    assert loaded == result and loaded is not None, (
        "the foreign application keeps the original immutable result"
    )
    assert loaded.input_binding is not None and loaded.input_binding.term == 10
    assert (
        loaded.local_timing_witness is not None
        and loaded.local_timing_witness.input_scope == loaded.input_binding
    )


def test_timing_only_intent_corruption_uses_shared_public_restore_and_doctor_readers(
    tmp_path: Path,
) -> None:
    path = tmp_path / "timing.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        repo.save_batch_intent(timed_intent("name"))
        repo.restore(repo.dump())
        assert len(repo.list_queued_batch_intents()) == 1
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute(
                "UPDATE llm_batch_intents SET meta_json=json_set(meta_json,'$.local_timing_witness.input_scope.number','wrong')"
            )
        with pytest.raises(ValueError):
            repo.list_queued_batch_intents()
        corrupt = repo.dump()
    report = audit(path)
    assert any(f["code"] == "invalid_input_intent_snapshot" for f in report["findings"])
    with closing(SqliteBillRepository(":memory:")) as target:
        target.migrate()
        before = target.dump()
        with pytest.raises(ValueError, match="invalid_input_intent_snapshot"):
            target.restore(corrupt)
        assert target.dump() == before


def test_explanation_timing_binds_the_outer_scope_instead_of_nested_primary_metadata() -> None:
    raw = timed_intent("explanation").model_dump(mode="json")
    raw["meta"]["explanation"] = intent_for("analysis").meta.model_dump(mode="json")
    loaded = BatchIntent.model_validate(raw)
    assert isinstance(loaded.meta, DigestItemMeta), "the explanation uses secondary outer metadata"
    nested = loaded.meta.explanation
    assert nested is not None, "the fixture includes its original primary context metadata"
    witness = loaded.meta.local_timing_witness
    assert witness is not None and witness.purpose == "explanation", (
        "the outer request carries its own timing"
    )
    assert witness.input_scope == loaded.meta.input_binding
    assert witness.input_scope != nested.input_binding


@pytest.mark.parametrize("purpose", ["consultation_review", "commission_review", "act_application"])
def test_compact_review_result_keeps_timing_beside_its_original_review_input(
    purpose: ReviewPurpose, repo: SqliteBillRepository
) -> None:
    original = timed_result(purpose)
    assert isinstance(original.record, DocumentReviewRecord), (
        "the fixture supplies the full legacy review record"
    )
    reference = ReviewInputReference.from_context(original.record.context, term=10)
    binding = ComputationInputBinding(term=10, number="3039", reuse_key=reference.key)
    witness = original.local_timing_witness
    assert witness is not None, "the source fixture supplies explicit local clocks"
    witness = LocalTimingWitness.model_validate(
        witness.model_copy(update={"input_scope": binding}).model_dump(mode="json")
    )
    raw = original.model_dump(mode="json")
    raw.update(
        schema_version=3,
        record=ReviewResultRecord.from_record(original.record).model_dump(mode="json"),
        review_input=reference.model_dump(mode="json"),
        input_binding=binding.model_dump(mode="json"),
        local_timing_witness=witness.model_dump(mode="json"),
    )
    compact = ComputationResult.model_validate(raw)
    repo.save_computation_result(compact)
    repo.restore(repo.dump())
    assert repo.get_computation_result(compact.result_id) == compact
    assert compact.result_id == original.result_id


@pytest.mark.parametrize("kind", KINDS)
def test_timing_only_submitted_items_restore_without_text_receipts(
    kind: BatchKind, repo: SqliteBillRepository
) -> None:
    intent = timed_intent(kind)
    item = LlmBatchItem.from_intent("timing-batch", intent)
    repo.save_llm_batch(
        LlmBatch(
            batch_id="timing-batch",
            provider="openai",
            call_kind=kind,
            submitted_at=NOW,
            status="submitted",
            request_count=1,
            estimated_cost_usd=0,
        ),
        [item],
    )
    repo.restore(repo.dump())
    assert repo.list_llm_batch_items("timing-batch") == [item]
    assert item.meta.prepared_input_receipt is None


@pytest.mark.parametrize("fault", ["scope", "hash", "after_submission"])
def test_timing_only_submitted_corruption_is_checked_by_restore_and_doctor(
    tmp_path: Path, fault: str
) -> None:
    path = tmp_path / "items.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        intent = timed_intent("name")
        item = LlmBatchItem.from_intent("timing-batch", intent)
        repo.save_llm_batch(
            LlmBatch(
                batch_id="timing-batch",
                provider="openai",
                call_kind="name",
                submitted_at=NOW,
                status="submitted",
                request_count=1,
                estimated_cost_usd=0,
            ),
            [item],
        )
        field, value = (
            ("input_scope.number", "wrong")
            if fault == "scope"
            else ("request_sha256", "b" * 64)
            if fault == "hash"
            else ("triggered_at", (NOW + timedelta(minutes=1)).isoformat())
        )
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute(
                "UPDATE llm_batch_items SET meta_json=json_set(meta_json,?,?)",
                ("$.local_timing_witness." + field, value),
            )
        if fault != "after_submission":
            with pytest.raises(ValueError):
                repo.list_llm_batch_items("timing-batch")
        corrupt = repo.dump()
    report = audit(path)
    assert any(f["code"] == "invalid_input_item_snapshot" for f in report["findings"])
    with closing(SqliteBillRepository(":memory:")) as target:
        target.migrate()
        before = target.dump()
        with pytest.raises(ValueError, match="invalid_input_item_snapshot"):
            target.restore(corrupt)
        assert target.dump() == before


def test_operational_cleanup_preserves_original_result_and_timing_metadata(
    tmp_path: Path, process_3039: ProcessDetail
) -> None:
    path = tmp_path / "retention.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        repo.upsert_summary(process_3039, now=NOW)
        repo.set_status(10, "3039", BillStatus.ANALYZED)
        intent = timed_intent("analysis")
        metadata = intent.meta.model_copy(update={"text": "Operational input text " * 100})
        intent = BatchIntent.model_validate(
            intent.model_copy(update={"meta": metadata}).model_dump(mode="json")
        )
        item = LlmBatchItem.from_intent("provider-batch", intent)
        repo.save_llm_batch(
            LlmBatch(
                batch_id=item.batch_id,
                provider="openai",
                call_kind="analysis",
                submitted_at=NOW,
                status="submitted",
                request_count=1,
                estimated_cost_usd=0,
            ),
            [item],
        )
        result = timed_result("analysis")
        assert result.custom_id == item.custom_id
        repo.save_computation_result(result)
        repo.mark_llm_batch_item_consumed(
            item.batch_id, item.custom_id, consumed_at=NOW + timedelta(hours=1)
        )
        repo.mark_batch_item_accounted(item.batch_id, item.custom_id, at=NOW + timedelta(hours=1))
        repo.mark_llm_batch_collected(item.batch_id, completed_at=NOW + timedelta(hours=1))
        repo.mark_llm_batch_forgotten(item.batch_id, forgotten_at=NOW + timedelta(hours=2))
        stats = repo.prune_history(
            before=NOW + timedelta(days=365), memo_before=NOW + timedelta(days=365)
        )
        assert stats["batch_payloads"] == 1
        repo.restore(repo.dump())
        assert repo.get_computation_result(result.result_id) == result
    with closing(sqlite3.connect(path)) as connection:
        row = connection.execute("SELECT meta_json FROM llm_batch_items").fetchone()
    assert row is not None, "operational cleanup retains the accounted metadata row"
    restored = batch_item_meta("analysis", row[0])
    assert restored.local_timing_witness == item.meta.local_timing_witness
    assert isinstance(restored, BatchItemMeta), "the shared decoder retains primary metadata"
    assert restored.text == ""


@pytest.mark.parametrize(
    "stamp",
    [
        datetime(2026, 10, 25, 2, 30, tzinfo=ZoneInfo("Europe/Warsaw"), fold=0),
        datetime(2026, 10, 25, 2, 30, tzinfo=ZoneInfo("Europe/Warsaw"), fold=1),
        datetime(2026, 1, 1, tzinfo=timezone(timedelta(seconds=30))),
        datetime(2026, 1, 1, tzinfo=timezone(timedelta(seconds=30, microseconds=1))),
    ],
)
def test_witness_clock_roundtrip_preserves_the_exact_offset_and_instant(stamp: datetime) -> None:
    witness = timed_intent("amendments").meta.local_timing_witness
    assert witness is not None, "the fixture provides a bound timing record"
    raw = witness.model_dump()
    raw.update(triggered_at=stamp, read_kind=None, read_completed_at=None)
    bound = LocalTimingWitness.model_validate(raw)
    loaded = LocalTimingWitness.model_validate_json(bound.model_dump_json())
    assert loaded == bound
    assert loaded.triggered_at is not None, "the supplied trigger clock remains present"
    assert loaded.triggered_at.utcoffset() == stamp.utcoffset()
    age = saved_clock_age(stamp, loaded.triggered_at)
    assert age.unknown is None and age.seconds == 0


@pytest.mark.parametrize("carrier", ["intent", "metadata", "result"])
def test_unrepresentable_enclosing_json_clock_rejects_only_a_nonnull_witness(carrier: str) -> None:
    endpoint = NOW.replace(tzinfo=timezone(timedelta(seconds=30)))
    if carrier == "result":
        raw = timed_result("amendments").model_dump()
        raw["collected_at"] = endpoint + timedelta(hours=1)
        with pytest.raises(ValueError, match="unsupported_timing_endpoint_offset"):
            ComputationResult.model_validate(raw)
        raw["local_timing_witness"] = None
        assert ComputationResult.model_validate(raw).local_timing_witness is None
    else:
        raw = timed_intent("amendments").model_dump()
        if carrier == "metadata":
            raw["meta"]["queued_at"] = endpoint
        else:
            raw["created_at"] = endpoint
        with pytest.raises(ValueError, match="unsupported_timing_endpoint_offset"):
            BatchIntent.model_validate(raw)
        raw["meta"]["local_timing_witness"] = None
        assert BatchIntent.model_validate(raw).meta.local_timing_witness is None


@pytest.mark.parametrize("fault", ["duplicate", "duplicate_none", "deep"])
def test_malformed_timing_only_item_json_is_a_bounded_finding_instead_of_an_omission(
    tmp_path: Path, fault: str
) -> None:
    path = tmp_path / "malformed-item.db"
    intent = timed_intent("name")
    item = LlmBatchItem.from_intent("timing-batch", intent)
    valid = item.meta.model_dump_json()
    raw = (
        valid.replace(
            '"local_timing_witness":', '"local_timing_witness":null,"local_timing_witness":', 1
        )
        if fault == "duplicate"
        else valid[:-1] + ',"local_timing_witness":null}'
        if fault == "duplicate_none"
        else '{"local_timing_witness":' + "[" * 2500 + "0" + "]" * 2500 + "}"
    )
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        repo.save_llm_batch(
            LlmBatch(
                batch_id="timing-batch",
                provider="openai",
                call_kind="name",
                submitted_at=NOW,
                status="submitted",
                request_count=1,
                estimated_cost_usd=0,
            ),
            [item],
        )
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("UPDATE llm_batch_items SET meta_json=?", (raw,))
        with pytest.raises(ValueError):
            repo.list_llm_batch_items("timing-batch")
        corrupt = repo.dump()
    report = audit(path)
    assert any(f["code"] == "invalid_input_item_snapshot" for f in report["findings"])
    with closing(SqliteBillRepository(":memory:")) as target:
        target.migrate()
        before = target.dump()
        with pytest.raises(ValueError, match="invalid_input_item_snapshot"):
            target.restore(corrupt)
        assert target.dump() == before


@pytest.mark.parametrize("fault", ["scope", "duplicate_none", "deep"])
def test_retained_timing_json_uses_shared_public_doctor_and_atomic_restore_guards(
    tmp_path: Path, fault: str
) -> None:
    path = tmp_path / "malformed-result.db"
    result = timed_result("amendments")
    value = result.model_dump(mode="json")
    if fault == "scope":
        value["local_timing_witness"]["input_scope"]["number"] = "wrong"
    if fault == "deep":
        value.pop("local_timing_witness")
        raw = (
            json.dumps(value)[:-1]
            + ',"local_timing_witness":'
            + "[" * 2500
            + "0"
            + "]" * 2500
            + "}"
        )
    else:
        raw = json.dumps(value)
        if fault == "duplicate_none":
            raw = raw[:-1] + ',"local_timing_witness":null}'
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        repo.save_computation_result(result)
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("UPDATE computation_results SET result_json=?", (raw,))
        with pytest.raises(ValueError):
            repo.get_computation_result(result.result_id)
        with pytest.raises(ValueError):
            repo.list_computation_results(10, "3039")
        corrupt = repo.dump()
    report = audit(path)
    assert any(f["code"] == "invalid_input_result_snapshot" for f in report["findings"])
    with closing(SqliteBillRepository(":memory:")) as target:
        target.migrate()
        before = target.dump()
        with pytest.raises(ValueError, match="invalid_input_result_snapshot"):
            target.restore(corrupt)
        assert target.dump() == before


def test_timing_request_duplicate_json_is_not_accepted_as_a_known_frozen_request(
    tmp_path: Path,
) -> None:
    path = tmp_path / "request.db"
    intent = timed_intent("name")
    request = intent.request.model_dump_json()
    request = request.replace('"payload_sha256":', '"payload_sha256":null,"payload_sha256":', 1)
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        repo.save_batch_intent(intent)
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("UPDATE llm_batch_intents SET request_json=?", (request,))
        with pytest.raises(ValueError):
            repo.list_queued_batch_intents()
        corrupt = repo.dump()
    report = audit(path)
    assert any(f["code"] == "invalid_input_intent_snapshot" for f in report["findings"])
    with closing(SqliteBillRepository(":memory:")) as target:
        target.migrate()
        before = target.dump()
        with pytest.raises(ValueError, match="invalid_input_intent_snapshot"):
            target.restore(corrupt)
        assert target.dump() == before


def test_input_timing_validation_does_not_swallow_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "cancel.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        repo.save_batch_intent(timed_intent("name"))
    failure = KeyboardInterrupt("cancel")

    def stop(raw: str) -> Never:
        raise failure

    monkeypatch.setattr(input_adapter, "read_json", stop)
    with closing(sqlite3.connect(path)) as connection, pytest.raises(KeyboardInterrupt) as raised:
        list(input_snapshot_checks(connection))
    assert raised.value is failure
