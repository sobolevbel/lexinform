"""Read and validate the existing immutable legacy archive without new historical inference."""

import re
import sqlite3
from contextlib import closing

from lexinform.legacy_results import (
    LEGACY_PROJECTION_COLUMNS,
    legacy_analysis_body,
    materialize_legacy_value,
    read_legacy_json,
)
from lexinform.models import LegacyResultImport, LegacyResultOccurrence, LegacyResultValue
from lexinform.models.batch import BatchItemMeta, DigestItemMeta, batch_item_meta
from lexinform.models.legacy_results import LegacyBatchTextValue, canonical_legacy_json, legacy_json


def read_legacy_result(connection: sqlite3.Connection, value_id: str) -> LegacyResultValue | None:
    row = connection.execute(
        "SELECT envelope_json, analysis_body_sha256 FROM legacy_result_values WHERE value_id = ?",
        (value_id,),
    ).fetchone()
    if row is None:
        return None
    body = None
    if row[1] is not None:
        body = connection.execute(
            "SELECT body_json FROM legacy_analysis_bodies WHERE body_sha256 = ?", (row[1],)
        ).fetchone()
    value = materialize_legacy_value(
        row[0], body_sha256=row[1], body_json=body[0] if body else None
    )
    if value.value_id != value_id:
        raise ValueError("immutable legacy value checksum mismatch")
    return value


def read_legacy_batch_text(
    connection: sqlite3.Connection,
    batch_id: str,
    custom_id: str,
    term: int,
    number: str,
    purpose: str,
) -> LegacyBatchTextValue | None:
    row = connection.execute(
        "SELECT o.value_id FROM legacy_result_occurrences o"
        " JOIN legacy_result_imports i USING(import_id)"
        " WHERE o.source_table='llm_batch_items' AND o.source_key_json=?"
        " AND o.source_column='meta_json' AND o.source_path='/text'"
        " AND o.container_term=? AND o.container_number=? AND o.purpose=?"
        " AND i.source_sha256 IS NOT NULL",
        (canonical_legacy_json([batch_id, custom_id]), term, number, purpose),
    ).fetchone()
    if row is None:
        return None
    value = read_legacy_result(connection, row[0])
    if value is None or (body := value.batch_text) is None:
        raise ValueError("missing immutable legacy batch text")
    return body


def read_legacy_batch_item_meta(
    connection: sqlite3.Connection, row: sqlite3.Row
) -> BatchItemMeta | DigestItemMeta:
    meta = batch_item_meta(row["call_kind"], row["meta_json"])
    if (
        not row["consumed_at"]
        or not isinstance(meta, BatchItemMeta)
        or meta.text
        or meta.prepared_input_receipt is not None
        or meta.frozen_request_manifest is None
        or meta.input_binding is None
    ):
        return meta
    body = read_legacy_batch_text(
        connection, row["batch_id"], row["custom_id"], row["term"], row["number"], row["call_kind"]
    )
    if body is None:
        return meta
    if (meta.input_binding.term, meta.input_binding.number) != (row["term"], row["number"]) or (
        meta.input_chars != len(body.text)
    ):
        raise ValueError("immutable legacy batch text differs from its original metadata")
    return meta.model_copy(update={"text": body.text})


