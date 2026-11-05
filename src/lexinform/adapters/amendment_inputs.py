"""Validate immutable amendment inputs and request-bound references with original lineage."""

import hashlib
import sqlite3
from collections.abc import Iterator

from lexinform.adapters.computation_history import read_computation_result
from lexinform.adapters.input_receipts import _parents, _reference, parse_batch_request
from lexinform.json_storage import canonical_json
from lexinform.models.amendment_inputs import AmendmentInputRef, AmendmentInputSnapshot
from lexinform.models.batch import BatchIntent, DigestItemMeta, LlmBatchItem, batch_item_meta
from lexinform.models.computation_inputs import ComputationInputBinding
from lexinform.models.computations import ComputationResult
from lexinform.models.input_receipts import amendment_context_sha256


class AmendmentInputError(ValueError):
    def __init__(self) -> None:
        super().__init__("invalid_amendment_input")


def read_amendment_input(
    connection: sqlite3.Connection, snapshot_id: str
) -> AmendmentInputSnapshot | None:
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        row = cursor.execute(
            "SELECT * FROM amendment_input_snapshots WHERE snapshot_id=?", (snapshot_id,)
        ).fetchone()
        if row is None:
            return None
        if not isinstance(row["question_json"], str):
            raise AmendmentInputError()
        snapshot = AmendmentInputSnapshot(
            profile=row["profile"], question_json=row["question_json"]
        )
        if (snapshot.snapshot_id, snapshot.profile, snapshot.question_sha256) != (
            row["snapshot_id"],
            row["profile"],
            row["question_sha256"],
        ):
            raise AmendmentInputError()
        return snapshot
    except ValueError, TypeError, IndexError, KeyError, RecursionError, sqlite3.Error:
        raise AmendmentInputError() from None
    finally:
        cursor.close()


def validate_amendment_ref(
    connection: sqlite3.Connection,
    reference: AmendmentInputRef | None,
    purpose: str,
    request_sha256: str | None,
    binding: ComputationInputBinding | None,
) -> None:
    if reference is None:
        return
    try:
        reference = AmendmentInputRef.model_validate(reference.model_dump())
        reference.require_carrier(purpose, request_sha256, binding)
        snapshot = read_amendment_input(connection, reference.snapshot_id)
        if (
            snapshot is None
            or snapshot.question_sha256 != reference.question_sha256
            or binding is None
            or snapshot.context.number != binding.number
        ):
            raise AmendmentInputError()
        receipt = reference.lineage_receipt
        if receipt is not None:
            context = snapshot.context
            if (
                receipt.context_sha256 != amendment_context_sha256(context)
                or receipt.text_sha256 != hashlib.sha256(context.text.encode()).hexdigest()
                or receipt.text_chars != len(context.text)
            ):
                raise AmendmentInputError()
            _parents(connection, receipt)
            if reference.lineage_result_id is not None:
                cursor = connection.cursor()
                cursor.row_factory = sqlite3.Row
                try:
                    row = cursor.execute(
                        "SELECT * FROM computation_results WHERE result_id=?",
                        (reference.lineage_result_id,),
                    ).fetchone()
                    original = read_computation_result(row) if row is not None else None
                finally:
                    cursor.close()
                if (
                    original is None
                    or original.purpose != "amendments"
                    or original.request_sha256 != receipt.request_sha256
                    or original.amendment_input_ref is None
                    or original.amendment_input_ref.snapshot_id != reference.snapshot_id
                    or original.amendment_input_ref.lineage_receipt != receipt
                    or original.amendment_input_ref.lineage_result_id is not None
                ):
                    raise AmendmentInputError()
                validate_result_input(connection, original)
    except ValueError, TypeError, IndexError, KeyError, RecursionError, sqlite3.Error:
        raise AmendmentInputError() from None


def _direct_receipt(meta: DigestItemMeta) -> None:
    reference = meta.amendment_input_ref
    if (
        reference is not None
        and reference.lineage_result_id is None
        and reference.lineage_receipt is not None
        and meta.prepared_input_receipt is not None
        and reference.lineage_receipt != meta.prepared_input_receipt
    ):
        raise AmendmentInputError()


