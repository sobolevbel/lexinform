import datetime as dt
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Literal

import pytest
from tools.computation_doctor import audit

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.json_storage import canonical_json
from lexinform.models import (
    ComputationInputBinding,
    ComputationResult,
    DocumentReviewRecord,
    ReviewContext,
    ReviewInputReference,
)
from lexinform.models.review_memos import ReviewMemoProjection, ReviewMemoReference
from lexinform.models.reviews import ReviewPurpose
from lexinform.primary_projections import PrimaryProjectionError
from lexinform.review_memos import read_review_memo, resolve_review_memo, review_record_sha256
from lexinform.services.computation_reuse import review_memo
from tests.scenario.test_commission_reviews import context as commission_context
from tests.scenario.test_document_reviews import context
from tests.unit.test_government_review_reuse import prepared, replace_and_clean
from tests.unit.test_review_computation_reuse import paid_review as original_paid_review


def marker(result: ComputationResult) -> str:
    assert result.record is not None, "the schema3 technical result is validated"
    assert result.request_sha256 is not None, "the technical result has a frozen request SHA"
    return json.dumps(
        {
            "modern_review_ref": result.result_id,
            "version": 1,
            "request_sha256": result.request_sha256,
            "retained_record_sha256": hashlib.sha256(
                canonical_json(result.record.model_dump(mode="json", exclude_unset=True)).encode()
            ).hexdigest(),
        }
    )


def test_known_missing_review_memo_parent_is_not_a_legacy_cache_miss() -> None:
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        result = paid_review(context())
        repo.save_analysis_memo(
            context().memo_key(10), marker(result), term=10, number=result.number
        )
        with pytest.raises(ValueError, match="invalid_review_memo"):
            repo.load_analysis_memo()


def test_restore_rejects_known_missing_review_memo_parent_atomically() -> None:
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        before = repo.dump()
        result = paid_review(context())
        repo.save_analysis_memo(
            context().memo_key(10), marker(result), term=10, number=result.number
        )
        invalid = repo.dump()
        repo.restore(before)
        with pytest.raises(ValueError, match="invalid_review_memo"):
            repo.restore(invalid)
        assert repo.dump() == before


@pytest.mark.parametrize("purpose", ["consultation_review", "commission_review", "act_application"])
@pytest.mark.parametrize("accounting", ["paid", "reuse"])
def test_technical_review_memo_carriers_resolve_only_actual_context_and_preserve_projection(
    purpose: ReviewPurpose, accounting: Literal["paid", "reuse"]
) -> None:
    source = source_for(purpose)
    result = paid_review(source)
    projection = ReviewMemoProjection(
        accounting=accounting,
        baseline_day=dt.date(2026, 9, 1),
        source_key="review-input:" + "a" * 64,
        checked_at=dt.datetime(2026, 9, 2, tzinfo=dt.UTC),
        source_fingerprint="b" * 64,
        canonical_dates=(dt.date(2026, 10, 1),),
    )
    assert result.record is not None, "the technical schema3 result supplies a validated review"
    reference = ReviewMemoReference.model_validate(
        {**json.loads(marker(result)), "projection": projection}
    )
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        repo.save_computation_result(result)
        encoded = result.model_dump_json()
        raw = reference.model_dump_json()
        repo.save_analysis_memo(source.memo_key(10), raw, term=10, number=source.number)
        assert repo.load_analysis_memo()[source.memo_key(10)] == raw
        record = review_memo(repo, 10, source, raw)
        assert record.context is source
        assert record.baseline_day == projection.baseline_day
        assert record.source_key == projection.source_key
        assert record.checked_at == projection.checked_at
        assert record.source_fingerprint == projection.source_fingerprint
        assert record.canonical_dates == projection.canonical_dates
        assert record.input_tokens == (0 if accounting == "reuse" else result.record.input_tokens)
        assert record.computation_id == result.result_id
        assert repo.get_computation_result(result.result_id) == result
        assert result.model_dump_json() == encoded
        dumped = repo.dump()
        repo.restore(dumped)
        repo.restore(dumped)
        assert repo.dump() == dumped


