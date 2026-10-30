import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lexinform.adapters.primary_projections import PrimaryProjectionReader
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.json_storage import canonical_json, read_json
from lexinform.models import AnalysisRecord, BillStatus, LocatedText, ProcessDetail, ReadyAnalysis
from lexinform.primary_projections import (
    MAX_PRIMARY_PROJECTION_BYTES,
    PrimaryProjectionContext,
    PrimaryProjectionError,
    PrimaryProjectionSource,
    ProjectionOwner,
    compact_primary_projection,
    read_primary_json,
)
from lexinform.services.analysis_applications import apply_analysis
from tests.computations import retained_result

NOW = datetime(2026, 10, 30, tzinfo=UTC)
OWNER = ProjectionOwner(10, "3039")


def test_minimal_observation_roundtrips_the_exact_inline_shape_and_original_paid_record() -> None:
    result = retained_result("analysis")
    assert isinstance(result.record, AnalysisRecord), "the fixture has a primary paid record"
    source = PrimaryProjectionSource(result.model_dump_json())
    record = result.record.model_copy(update={"source_url": "https://example.test/new.pdf"})
    raw = record.model_dump_json()

    compact = compact_primary_projection(raw, source, OWNER)

    value = read_json(compact)
    assert isinstance(value, dict), "the compact reference is a JSON object"
    assert value["observation"] == {"source_url": "https://example.test/new.pdf"}
    restored = read_primary_json(compact, lambda _: source, PrimaryProjectionContext(OWNER))
    assert canonical_json(read_json(restored)) == canonical_json(read_json(raw))
    assert (
        AnalysisRecord.model_validate_json(restored).model_fields_set
        == AnalysisRecord.model_validate_json(raw).model_fields_set
    )
    assert source.result_json == result.model_dump_json()
    assert 0 < len(compact.encode()) < len(raw.encode())


@pytest.mark.parametrize("field", ["source_url", "model"])
def test_overlay_cannot_delete_paid_fields_even_when_model_values_are_equal(field: str) -> None:
    result = retained_result("analysis")
    assert isinstance(result.record, AnalysisRecord), "the fixture has a primary record"
    raw = read_json(result.record.model_dump_json())
    assert isinstance(raw, dict), "the record serializes as an object"
    raw.pop(field)
    encoded = canonical_json(raw)

    assert (
        compact_primary_projection(
            encoded, PrimaryProjectionSource(result.model_dump_json()), OWNER
        )
        == encoded
    )


def test_nested_field_presence_does_not_become_current_model_defaults() -> None:
    result = retained_result("analysis")
    assert isinstance(result.record, AnalysisRecord), "the fixture has a primary record"
    raw = read_json(result.record.model_dump_json())
    assert isinstance(raw, dict) and isinstance(raw["analysis"], dict), "the answer is structured"
    raw["analysis"].pop("mnemonic_fallback")
    encoded = canonical_json(raw)

    assert (
        compact_primary_projection(
            encoded, PrimaryProjectionSource(result.model_dump_json()), OWNER
        )
        == encoded
    )


def test_sparse_paid_observation_can_add_an_explicit_null_without_adding_other_fields() -> None:
    result = retained_result("analysis")
    source_value = read_json(result.model_dump_json())
    assert isinstance(source_value, dict) and isinstance(source_value["record"], dict), (
        "the retained result has a structured paid record"
    )
    source_value["record"].pop("source_url")
    source = PrimaryProjectionSource(canonical_json(source_value))
    current = dict(source_value["record"])
    current["source_url"] = None
    raw = canonical_json(current)

    compact = compact_primary_projection(raw, source, OWNER)

    assert compact != raw
    value = read_json(compact)
    assert isinstance(value, dict), "the compact reference is an object"
    assert value["observation"] == {"source_url": None}
    restored = read_primary_json(compact, lambda _: source, PrimaryProjectionContext(OWNER))
    assert read_json(restored) == current


@pytest.mark.parametrize("usage", [(0, 0, 0, 0), (0, 3, 0, 0), (None, None, None, None)])
def test_accounting_allows_only_original_or_complete_reuse_zero(
    usage: tuple[int | None, int | None, int | None, int | None],
) -> None:
    result = retained_result("analysis")
    assert isinstance(result.record, AnalysisRecord), "the fixture has a primary record"
    record = result.record.model_copy(
        update=dict(
            zip(
                (
                    "input_tokens",
                    "output_tokens",
                    "cache_read_input_tokens",
                    "cache_creation_input_tokens",
                ),
                usage,
                strict=True,
            )
        )
    )
    raw = record.model_dump_json()
    source = PrimaryProjectionSource(result.model_dump_json())

    compact = compact_primary_projection(raw, source, OWNER)

    assert (compact != raw) == (usage == (0, 0, 0, 0))
    if compact != raw:
        restored = read_primary_json(compact, lambda _: source, PrimaryProjectionContext(OWNER))
        assert AnalysisRecord.model_validate_json(restored) == record