def _validate_batch_text_occurrence(
    connection: sqlite3.Connection, occurrence: LegacyResultOccurrence, body: LegacyBatchTextValue
) -> None:
    key = legacy_json(occurrence.source_key_json)
    if (
        (occurrence.source_table, occurrence.source_column, occurrence.source_path)
        != ("llm_batch_items", "meta_json", "/text")
        or not isinstance(key, list)
        or len(key) != 2
        or any(not isinstance(part, str) or not part for part in key)
        or occurrence.container_term is None
        or not occurrence.container_number
        or occurrence.purpose not in ("analysis", "reanalysis")
    ):
        raise ValueError("legacy batch text requires its exact source occurrence")
    saved = connection.execute(
        "SELECT term, number, call_kind, consumed_at, meta_json"
        " FROM llm_batch_items WHERE batch_id=? AND custom_id=?",
        key,
    ).fetchone()
    if saved is None:
        return
    if (
        tuple(saved[:3])
        != (occurrence.container_term, occurrence.container_number, occurrence.purpose)
        or not saved[3]
    ):
        raise ValueError("legacy batch text differs from its consumed batch owner")
    meta = legacy_json(saved[4])
    if not isinstance(meta, dict) or ("text" in meta and meta["text"] != body.text):
        raise ValueError("legacy batch text differs from its source column")
    request_sha256 = meta.get("request_sha256")
    if request_sha256 is not None and (
        not isinstance(request_sha256, str) or re.fullmatch(r"[a-f0-9]{64}", request_sha256) is None
    ):
        raise ValueError("invalid observed legacy batch request checksum")


def validate_legacy_import(connection: sqlite3.Connection) -> None:
    for body_sha, raw in connection.execute(
        "SELECT body_sha256, body_json FROM legacy_analysis_bodies"
    ):
        legacy_analysis_body(raw, body_sha)
    for table in ("legacy_result_values", "legacy_result_occurrences"):
        if connection.execute(f"PRAGMA foreign_key_check({table})").fetchone() is not None:
            raise ValueError("legacy result import has a broken reference")
    markers = connection.execute(
        "SELECT manifest_sha256, imported_at, source_sha256 FROM legacy_result_imports"
    ).fetchall()
    if len(markers) > 1:
        raise ValueError("legacy result import has more than one marker")
    values: list[LegacyResultValue] = []
    for (value_id,) in connection.execute("SELECT value_id FROM legacy_result_values"):
        value = read_legacy_result(connection, value_id)
        assert value is not None, "the selected legacy result row exists on this connection"
        values.append(value)
    occurrences = tuple(
        LegacyResultOccurrence.model_validate(
            {
                "value_id": row[0],
                "source_table": row[1],
                "source_key_json": row[2],
                "source_column": row[3],
                "source_path": row[4],
                "container_term": row[5],
                "container_number": row[6],
                "purpose": row[7],
            }
        )
        for row in connection.execute(
            "SELECT value_id, source_table, source_key_json, source_column, source_path,"
            " container_term, container_number, purpose FROM legacy_result_occurrences"
        )
    )
    if not markers:
        if (
            values
            or occurrences
            or connection.execute("SELECT 1 FROM legacy_analysis_bodies LIMIT 1").fetchone()
        ):
            raise ValueError("legacy result rows require an import marker")
        return
    manifest_sha, imported_at, source_sha = markers[0]
    imported = LegacyResultImport(
        imported_at=imported_at,
        source_sha256=source_sha,
        values=tuple(values),
        occurrences=occurrences,
    )
    if manifest_sha != imported.manifest_sha256:
        raise ValueError("legacy result import manifest checksum mismatch")
    batch_texts = {
        value.value_id: body for value in imported.values if (body := value.batch_text) is not None
    }
    if batch_texts and imported.source_sha256 is None:
        raise ValueError("legacy batch text requires a pinned import snapshot")
    for occurrence in imported.occurrences:
        if (body := batch_texts.get(occurrence.value_id)) is not None:
            _validate_batch_text_occurrence(connection, occurrence, body)


def validate_legacy_projections(connection: sqlite3.Connection) -> None:
    def read(value_id: str) -> LegacyResultValue | None:
        return read_legacy_result(connection, value_id)

    validate_legacy_import(connection)
    with closing(connection.cursor()) as cursor:
        cursor.row_factory = None
        for table in (
            "bills",
            "status_changes",
            "publications",
            "analysis_memo",
            "document_work",
            "llm_batch_items",
            "llm_batch_intents",
        ):
            columns = {
                row[1] for row in cursor.execute(f"PRAGMA table_info({table})")
            } & LEGACY_PROJECTION_COLUMNS
            for column in columns:
                for (raw,) in cursor.execute(
                    f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL"
                ):
                    read_legacy_json(raw, read)
