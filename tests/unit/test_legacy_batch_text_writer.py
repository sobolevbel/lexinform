import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import JsonValue

from lexinform.adapters.legacy_history import read_legacy_batch_item_meta, read_legacy_batch_text
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.legacy_maintenance import (
    LegacyMaintenancePlan,
    LegacySnapshotColumn,
    plan_legacy_maintenance,
)
from lexinform.models.batch import batch_item_meta
from lexinform.models.legacy_results import (
    MAX_LEGACY_BATCH_TEXT_BYTES,
    canonical_legacy_json,
    legacy_json,
)

TEXT = "Przepis wchodzi w życie.\r\nZałącznik\x0c"
NOW = datetime(2026, 11, 9, tzinfo=UTC)


def batch_column(
    number: str, *, purpose: str = "analysis", digest: str = "a" * 64, text: str = TEXT
) -> LegacySnapshotColumn:
    meta: dict[str, JsonValue] = {
        "input_chars": len(text),
        "truncated": False,
        "text_source": "documents",
        "source_kind": "rcl",
        "source_url": None,
        "revision": 1,
        "text_sha256": None,
        "text": text,
        "memo_key": "b" * 64,
        "request_sha256": digest,
        "input_binding": {"term": 10, "number": number, "reuse_key": "b" * 64},
        "frozen_request_manifest": {
            "provider": "openai",
            "purpose": purpose,
            "request_sha256": "a" * 64,
            "model": "saved",
            "prompt_version": "saved",
            "payload_bytes": 123,
            "messages_sha256": "b" * 64,
            "output_format_sha256": "c" * 64,
            "options_sha256": "d" * 64,
        },
    }
    key = canonical_legacy_json(["batch", number])
    return LegacySnapshotColumn(
        "llm_batch_items", key, "meta_json", canonical_legacy_json(meta), 10, number
    )


def test_settled_batch_text_moves_once_and_preserves_separate_owners() -> None:
    first, second, pending = (batch_column(number) for number in ("RCL/1", "RCL/2", "RCL/3"))
    plan = plan_legacy_maintenance(
        (first, second, pending),
        snapshot_sha256="f" * 64,
        imported_at=NOW,
        retain_batch_text=True,
        settled_batch_inputs={item.key_json: ("analysis", "openai") for item in (first, second)},
    )
    assert plan.imported is not None, "both eligible inputs carry exact text"
    (stored,) = plan.imported.values
    body = stored.batch_text
    assert body is not None, "the immutable value is a batch text"
    assert body.text.encode() == TEXT.encode()
    assert {occ.container_number for occ in plan.imported.occurrences} == {"RCL/1", "RCL/2"}
    assert {occ.source_path for occ in plan.imported.occurrences} == {"/text"}
    assert {change.original for change in plan.replacements} == {first, second}
    for change in plan.replacements:
        original = legacy_json(change.original.raw_json)
        replacement = legacy_json(change.replacement_json)
        assert isinstance(original, dict) and isinstance(replacement, dict), (
            "the planner preserves the exact batch metadata object"
        )
        assert "text" not in replacement
        assert {**replacement, "text": TEXT} == original


@pytest.mark.parametrize(
    ("number", "provider", "digest"),
    [("RCL/2", "anthropic", "a" * 64), ("RCL/2", "openai", "e" * 64)],
)
def test_settled_batch_text_rejects_request_mismatch(
    number: str, provider: str, digest: str
) -> None:
    column = batch_column(number, digest=digest)
    with pytest.raises(ValueError, match="request"):
        plan_legacy_maintenance(
            (column,),
            snapshot_sha256="f" * 64,
            imported_at=NOW,
            retain_batch_text=True,
            settled_batch_inputs={column.key_json: ("analysis", provider)},
        )


def test_legacy_batch_lookup_rehydrates_only_exact_owner_and_key(tmp_path: Path) -> None:
    path = tmp_path / "legacy-text.db"
    column = batch_column("RCL/1")
    plan = plan_legacy_maintenance(
        (column,),
        snapshot_sha256="f" * 64,
        imported_at=NOW,
        retain_batch_text=True,
        settled_batch_inputs={column.key_json: ("analysis", "openai")},
    )
    assert plan.imported is not None, "the settled batch supplies its observed exact text"
    (replacement,) = plan.replacements
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute(
                "INSERT INTO llm_batches"
                "(batch_id,provider,call_kind,submitted_at,status,request_count,estimated_cost_usd)"
                " VALUES ('batch','openai','analysis',?,'completed',1,0)",
                (NOW.isoformat(),),
            )
            conn.execute(
                "INSERT INTO llm_batch_items"
                "(batch_id,custom_id,call_kind,term,number,meta_json,consumed_at)"
                " VALUES ('batch','RCL/1','analysis',10,'RCL/1',?,?)",
                (replacement.replacement_json, NOW.isoformat()),
            )
        assert repo.import_legacy_results(plan.imported)
        with closing(sqlite3.connect(path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM llm_batch_items").fetchone()
            assert read_legacy_batch_item_meta(conn, row) == batch_item_meta(
                "analysis", column.raw_json
            )
            assert read_legacy_batch_text(conn, "batch", "RCL/1", 10, "RCL/2", "analysis") is None
            assert read_legacy_batch_text(conn, "batch", "other", 10, "RCL/1", "analysis") is None


def test_batch_text_retention_is_opt_in() -> None:
    column = batch_column("RCL/1")
    plan = plan_legacy_maintenance(
        (column,),
        snapshot_sha256="f" * 64,
        imported_at=NOW,
        settled_batch_inputs={column.key_json: ("analysis", "openai")},
    )
    assert not plan.retain_batch_text
    assert plan.imported is None and not plan.replacements


def test_opted_in_import_caps_distinct_utf8_bodies_at_one_mib() -> None:
    body = "ą" * (MAX_LEGACY_BATCH_TEXT_BYTES // 2)
    first = batch_column("RCL/1", text=body)
    second = batch_column("RCL/2", text=body)
    extra = batch_column("RCL/3", text="!")
    oversized = batch_column("RCL/4", text=body + "!")
    eligible = {item.key_json: ("analysis", "openai") for item in (first, second, extra, oversized)}

    def plan(*columns: LegacySnapshotColumn) -> LegacyMaintenancePlan:
        return plan_legacy_maintenance(
            columns,
            snapshot_sha256="f" * 64,
            imported_at=NOW,
            retain_batch_text=True,
            settled_batch_inputs=eligible,
        )

    retained = plan(first, second).imported
    assert retained is not None, "the duplicate bodies share one value at the byte limit"
    assert len(retained.values) == 1 and len(retained.occurrences) == 2
    with pytest.raises(ValueError, match="cumulative UTF-8 byte limit"):
        plan(first, second, extra)
    with pytest.raises(ValueError, match="cumulative UTF-8 byte limit"):
        plan(oversized)