def test_utf8_envelope_cap_and_strict_savings_are_checked_on_exact_encoded_input() -> None:
    result = retained_result("analysis")
    assert isinstance(result.record, AnalysisRecord), "the fixture has a primary record"
    current = result.record.model_copy(update={"source_url": "https://example.test/" + "я" * 20})
    raw = current.model_dump_json()
    source = PrimaryProjectionSource(result.model_dump_json())
    compact = compact_primary_projection(raw, source, OWNER)
    size = len(compact.encode())
    assert compact != raw and size > len(compact)

    assert compact_primary_projection(raw, source, OWNER, max_bytes=size) == compact
    assert compact_primary_projection(raw, source, OWNER, max_bytes=size - 1) == raw
    assert len(compact.encode()) <= MAX_PRIMARY_PROJECTION_BYTES
    assert compact_primary_projection(compact, source, OWNER) == compact


def test_large_observation_stays_inline_instead_of_truncating_a_valid_record() -> None:
    result = retained_result("analysis")
    assert isinstance(result.record, AnalysisRecord), "the fixture has a primary record"
    current = result.record.model_copy(update={"source_url": "https://example.test/" + "я" * 5000})
    raw = current.model_dump_json()

    assert (
        compact_primary_projection(raw, PrimaryProjectionSource(result.model_dump_json()), OWNER)
        == raw
    )


@pytest.mark.parametrize("raw", ["not-json", "{}", "[]", '{"modern_primary_ref":"opaque"}'])
def test_optional_non_primary_shapes_stay_inline(raw: str) -> None:
    source = PrimaryProjectionSource(retained_result("analysis").model_dump_json())
    assert compact_primary_projection(raw, source, OWNER) == raw


def test_same_paid_owner_is_required_even_when_a_foreign_receipt_could_admit_reading() -> None:
    result = retained_result("analysis")
    assert isinstance(result.record, AnalysisRecord), "the fixture has a primary record"
    raw = result.record.model_dump_json()
    assert (
        compact_primary_projection(
            raw, PrimaryProjectionSource(result.model_dump_json()), ProjectionOwner(11, "3039")
        )
        == raw
    )


def test_current_and_root_primary_memo_compact_while_ready_stays_inline(
    tmp_path: Path, process_3039: ProcessDetail
) -> None:
    path = tmp_path / "writer.db"
    repo = SqliteBillRepository(path)
    repo.migrate()
    repo.upsert_summary(process_3039, now=NOW)
    result = retained_result("analysis")
    assert isinstance(result.record, AnalysisRecord), "the fixture has a primary record"
    repo.save_computation_result(result)
    paid_before = repo.get_computation_result(result.result_id)
    apply_analysis(repo, 10, "3039", result.record, now=NOW)
    ready = ReadyAnalysis(record=result.record, located=LocatedText(), text="frozen input")
    repo.save_ready_analysis(10, "3039", ready)
    repo.save_analysis_memo(
        "primary", result.record.model_dump_json(), term=10, number="3039", used_at=NOW
    )

    with sqlite3.connect(path) as connection:
        current, frozen = connection.execute(
            "SELECT analysis_json, ready_analysis_json FROM bills"
        ).fetchone()
        memo = connection.execute("SELECT record_json FROM analysis_memo").fetchone()[0]
    assert "modern_primary_ref" in current and "modern_primary_ref" in memo
    assert "modern_primary_ref" not in frozen
    bill = repo.get(10, "3039")
    assert bill is not None and bill.analysis == result.record and bill.ready_analysis == ready
    assert AnalysisRecord.model_validate_json(repo.load_analysis_memo()["primary"]) == result.record
    before = repo.dump()
    repo.restore(before)
    assert repo.dump() == before
    assert repo.get_computation_result(result.result_id) == paid_before
    assert len(repo.list_result_applications(10, "3039")) == 1
    for status in (BillStatus.BATCH_PENDING, BillStatus.REANALYSIS_READY):
        repo.set_status(10, "3039", status)
        original = repo.get(10, "3039")
        repo.save_analysis(10, "3039", result.record)
        assert repo.get(10, "3039") == original
    repo.close()


