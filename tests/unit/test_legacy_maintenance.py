import hashlib
import json
import sqlite3
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.legacy_maintenance import (
    LegacyMaintenancePlan,
    LegacyProjectionReplacement,
    LegacySnapshotColumn,
    plan_legacy_maintenance,
)
from lexinform.models import AnalysisRecord, ProcessDetail
from lexinform.models.legacy_results import canonical_legacy_json, legacy_json
from tests.fakes import make_analysis

NOW = datetime(2026, 10, 28, 19, tzinfo=UTC)
SNAPSHOT_SHA = "a" * 64


def exact_value(**updates: JsonValue) -> dict[str, JsonValue]:
    value: dict[str, JsonValue] = {
        "model": "saved",
        "prompt_version": "old",
        "created_at": None,
        "analysis": {"summary": "immutable answer", "opaque": {"zero": 0}},
        "unknown": [None, False, ""],
    }
    value.update(updates)
    return value


def column(
    value: JsonValue,
    *,
    table: str = "bills",
    name: str = "analysis_json",
    key: str | None = None,
) -> LegacySnapshotColumn:
    if key is None:
        key = {
            "bills": '[10,"3039"]',
            "status_changes": "[1]",
            "publications": "[1]",
            "llm_batch_items": '["batch","item"]',
        }.get(table, '["key"]')
    return LegacySnapshotColumn(table, key, name, canonical_legacy_json(value), 10, "3039")


def planned(*columns: LegacySnapshotColumn, share: bool = True) -> LegacyMaintenancePlan:
    return plan_legacy_maintenance(
        columns, snapshot_sha256=SNAPSHOT_SHA, imported_at=NOW, share_analysis_bodies=share
    )


@pytest.mark.parametrize(
    ("table", "name", "wrapped"),
    [
        ("bills", "analysis_json", False),
        ("bills", "joint_json", False),
        ("bills", "consultation_review_json", False),
        ("bills", "commission_review_json", False),
        ("bills", "act_application_review_json", False),
        ("bills", "ready_analysis_json", True),
        ("status_changes", "amendments_json", False),
        ("status_changes", "supplements_json", True),
        ("analysis_memo", "record_json", False),
    ],
)
def test_only_approved_direct_record_paths_are_compacted(
    table: str, name: str, wrapped: bool
) -> None:
    saved = exact_value()
    value: JsonValue = saved
    if wrapped:
        value = {"record": saved, "other": saved} if table == "bills" else [saved, "opaque"]
    plan = planned(column(value, table=table, name=name))
    assert plan.imported is not None, "the fixture contains one exact legacy value"
    assert len(plan.replacements) == 1
    actual = legacy_json(plan.replacements[0].replacement_json)
    ref: dict[str, JsonValue] = {"legacy_result_ref": plan.imported.values[0].value_id}
    expected: JsonValue = ref
    if wrapped:
        expected = {"record": ref, "other": saved} if table == "bills" else [ref, "opaque"]
    assert actual == expected
    assert plan.imported.values[0].value_json == canonical_legacy_json(saved)


@pytest.mark.parametrize(
    ("table", "name"),
    [
        ("bills", "supplements_json"),
        ("bills", "review_work_json"),
        ("publications", "delivery_json"),
        ("document_work", "work_json"),
        ("llm_batch_items", "meta_json"),
        ("llm_batch_intents", "meta_json"),
        ("content_snapshots", "payload_json"),
    ],
)
def test_evidence_only_locations_are_archived_without_physical_rewrites(
    table: str, name: str
) -> None:
    encoded = json.dumps({"analysis": exact_value()}, indent=2)
    plan = planned(column({"bill_json": encoded}, table=table, name=name))
    assert plan.imported is not None, "the encoded snapshot contains an exact legacy value"
    assert not plan.replacements
    assert plan.imported.occurrences[0].source_path == "/bill_json/analysis"
    assert plan.imported.occurrences[0].purpose is None
    assert "provider" not in plan.imported.values[0].value_json


def test_exact_variants_share_only_repeated_analysis_bodies_without_upcasting() -> None:
    first = column(exact_value(source_checked_at=None))
    second = column(
        exact_value(source_checked_at="2026-01-02", input_tokens=0),
        table="analysis_memo",
        name="record_json",
        key='["memo"]',
    )
    plan = planned(first, second)
    assert plan == planned(first, second)
    assert plan.imported is not None, "two wrapper variants contain exact legacy values"
    assert len(plan.imported.values) == 2 and len(plan.shared) == 2
    assert len({item.body_sha256 for item in plan.shared}) == 1
    assert all("mnemonic_candidate" not in item.body_json for item in plan.shared)
    assert all('"analysis"' not in item.envelope_json for item in plan.shared)
    assert not planned(first).shared and not planned(first, second, share=False).shared
    attribute = "snapshot_sha256"
    with pytest.raises(FrozenInstanceError):
        setattr(plan, attribute, "b" * 64)