@pytest.mark.parametrize(
    "mutation",
    [
        "request",
        "record_hash",
        "version",
        "bool_version",
        "extra",
        "context",
        "oversized",
        "duplicate",
        "deep",
        "unknown_owner",
        "sql_identity",
    ],
)
def test_known_broken_marker_is_shared_load_doctor_atomic_restore_error(
    tmp_path: Path, mutation: str
) -> None:
    path = tmp_path / "state.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        result = paid_review(context())
        repo.save_computation_result(result)
        value = json.loads(marker(result))
        if mutation == "request":
            value["request_sha256"] = "0" * 64
        elif mutation == "record_hash":
            value["retained_record_sha256"] = "0" * 64
        elif mutation == "version":
            value["version"] = 2
        elif mutation == "bool_version":
            value["version"] = True
        elif mutation in ("extra", "context"):
            value[mutation] = {}
        elif mutation == "oversized":
            value["projection"] = {"source_key": "x" * 4097}
        raw = json.dumps(value)
        if mutation == "duplicate":
            raw = raw[:-1] + ',"version":1}'
        elif mutation == "deep":
            raw = raw[:-1] + ',"projection":' + "[" * 2000 + "0" + "]" * 2000 + "}"
        repo.save_analysis_memo(
            context().memo_key(10),
            raw,
            term=None if mutation == "unknown_owner" else 10,
            number=result.number,
        )
        if mutation == "sql_identity":
            with sqlite3.connect(path) as connection:
                connection.execute(
                    "UPDATE computation_results SET number='other' WHERE result_id=?",
                    (result.result_id,),
                )
        invalid = repo.dump()
        with pytest.raises(ValueError, match="invalid_review_memo"):
            repo.load_analysis_memo()
        doctor_path = tmp_path / "checkpointed.db"
        with sqlite3.connect(path) as connection, sqlite3.connect(doctor_path) as copied:
            connection.backup(copied)
        report = audit(doctor_path)
        assert any(item["code"] == "invalid_review_memo" for item in report["findings"])
        with closing(SqliteBillRepository(":memory:")) as target:
            target.migrate()
            before = target.dump()
            with pytest.raises(ValueError, match="invalid_review_memo"):
                target.restore(invalid)
            assert target.dump() == before


def test_inline_and_none_legacy_memo_bytes_and_default_writer_stay_exact() -> None:
    source = context()
    result = paid_review(source)
    inline = resolve_review_memo(
        ReviewMemoReference.model_validate_json(marker(result)), result, source
    ).model_dump_json()
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        repo.save_computation_result(result)
        repo.save_analysis_memo(source.memo_key(10), inline, term=10, number=source.number)
        assert repo.load_analysis_memo()[source.memo_key(10)] == inline
        assert "modern_review_ref" not in repo.dump()
        repo.save_analysis_memo("legacy-unknown", "null")
        assert repo.load_analysis_memo()["legacy-unknown"] == "null"
        dumped = repo.dump()
        repo.restore(dumped)
        assert repo.dump() == dumped


@pytest.mark.parametrize("change", ["text", "order", "scope", "purpose"])
def test_pointer_caller_mismatch_is_error_not_another_historical_result(change: str) -> None:
    source = source_for("commission_review")
    result = paid_review(source)
    reference = ReviewMemoReference.model_validate_json(marker(result))
    if change == "text":
        first = source.documents[0].model_copy(update={"text": "forged text"})
        source = source.model_copy(update={"documents": (first, *source.documents[1:])})
    elif change == "order":
        source = source.model_copy(update={"documents": tuple(reversed(source.documents))})
    elif change == "scope":
        source = source.model_copy(update={"comparison_sha256": "c" * 64})
    else:
        source = source.model_copy(update={"purpose": "consultation_review"})
    with pytest.raises(ValueError, match="invalid_review_memo"):
        resolve_review_memo(reference, result, source)


@pytest.mark.parametrize("purpose", ["consultation_review", "commission_review", "act_application"])
def test_public_request_uses_manual_reader_fixture_without_preparing_or_mutating_bill(
    purpose: ReviewPurpose,
) -> None:
    with closing(SqliteBillRepository(":memory:")) as repo:
        case = prepared(repo, "RCL/123", "consultation_review")
        source = source_for(purpose).model_copy(update={"number": case.number})
        result = paid_review(source)
        repo.save_computation_result(result)
        raw = marker(result)
        repo.save_analysis_memo(source.memo_key(10), raw, term=10, number=case.number)
        service, _ = case.services()
        current = repo.get(10, case.number)
        assert current is not None, "the technical reader fixture has an actual current Bill"
        before = current.model_dump_json()
        assert not service.review_enabled("act_application")
        reused = service.request_document_review(current, source)
        assert isinstance(reused, DocumentReviewRecord)
        assert reused.computation_id == result.result_id
        assert reused.input_tokens == reused.output_tokens == 0
        assert case.backend.preparations == 0 and service.calls == []
        assert not service.review_exhausted(10, source)
        retained = repo.get(10, case.number)
        assert retained is not None and retained.model_dump_json() == before
        assert repo.load_analysis_memo()[source.memo_key(10)] == raw


