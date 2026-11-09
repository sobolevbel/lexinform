import hashlib
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import JsonValue

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.legacy_results import hydrate_legacy_value
from lexinform.models.legacy_results import (
    MAX_LEGACY_BATCH_TEXT_BYTES,
    LegacyBatchTextValue,
    LegacyResultImport,
    LegacyResultOccurrence,
    LegacyResultValue,
    canonical_legacy_json,
)

NOW = datetime(2026, 11, 9, tzinfo=UTC)
TEXT = "Przepis wchodzi w życie.\r\nZałącznik\x0c"


def batch_value() -> LegacyResultValue:
    body = LegacyBatchTextValue(text=TEXT, text_sha256=hashlib.sha256(TEXT.encode()).hexdigest())
    return LegacyResultValue(value_json=canonical_legacy_json(body.model_dump(mode="json")))


@pytest.mark.parametrize(
    ("term", "source", "stored_text", "error"),
    [
        (10, "a" * 64, None, None),
        (8, "a" * 64, None, "consumed batch owner"),
        (10, None, None, "pinned import snapshot"),
        (10, "a" * 64, "different", "source column"),
    ],
)
def test_batch_text_reader_requires_exact_bound_source(
    tmp_path: Path, term: int, source: str | None, stored_text: str | None, error: str | None
) -> None:
    database = tmp_path / "legacy-batch-text.db"
    with closing(SqliteBillRepository(database)) as repo:
        repo.migrate()
        with closing(sqlite3.connect(database)) as conn, conn:
            conn.execute(
                "INSERT INTO llm_batches"
                "(batch_id,provider,call_kind,submitted_at,status,request_count,estimated_cost_usd)"
                " VALUES ('batch','openai','analysis',?,'completed',1,0)",
                (NOW.isoformat(),),
            )
            meta: dict[str, JsonValue] = {"request_sha256": "b" * 64}
            if stored_text is not None:
                meta["text"] = stored_text
            conn.execute(
                "INSERT INTO llm_batch_items"
                "(batch_id,custom_id,call_kind,term,number,meta_json,consumed_at)"
                " VALUES ('batch','item','analysis',10,'123',?,?)",
                (canonical_legacy_json(meta), NOW.isoformat()),
            )
        value = batch_value()
        imported = LegacyResultImport(
            imported_at=NOW,
            source_sha256=source,
            values=(value,),
            occurrences=(
                LegacyResultOccurrence(
                    value_id=value.value_id,
                    source_table="llm_batch_items",
                    source_key_json='["batch","item"]',
                    source_column="meta_json",
                    source_path="/text",
                    container_term=term,
                    container_number="123",
                    purpose="analysis",
                ),
            ),
        )
        if error is not None:
            before = repo.dump()
            with pytest.raises(ValueError, match=error):
                repo.import_legacy_results(imported)
            assert repo.dump() == before
            return
        assert repo.import_legacy_results(imported)
        retained = repo.get_legacy_result(value.value_id)
        assert retained is not None, "the importer saved the immutable input"
        body = retained.batch_text
        assert body is not None, "the tagged body is a batch text value"
        assert body.text.encode() == TEXT.encode()
        assert not repo.import_legacy_results(imported)
        with closing(sqlite3.connect(database)) as conn, conn:
            conn.execute("DELETE FROM llm_batch_items WHERE batch_id='batch' AND custom_id='item'")
        dump = repo.dump()
        repo.restore(dump)
        assert repo.dump() == dump
        assert repo.get_legacy_result(value.value_id) == value


def test_batch_text_cannot_supply_an_answer_projection() -> None:
    value = batch_value()
    with pytest.raises(ValueError, match="cannot hydrate an answer"):
        hydrate_legacy_value({"legacy_result_ref": value.value_id}, lambda _: value)
    with pytest.raises(ValueError, match="checksum"):
        LegacyBatchTextValue(text=TEXT, text_sha256="0" * 64)


def test_batch_text_limit_counts_utf8_bytes() -> None:
    text = "ą" * (MAX_LEGACY_BATCH_TEXT_BYTES // 2 + 1)
    assert len(text) < MAX_LEGACY_BATCH_TEXT_BYTES
    with pytest.raises(ValueError, match="UTF-8 byte limit"):
        LegacyBatchTextValue(text=text, text_sha256=hashlib.sha256(text.encode()).hexdigest())