def test_modern_refs_placeholders_and_unknown_context_remain_explicit() -> None:
    placeholder = exact_value(analysis=None)
    memo = LegacySnapshotColumn(
        "analysis_memo", '["unknown"]', "record_json", json.dumps(placeholder)
    )
    plan = planned(column(exact_value(computation_id="known-modern")), memo)
    assert plan.modern_occurrences == 1
    assert plan.imported is not None, "the placeholder is retained without claiming an answer"
    assert len(plan.imported.values) == 1 and not plan.replacements
    observed = plan.imported.occurrences[0]
    assert observed.container_term is None and observed.container_number is None
    assert observed.purpose is None
    assert plan.imported.values[0].value_json == canonical_legacy_json(placeholder)


@pytest.mark.parametrize(
    "raw",
    [
        '{"key":0,"key":1}',
        '{"bill_json":"{invalid"}',
        '{"bill_json":"{\\"n\\":0,\\"n\\":1}"}',
        '{"bill_json":"{\\"legacy_result_ref\\":\\"bad\\"}"}',
        '{"legacy_result_ref":"bad"}',
        '{"projection_overrides":{}}',
        '{"n":NaN}',
        '{"n":1e999}',
    ],
)
def test_corrupt_or_unaccounted_evidence_fails_closed(raw: str) -> None:
    with pytest.raises(ValueError):
        planned(LegacySnapshotColumn("bills", '[10,"3039"]', "analysis_json", raw))


def test_escaped_paths_do_not_merge_distinct_opaque_keys() -> None:
    plan = planned(column({"a/b": exact_value(), "a": {"b": exact_value()}}))
    assert plan.imported is not None, "the fixture has two observed locations"
    assert {item.source_path for item in plan.imported.occurrences} == {"/a~1b", "/a/b"}
    assert all(item.purpose is None for item in plan.imported.occurrences)
    assert not plan.replacements


@pytest.mark.parametrize(
    "saved",
    [
        LegacySnapshotColumn("commands", "[1]", "text", "{}"),
        LegacySnapshotColumn("bills", '[10,"3039"]', "summary_json", "{}"),
        LegacySnapshotColumn("analysis_memo", '["key","extra"]', "record_json", "{}"),
        LegacySnapshotColumn("analysis_memo", "[true]", "record_json", "{}"),
    ],
)
def test_the_pure_plan_rejects_locations_outside_its_fixed_contract(
    saved: LegacySnapshotColumn,
) -> None:
    with pytest.raises(ValueError):
        planned(saved)


def test_nesting_limits_and_duplicate_physical_columns_fail_closed() -> None:
    nested: JsonValue = exact_value()
    for _ in range(52):
        nested = [nested]
    with pytest.raises(ValueError, match="nesting"):
        planned(column(nested))
    saved = column(exact_value())
    with pytest.raises(ValueError, match="repeat"):
        planned(saved, saved)


def seed_repository(
    path: Path, *, padding: bool = True, opaque: bool = True
) -> SqliteBillRepository:
    repo = SqliteBillRepository(path)
    repo.migrate()
    title = (
        "".join(hashlib.sha256(str(index).encode()).hexdigest() for index in range(2000))
        if padding
        else "Bill"
    )
    repo.upsert_summary(
        ProcessDetail(term=10, number="3039", title=title, document_type="bill", change_date=NOW),
        now=NOW,
    )
    record = AnalysisRecord(
        analysis=make_analysis(),
        model="saved",
        prompt_version="old",
        created_at=NOW,
        input_chars=100,
        truncated=False,
        text_source="pdf",
    )
    raw = record.model_dump(mode="json")
    if opaque:
        raw["unknown"] = {"null": None, "exact": [0, False, ""]}
    other = {**raw, "source_checked_at": "2026-01-02T00:00:00Z"}
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE bills SET analysis_json=?", (json.dumps(raw),))
        connection.execute(
            "INSERT INTO analysis_memo(key,record_json) VALUES ('memo',?)", (json.dumps(other),)
        )
    return repo