def test_exact_government_pointer_uses_j243_original_term_authority_after_cleanup() -> None:
    with closing(SqliteBillRepository(":memory:")) as repo:
        case = prepared(repo, "WPL/UD123", "consultation_review")
        original, encoded = replace_and_clean(case)
        assert original.computation_id is not None, "the real public review retained a paid result"
        result = repo.get_computation_result(original.computation_id)
        assert result is not None, "the original result survives both public memo cleanups"
        raw = marker(result)
        repo.save_analysis_memo(original.context.memo_key(11), raw, term=11, number=case.number)
        service, _ = case.services()
        current = repo.get(11, case.number)
        assert current is not None, "rollover retained the same government number"
        before = case.backend.preparations
        reused = service.request_document_review(current, original.context)
        assert (
            isinstance(reused, DocumentReviewRecord) and reused.computation_id == result.result_id
        )
        assert case.backend.preparations == before
        assert result.model_dump_json() == encoded


def test_pointer_never_substitutes_an_authorized_different_result() -> None:
    with closing(SqliteBillRepository(":memory:")) as repo:
        case = prepared(repo, "RCL/123", "consultation_review")
        original, _ = replace_and_clean(case)
        assert original.computation_id is not None, "the original actual result is retained"
        original_result = repo.get_computation_result(original.computation_id)
        assert original_result is not None and original_result.record is not None, (
            "the actual original result is validated"
        )
        changed = original_result.model_copy(update={"batch_id": "technical-unapplied-result"})
        other = ComputationResult.model_validate(
            {
                **changed.model_dump(),
                "record": original_result.record.model_copy(
                    update={"computation_id": changed.result_id}
                ),
            }
        )
        repo.save_computation_result(other)
        raw = marker(other)
        repo.save_analysis_memo(original.context.memo_key(11), raw, term=11, number=case.number)
        service, _ = case.services()
        current = repo.get(11, case.number)
        assert current is not None, (
            "the current owner retains authority only for the original result"
        )
        before = case.backend.preparations
        with pytest.raises(ValueError, match="invalid_review_memo"):
            service.request_document_review(current, original.context)
        assert case.backend.preparations == before and service.calls == []


def test_optional_parent_failure_and_cancellation_do_not_fall_through_to_prepare(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        result = paid_review(context())
        raw = marker(result)

        def cancelled(self: SqliteBillRepository, result_id: str) -> ComputationResult | None:
            raise KeyboardInterrupt

        monkeypatch.setattr(SqliteBillRepository, "get_computation_result", cancelled)
        with pytest.raises(KeyboardInterrupt):
            review_memo(repo, 10, context(), raw)


def test_projection_profile_refuses_unsupported_fields_and_clocks() -> None:
    with pytest.raises(ValueError):
        ReviewMemoProjection.model_validate({"future_context": {}})
    with pytest.raises(ValueError):
        ReviewMemoProjection(
            checked_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone(dt.timedelta(seconds=1)))
        )


def source_for(purpose: ReviewPurpose) -> ReviewContext:
    if purpose == "commission_review":
        return commission_context().model_copy(update={"number": "3039"})
    return context(role="final_act") if purpose == "act_application" else context()


def paid_review(source: ReviewContext) -> ComputationResult:
    original = original_paid_review(source.model_copy(update={"number": "3039"}))
    return ComputationResult.model_validate(
        {
            **original.model_dump(),
            "number": source.number,
            "review_input": ReviewInputReference.from_context(source, term=10),
            "input_binding": ComputationInputBinding(
                term=10, number=source.number, reuse_key=source.memo_key(10)
            ),
        }
    )


@pytest.mark.parametrize("unknown", ["binding", "schema", "request"])
def test_known_pointer_never_accepts_unverifiable_original_parent(unknown: str) -> None:
    source = source_for("consultation_review")
    result = paid_review(source)
    reference = ReviewMemoReference.model_validate_json(marker(result))
    updates: dict[str, dict[str, object]] = {
        "binding": {"input_binding": None},
        "schema": {"schema_version": 2},
        "request": {"request_sha256": None},
    }
    changed = result.model_copy(update=updates[unknown])
    with pytest.raises(ValueError, match="invalid_review_memo"):
        resolve_review_memo(reference, changed, source)