def test_memo_owner_transfer_materializes_the_original_answer_and_keeps_conflict_semantics(
    tmp_path: Path,
) -> None:
    path = tmp_path / "memo.db"
    repo = SqliteBillRepository(path)
    repo.migrate()
    result = retained_result("analysis")
    assert isinstance(result.record, AnalysisRecord), "the fixture has a primary record"
    repo.save_computation_result(result)
    original = result.record.model_dump_json()
    repo.save_analysis_memo("primary", original, term=10, number="3039", used_at=NOW)
    replacement = result.record.model_copy(update={"model": "not-the-original-answer"})

    repo.save_analysis_memo(
        "primary", replacement.model_dump_json(), used_at=NOW + timedelta(days=1)
    )
    with sqlite3.connect(path) as connection:
        repeated = connection.execute("SELECT record_json FROM analysis_memo").fetchone()[0]
    assert "modern_primary_ref" in repeated
    repo.save_analysis_memo("primary", replacement.model_dump_json(), term=11)

    with sqlite3.connect(path) as connection:
        stored, term, number, used_at = connection.execute(
            "SELECT record_json,term,number,last_used_at FROM analysis_memo"
        ).fetchone()
    assert "modern_primary_ref" not in stored
    assert canonical_json(read_json(stored)) == canonical_json(read_json(original))
    assert (term, number) == (11, "3039")
    assert used_at == (NOW + timedelta(days=1)).isoformat()
    assert AnalysisRecord.model_validate_json(repo.load_analysis_memo()["primary"]) == result.record
    assert repo.get_computation_result(result.result_id) == result
    repo.close()


def test_later_same_owner_memo_save_compacts_original_content_after_paid_result_arrives(
    tmp_path: Path,
) -> None:
    path = tmp_path / "later.db"
    repo = SqliteBillRepository(path)
    repo.migrate()
    result = retained_result("analysis")
    assert isinstance(result.record, AnalysisRecord), "the fixture has a primary record"
    original = result.record.model_dump_json()
    repo.save_analysis_memo("primary", original, term=10, number="3039", used_at=NOW)
    with sqlite3.connect(path) as connection:
        before = connection.execute("SELECT record_json FROM analysis_memo").fetchone()[0]
    assert before == original
    repo.save_computation_result(result)
    replacement = result.record.model_copy(update={"model": "ignored replacement"})

    repo.save_analysis_memo(
        "primary", replacement.model_dump_json(), term=10, number="3039", used_at=NOW
    )

    with sqlite3.connect(path) as connection:
        after = connection.execute("SELECT record_json FROM analysis_memo").fetchone()[0]
    assert "modern_primary_ref" in after
    assert canonical_json(read_json(repo.load_analysis_memo()["primary"])) == canonical_json(
        read_json(original)
    )
    repo.close()


@pytest.mark.parametrize("kind", ["foreign", "secondary", "changed-answer", "unknown-owner"])
def test_ineligible_memo_never_acquires_a_primary_reference(tmp_path: Path, kind: str) -> None:
    path = tmp_path / "ineligible.db"
    repo = SqliteBillRepository(path)
    repo.migrate()
    result = retained_result("supplement" if kind == "secondary" else "analysis")
    assert result.record is not None, "the fixture retains a validated answer"
    repo.save_computation_result(result)
    record = result.record
    if kind == "changed-answer":
        assert isinstance(record, AnalysisRecord), "this case uses a primary answer"
        record = record.model_copy(
            update={"analysis": record.analysis.model_copy(update={"summary": "Changed answer"})}
        )
    raw = record.model_dump_json()
    term = None if kind == "unknown-owner" else 11 if kind == "foreign" else 10

    repo.save_analysis_memo("candidate", raw, term=term, number="3039")

    with sqlite3.connect(path) as connection:
        saved = connection.execute("SELECT record_json FROM analysis_memo").fetchone()[0]
    assert saved == raw
    repo.close()