def amendment_input_issues(connection: sqlite3.Connection) -> Iterator[tuple[str, str]]:
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if (
            "amendment_input_snapshots" not in tables
            and connection.execute("PRAGMA user_version").fetchone()[0] >= 67
        ):
            yield "invalid_amendment_input", "amendment_input_snapshots"
            return
        for row in (
            cursor.execute("SELECT snapshot_id FROM amendment_input_snapshots")
            if "amendment_input_snapshots" in tables
            else ()
        ):
            try:
                read_amendment_input(connection, row["snapshot_id"])
            except AmendmentInputError:
                yield (
                    "invalid_amendment_input",
                    _reference("amendment_input_snapshots", row["snapshot_id"]),
                )
        for row in (
            cursor.execute("SELECT * FROM computation_results")
            if "computation_results" in tables
            else ()
        ):
            try:
                result = read_computation_result(row)
            except ValueError, TypeError, RecursionError:
                continue
            try:
                validate_result_input(connection, result)
            except AmendmentInputError:
                yield "invalid_amendment_input", _reference("computation_results", row["result_id"])
        for table in ("llm_batch_intents", "llm_batch_items"):
            if table not in tables:
                continue
            for row in cursor.execute("SELECT * FROM " + table):
                try:
                    request = (
                        parse_batch_request(row["request_json"])
                        if table == "llm_batch_intents"
                        else None
                    )
                    meta = batch_item_meta(
                        request.call_kind if request is not None else row["call_kind"],
                        row["meta_json"],
                        submitted=request is None,
                    )
                except ValueError, TypeError, RecursionError:
                    continue
                if not isinstance(meta, DigestItemMeta) or meta.amendment_input_ref is None:
                    continue
                try:
                    _direct_receipt(meta)
                    validate_amendment_ref(
                        connection,
                        meta.amendment_input_ref,
                        request.call_kind if request is not None else row["call_kind"],
                        meta.request_sha256,
                        meta.input_binding,
                    )
                    if request is not None:
                        snapshot = read_amendment_input(
                            connection, meta.amendment_input_ref.snapshot_id
                        )
                        if snapshot is None or snapshot.question_json != canonical_json(
                            request.question.model_dump(mode="json")
                        ):
                            raise AmendmentInputError()
                except AmendmentInputError:
                    yield "invalid_amendment_input", _reference(table, row["custom_id"])
    except IndexError, KeyError, sqlite3.Error:
        yield "invalid_amendment_input", "amendment_input_snapshots"
    finally:
        cursor.close()


def validate_amendment_inputs(connection: sqlite3.Connection) -> None:
    if next(amendment_input_issues(connection), None) is not None:
        raise AmendmentInputError()


def validate_intent_input(connection: sqlite3.Connection, intent: BatchIntent) -> None:
    if not isinstance(intent.meta, DigestItemMeta) or intent.meta.amendment_input_ref is None:
        return
    reference = intent.meta.amendment_input_ref
    _direct_receipt(intent.meta)
    validate_amendment_ref(
        connection,
        reference,
        intent.request.call_kind,
        intent.request.payload_sha256,
        intent.meta.input_binding,
    )
    snapshot = read_amendment_input(connection, reference.snapshot_id)
    if snapshot is None or snapshot.question_json != canonical_json(
        intent.request.question.model_dump(mode="json")
    ):
        raise AmendmentInputError()


def validate_item_input(connection: sqlite3.Connection, item: LlmBatchItem) -> None:
    if isinstance(item.meta, DigestItemMeta):
        _direct_receipt(item.meta)
        validate_amendment_ref(
            connection,
            item.meta.amendment_input_ref,
            item.call_kind,
            item.meta.request_sha256,
            item.meta.input_binding,
        )


def validate_result_input(connection: sqlite3.Connection, result: ComputationResult) -> None:
    reference = result.amendment_input_ref
    validate_amendment_ref(
        connection, reference, result.purpose, result.request_sha256, result.input_binding
    )
    if reference is None:
        return
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        row = cursor.execute(
            "SELECT i.*,b.provider AS batch_provider FROM llm_batch_items i "
            "JOIN llm_batches b ON b.batch_id=i.batch_id "
            "WHERE i.batch_id=? AND i.custom_id=?",
            (result.batch_id, result.custom_id),
        ).fetchone()
        if row is not None:
            meta = batch_item_meta(row["call_kind"], row["meta_json"])
            if (
                row["batch_provider"] != result.provider
                or row["call_kind"] != "amendments"
                or not isinstance(meta, DigestItemMeta)
                or meta.amendment_input_ref != reference
                or meta.input_binding != result.input_binding
                or meta.request_sha256 != result.request_sha256
            ):
                raise AmendmentInputError()
            _direct_receipt(meta)
    except ValueError, TypeError, IndexError, KeyError, RecursionError, sqlite3.Error:
        raise AmendmentInputError() from None
    finally:
        cursor.close()


def checked_computation_result(
    connection: sqlite3.Connection, row: sqlite3.Row
) -> ComputationResult:
    result = read_computation_result(row)
    validate_result_input(connection, result)
    return result