def counts(path: Path) -> tuple[int, ...]:
    with sqlite3.connect(path) as connection:
        return tuple(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "legacy_result_imports",
                "legacy_result_values",
                "legacy_result_occurrences",
                "legacy_analysis_bodies",
            )
        )


def test_atomic_apply_preserves_exact_archive_readers_and_once_only_boundary(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    repo = seed_repository(path)
    before, view, memo = repo.dump(), repo.get(10, "3039"), repo.load_analysis_memo()
    plan = repo.plan_legacy_maintenance(imported_at=NOW)
    assert repo.dump() == before
    assert plan.snapshot_sha256 == hashlib.sha256(before.encode()).hexdigest()
    result = repo.apply_legacy_maintenance(plan)
    assert result.applied and counts(path) == (1, 2, 2, 1)
    assert result.baseline_gzip_bytes is not None and result.compact_gzip_bytes is not None, (
        "a successful apply measures both exact dumps"
    )
    assert result.compact_gzip_bytes * 100 <= result.baseline_gzip_bytes * 105
    assert repo.get(10, "3039") == view
    assert {key: legacy_json(raw) for key, raw in repo.load_analysis_memo().items()} == {
        key: legacy_json(raw) for key, raw in memo.items()
    }
    assert plan.imported is not None, "the seeded repository contains exact legacy values"
    assert all(repo.get_legacy_result(value.value_id) == value for value in plan.imported.values)
    compact = repo.dump()
    restored = SqliteBillRepository(":memory:")
    restored.restore(compact)
    assert restored.dump() == compact and restored.get(10, "3039") == view
    assert not repo.apply_legacy_maintenance(plan).applied and repo.dump() == compact
    repo.save_analysis_memo("new-later-key", json.dumps(exact_value()))
    later = repo.dump()
    repeated = repo.plan_legacy_maintenance(imported_at=NOW + timedelta(days=1))
    assert repeated.existing_manifest_sha256 == result.manifest_sha256
    assert repeated.surveyed_columns == 0 and repeated.imported is None
    assert not repo.apply_legacy_maintenance(repeated).applied
    assert repo.dump() == later and counts(path) == (1, 2, 2, 1)
    restored.close()
    repo.close()


def test_empty_import_does_not_burn_marker(tmp_path: Path) -> None:
    path = tmp_path / "empty.db"
    repo = SqliteBillRepository(path)
    repo.migrate()
    before = repo.dump()
    plan = repo.plan_legacy_maintenance(imported_at=NOW)
    assert plan.imported is None and not repo.apply_legacy_maintenance(plan).applied
    assert counts(path) == (0, 0, 0, 0) and repo.dump() == before
    repo.close()


def test_stale_snapshot_is_rejected_before_any_import(tmp_path: Path) -> None:
    path = tmp_path / "stale.db"
    repo = seed_repository(path)
    plan = repo.plan_legacy_maintenance(imported_at=NOW)
    repo.save_analysis_memo("new", json.dumps(exact_value()))
    before = repo.dump()
    with pytest.raises(ValueError, match="snapshot changed"):
        repo.apply_legacy_maintenance(plan)
    assert repo.dump() == before and counts(path) == (0, 0, 0, 0)
    repo.close()


@pytest.mark.parametrize("tampering", ["replacement", "archive", "sharing", "counts"])
def test_recomputed_plan_rejects_tampering_before_writes(tmp_path: Path, tampering: str) -> None:
    path = tmp_path / "tampered.db"
    repo = seed_repository(path)
    plan = repo.plan_legacy_maintenance(imported_at=NOW)
    if tampering == "replacement":
        first = plan.replacements[0]
        plan = replace(
            plan,
            replacements=(
                LegacyProjectionReplacement(replace(first.original, table="commands"), "null"),
            ),
        )
    elif tampering == "archive":
        assert plan.imported is not None, "the seeded repository produces a finite import"
        plan = replace(plan, imported=plan.imported.model_copy(update={"occurrences": ()}))
    elif tampering == "sharing":
        plan = replace(plan, shared=())
    else:
        plan = replace(plan, surveyed_columns=0)
    before = repo.dump()
    with pytest.raises(ValueError, match="plan does not match"):
        repo.apply_legacy_maintenance(plan)
    assert repo.dump() == before and counts(path) == (0, 0, 0, 0)
    repo.close()


def test_budget_failure_rolls_back_import_sharing_and_replacements(tmp_path: Path) -> None:
    path = tmp_path / "over-budget.db"
    repo = seed_repository(path, padding=False)
    before = repo.dump()
    plan = repo.plan_legacy_maintenance(imported_at=NOW)
    with pytest.raises(ValueError, match="105% gzip"):
        repo.apply_legacy_maintenance(plan)
    assert repo.dump() == before and counts(path) == (0, 0, 0, 0)
    repo.close()


def test_physical_cas_failure_rolls_back_nested_import(tmp_path: Path) -> None:
    path = tmp_path / "cas.db"
    repo = seed_repository(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TRIGGER ignore_projection BEFORE UPDATE OF analysis_json ON bills"
            " BEGIN SELECT RAISE(IGNORE); END"
        )
    before = repo.dump()
    plan = repo.plan_legacy_maintenance(imported_at=NOW)
    with pytest.raises(ValueError, match="physical value changed"):
        repo.apply_legacy_maintenance(plan)
    assert repo.dump() == before and counts(path) == (0, 0, 0, 0)
    repo.close()


def test_unrelated_broken_foreign_key_rolls_back_the_candidate(tmp_path: Path) -> None:
    path = tmp_path / "fk.db"
    repo = seed_repository(path)
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO publication_content VALUES (1,'missing-content')")
    before = repo.dump()
    plan = repo.plan_legacy_maintenance(imported_at=NOW)
    with pytest.raises(ValueError, match="foreign keys"):
        repo.apply_legacy_maintenance(plan)
    assert repo.dump() == before and counts(path) == (0, 0, 0, 0)
    repo.close()


def test_invalid_existing_reader_model_blocks_import_before_writes(tmp_path: Path) -> None:
    path = tmp_path / "model.db"
    repo = seed_repository(path)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE bills SET analysis_json='{}'")
    before = repo.dump()
    plan = repo.plan_legacy_maintenance(imported_at=NOW)
    with pytest.raises(ValueError):
        repo.apply_legacy_maintenance(plan)
    assert repo.dump() == before and counts(path) == (0, 0, 0, 0)
    repo.close()


def test_restore_validation_failure_rolls_back_every_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "restore-failure.db"
    repo = seed_repository(path)
    before = repo.dump()
    plan = repo.plan_legacy_maintenance(imported_at=NOW)

    def fail_restore(self: SqliteBillRepository, script: str) -> None:
        raise ValueError("forced restore refusal")

    monkeypatch.setattr(SqliteBillRepository, "restore", fail_restore)
    with pytest.raises(ValueError, match="forced restore refusal"):
        repo.apply_legacy_maintenance(plan)
    assert repo.dump() == before and counts(path) == (0, 0, 0, 0)
    repo.close()


def test_outer_transaction_can_still_rollback_successful_apply(tmp_path: Path) -> None:
    path = tmp_path / "outer.db"
    repo = seed_repository(path)
    before = repo.dump()
    plan = repo.plan_legacy_maintenance(imported_at=NOW)
    repo.begin()
    assert repo.apply_legacy_maintenance(plan).applied
    repo.rollback()
    assert repo.dump() == before and counts(path) == (0, 0, 0, 0)
    repo.close()


def test_corrupted_existing_archive_blocks_noop(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.db"
    repo = seed_repository(path)
    plan = repo.plan_legacy_maintenance(imported_at=NOW)
    repo.apply_legacy_maintenance(plan)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE legacy_analysis_bodies SET body_json='{}'")
    with pytest.raises(ValueError, match="checksum"):
        repo.plan_legacy_maintenance(imported_at=NOW)
    with pytest.raises(ValueError, match="checksum"):
        repo.apply_legacy_maintenance(plan)
    repo.close()


def test_analysis_quiet_refresh_preserves_original_import_counts(tmp_path: Path) -> None:
    path = tmp_path / "quiet.db"
    repo = seed_repository(path, opaque=False)
    repo.apply_legacy_maintenance(repo.plan_legacy_maintenance(imported_at=NOW))
    bill = repo.get(10, "3039")
    assert bill is not None and bill.analysis is not None, "the seeded bill has an analysis"
    record = bill.analysis.model_copy(update={"source_checked_at": NOW + timedelta(days=1)})
    repo.save_analysis(10, "3039", record)
    with sqlite3.connect(path) as connection:
        saved = cast(str, connection.execute("SELECT analysis_json FROM bills").fetchone()[0])
    assert "legacy_result_ref" in saved
    assert counts(path) == (1, 2, 2, 1)
    repo.close()