def test_failed_primary_receipt_rolls_back_the_new_compact_current_projection(
    tmp_path: Path, process_3039: ProcessDetail, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "receipt.db"
    repo = SqliteBillRepository(path)
    repo.migrate()
    repo.upsert_summary(process_3039, now=NOW)
    result = retained_result("analysis")
    assert isinstance(result.record, AnalysisRecord), "the fixture has a primary record"
    repo.save_computation_result(result)
    before = repo.dump()

    def fail_receipt(*args: object, **kwargs: object) -> None:
        raise RuntimeError("receipt refused")

    monkeypatch.setattr(repo, "save_result_application", fail_receipt)
    with pytest.raises(RuntimeError, match="receipt refused"):
        apply_analysis(repo, 10, "3039", result.record, now=NOW)

    assert repo.dump() == before
    repo.close()


@pytest.mark.parametrize("term", [10, 11])
def test_invalid_existing_memo_reference_rolls_back_refresh_or_owner_transfer(
    tmp_path: Path, term: int
) -> None:
    path = tmp_path / "corrupt.db"
    repo = SqliteBillRepository(path)
    repo.migrate()
    result = retained_result("analysis")
    assert isinstance(result.record, AnalysisRecord), "the fixture has a primary record"
    repo.save_computation_result(result)
    repo.save_analysis_memo("primary", result.record.model_dump_json(), term=10, number="3039")
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE analysis_memo SET record_json=json_set(record_json,"
            " '$.retained_record_sha256', ?)",
            ("f" * 64,),
        )
    before = repo.dump()

    with pytest.raises(PrimaryProjectionError):
        repo.save_analysis_memo("primary", "ignored incoming value", term=term)

    assert repo.dump() == before
    repo.close()


def test_indexed_source_sql_errors_are_not_optional_inline_fallbacks() -> None:
    connection = sqlite3.connect(":memory:")
    reader = PrimaryProjectionReader(connection)
    result = retained_result("analysis")
    assert isinstance(result.record, AnalysisRecord), "the fixture has a primary record"
    with pytest.raises(sqlite3.OperationalError, match="computation_results"):
        reader.compact(result.record.model_dump_json(), OWNER)
    connection.close()


def test_indexed_paid_owner_corruption_is_not_hidden_by_current_writer(
    tmp_path: Path, process_3039: ProcessDetail
) -> None:
    path = tmp_path / "indexed.db"
    repo = SqliteBillRepository(path)
    repo.migrate()
    repo.upsert_summary(process_3039, now=NOW)
    result = retained_result("analysis")
    assert isinstance(result.record, AnalysisRecord), "the fixture has a primary record"
    repo.save_computation_result(result)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE computation_results SET number='foreign'")
    before = repo.dump()

    with pytest.raises(PrimaryProjectionError, match="columns"):
        repo.save_analysis(10, "3039", result.record)

    assert repo.dump() == before
    repo.close()


def test_direct_government_row_move_materializes_current_without_fabricating_permission(
    tmp_path: Path, process_3039: ProcessDetail
) -> None:
    path = tmp_path / "direct-move.db"
    repo = SqliteBillRepository(path)
    repo.migrate()
    number = "RCL/123"
    repo.upsert_summary(process_3039.model_copy(update={"number": number}), now=NOW)
    result = retained_result("analysis").model_copy(update={"number": number})
    assert isinstance(result.record, AnalysisRecord), "the government fixture has a paid analysis"
    repo.save_computation_result(result)
    repo.save_analysis(10, number, result.record)
    original = repo.get(10, number)
    assert original is not None and original.analysis is not None, "the current analysis is saved"
    with sqlite3.connect(path) as connection:
        before = connection.execute("SELECT analysis_json FROM bills").fetchone()[0]
    assert "modern_primary_ref" in before

    assert repo.move_government_rows(10, 11) == 1

    moved = repo.get(11, number)
    assert moved is not None and moved.analysis is not None, "the public primitive moved the row"
    assert moved.analysis.model_dump_json() == original.analysis.model_dump_json()
    assert moved.analysis.model_fields_set == original.analysis.model_fields_set
    assert moved.analysis_generation == original.analysis_generation
    assert not repo.list_result_applications(11, number)
    assert repo.get_computation_result(result.result_id) == result
    with sqlite3.connect(path) as connection:
        after = connection.execute("SELECT analysis_json FROM bills").fetchone()[0]
    assert "modern_primary_ref" not in after
    repo.save_analysis(11, number, moved.analysis)
    assert repo.get(11, number) == moved
    repo.restore(repo.dump())
    assert repo.get(11, number) == moved
    repo.close()


@pytest.mark.parametrize("fault", ["constraint", "outer-caller"])
def test_government_move_and_materialization_roll_back_together(
    tmp_path: Path, process_3039: ProcessDetail, fault: str
) -> None:
    path = tmp_path / "move-rollback.db"
    repo = SqliteBillRepository(path)
    repo.migrate()
    number = "RCL/123"
    source = process_3039.model_copy(update={"number": number})
    repo.upsert_summary(source, now=NOW)
    result = retained_result("analysis").model_copy(update={"number": number})
    assert isinstance(result.record, AnalysisRecord), "the fixture has a primary paid record"
    repo.save_computation_result(result)
    repo.save_analysis(10, number, result.record)
    if fault == "constraint":
        repo.upsert_summary(source.model_copy(update={"term": 11}), now=NOW)
    before = repo.dump()

    if fault == "constraint":
        with pytest.raises(sqlite3.IntegrityError):
            repo.move_government_rows(10, 11)
    else:
        with pytest.raises(RuntimeError, match="caller rolled back"), repo.atomic():
            assert repo.move_government_rows(10, 11) == 1
            assert repo.get(11, number) is not None
            raise RuntimeError("caller rolled back")

    assert repo.dump() == before
    repo.close()