def test_known_marker_context_error_precedes_current_bill_review_reuse() -> None:
    with closing(SqliteBillRepository(":memory:")) as repo:
        case = prepared(repo, "RCL/123", "consultation_review")
        source = case.catalog.source
        result = paid_review(source)
        repo.save_computation_result(result)
        current_record = resolve_review_memo(
            ReviewMemoReference.model_validate_json(marker(result)), result, source
        )
        repo.save_document_review(10, case.number, current_record)
        wrong = source.model_copy(update={"comparison_sha256": "d" * 64})
        wrong_result = paid_review(wrong).model_copy(update={"batch_id": "different-context"})
        assert wrong_result.record is not None, (
            "the technical context control retains its compact review"
        )
        wrong_result = ComputationResult.model_validate(
            {
                **wrong_result.model_dump(),
                "record": wrong_result.record.model_copy(
                    update={"computation_id": wrong_result.result_id}
                ),
            }
        )
        repo.save_computation_result(wrong_result)
        repo.save_analysis_memo(
            source.memo_key(10), marker(wrong_result), term=10, number=case.number
        )
        service, _ = case.services()
        bill = repo.get(10, case.number)
        assert bill is not None and bill.consultation_review is not None, (
            "a matching current full review is present"
        )
        with pytest.raises(ValueError, match="invalid_review_memo"):
            service.request_document_review(bill, source)
        with pytest.raises(ValueError, match="invalid_review_memo"):
            service.review_exhausted(10, source)
        assert case.backend.preparations == 0


def test_version1_hash_preserves_saved_fields_without_adding_unset_defaults() -> None:
    original = paid_review(context())
    saved = original.model_dump(mode="json")
    record = saved["record"]
    assert isinstance(record, dict), "the technical result has a JSON record object"
    del record["cache_creation_input_tokens"]
    restored = ComputationResult.model_validate(saved)
    assert restored.record is not None, "the omitted optional usage field retains a valid result"
    assert "cache_creation_input_tokens" not in restored.record.model_fields_set
    assert (
        review_record_sha256(restored)
        == hashlib.sha256(canonical_json(record).encode()).hexdigest()
    )


@pytest.mark.parametrize("damage", ["duplicate", "malformed"])
def test_nonmarker_escaped_unicode_preserves_original_restore_error_owner(damage: str) -> None:
    raw = '{"text":"\\u0105","text":"second"}' if damage == "duplicate" else '{"text":"\\u0105",'
    assert read_review_memo(raw) is None
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        before = repo.dump()
        repo.save_analysis_memo("legacy-escaped", raw)
        script = repo.dump()
        with closing(SqliteBillRepository(":memory:")) as target:
            target.migrate()
            if damage == "duplicate":
                with pytest.raises(
                    PrimaryProjectionError,
                    match="invalid_modern_primary_projection: analysis_memo/legacy-escaped/record_json",
                ):
                    target.restore(script)
            else:
                with pytest.raises(json.JSONDecodeError):
                    target.restore(script)
            assert target.dump() == before


@pytest.mark.parametrize("boundary", ["load", "doctor", "restore"])
def test_known_blob_marker_has_bounded_error_in_each_reader(tmp_path: Path, boundary: str) -> None:
    path = tmp_path / "state.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        result = paid_review(context())
        repo.save_computation_result(result)
        with sqlite3.connect(path) as connection:
            connection.execute(
                "INSERT INTO analysis_memo(key,record_json,term,number) VALUES (?,?,?,?)",
                (context().memo_key(10), marker(result).encode(), 10, result.number),
            )
        script = repo.dump()
        if boundary == "load":
            with pytest.raises(ValueError, match="invalid_review_memo"):
                repo.load_analysis_memo()
        elif boundary == "doctor":
            snapshot = tmp_path / "doctor.db"
            with sqlite3.connect(path) as connection, sqlite3.connect(snapshot) as copied:
                connection.backup(copied)
            report = audit(snapshot)
            assert any(item["code"] == "invalid_review_memo" for item in report["findings"])
        else:
            with closing(SqliteBillRepository(":memory:")) as target:
                target.migrate()
                before = target.dump()
                with pytest.raises(ValueError, match="invalid_review_memo"):
                    target.restore(script)
                assert target.dump() == before


@pytest.mark.parametrize("location", ["body", "nested"])
def test_profile_is_not_inferred_from_body_text_or_nested_keys(location: str) -> None:
    raw = (
        json.dumps({"text": '{"modern_review_ref":"opaque"}'})
        if location == "body"
        else json.dumps({"nested": {"modern_review_ref": "opaque"}})
    )
    assert read_review_memo(raw) is None
    assert read_review_memo(raw.encode()) is None
    assert read_review_memo(None) is None


def test_escaped_top_level_marker_key_is_still_strictly_validated() -> None:
    result = paid_review(context())
    raw = marker(result).replace('"modern_review_ref"', '"modern_\\u0072eview_ref"')
    reference = read_review_memo(raw)
    assert reference is not None and reference.modern_review_ref == result.result_id
    with pytest.raises(ValueError, match="invalid_review_memo"):
        read_review_memo(raw[:-1] + ',"version":1}')
