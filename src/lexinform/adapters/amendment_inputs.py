"""Validate immutable amendment inputs and request-bound references with original lineage."""

import hashlib
import sqlite3
from collections.abc import Iterator

from lexinform.adapters.computation_history import read_computation_result
from lexinform.adapters.input_receipts import _object, _parents, _reference, parse_batch_request
from lexinform.json_storage import canonical_json, read_json
from lexinform.models.amendment_inputs import (
    AmendmentInputRef,
    AmendmentInputSnapshot,
    AmendmentScanInputWitness,
)
from lexinform.models.analysis import AmendmentsContext, AmendmentsRecord
from lexinform.models.batch import (
    AmendmentsQuestion,
    BatchIntent,
    BatchItemMeta,
    BatchRequest,
    DigestItemMeta,
    LlmBatchItem,
    batch_item_meta,
)
from lexinform.models.computation_inputs import ComputationInputBinding
from lexinform.models.computations import ComputationResult
from lexinform.models.input_receipts import amendment_context_sha256
from lexinform.senate_documents import validate_senate_scan


class AmendmentInputError(ValueError):
    def __init__(self) -> None:
        super().__init__("invalid_amendment_input")


def _checked_text(digest: object, text: object) -> str:
    if not isinstance(text, str) or hashlib.sha256(text.encode()).hexdigest() != digest:
        raise AmendmentInputError()
    return text


def _load_amendment_scan_context(
    connection: sqlite3.Connection, witness: AmendmentScanInputWitness
) -> tuple[str, AmendmentsContext]:
    digest = witness.context_sha256
    if not witness.external_context or digest is None:
        raise AmendmentInputError()
    row = connection.execute(
        "SELECT text FROM amendment_input_texts WHERE text_sha256=?", (digest,)
    ).fetchone()
    if row is None or not isinstance(row[0], str):
        raise AmendmentInputError()
    context_json = row[0]
    try:
        return context_json, witness.resolve_context(context_json)
    except ValueError, TypeError, RecursionError:
        raise AmendmentInputError() from None


def read_amendment_scan_context(
    connection: sqlite3.Connection, witness: AmendmentScanInputWitness
) -> str:
    return _load_amendment_scan_context(connection, witness)[0]


def write_amendment_scan_context(
    connection: sqlite3.Connection, witness: AmendmentScanInputWitness, context_json: str
) -> None:
    if (
        not connection.in_transaction
        or not witness.external_context
        or witness.context_sha256 is None
    ):
        raise AmendmentInputError()
    connection.execute(
        "INSERT INTO amendment_input_texts VALUES (?,?) ON CONFLICT DO NOTHING",
        (witness.context_sha256, context_json),
    )
    if read_amendment_scan_context(connection, witness) != context_json:
        raise AmendmentInputError()


def _validate_scan_context(
    connection: sqlite3.Connection, meta: DigestItemMeta, number: str
) -> None:
    witness = meta.amendment_scan_input
    if witness is None or not witness.external_context:
        return
    _, context = _load_amendment_scan_context(connection, witness)
    if context.number != number or context.source_check != meta.amendment_source:
        raise AmendmentInputError()


def _shared_question(cursor: sqlite3.Cursor, manifest: str, text_sha256: str) -> str:
    body = cursor.execute(
        "SELECT text FROM amendment_input_texts WHERE text_sha256=?", (text_sha256,)
    ).fetchone()
    if body is None:
        raise AmendmentInputError()
    question = read_json(manifest)
    if not isinstance(question, dict) or canonical_json(question) != manifest:
        raise AmendmentInputError()
    context = question.get("ctx")
    if not isinstance(context, dict) or "text" in context:
        raise AmendmentInputError()
    context["text"] = _checked_text(text_sha256, body["text"])
    return canonical_json(question)


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
        question_json = row["question_json"]
        if not isinstance(question_json, str):
            raise AmendmentInputError()
        columns = row.keys()
        text_sha256 = row["text_sha256"] if "text_sha256" in columns else None
        if text_sha256 is not None:
            if not isinstance(text_sha256, str):
                raise AmendmentInputError()
            question_json = _shared_question(cursor, question_json, text_sha256)
        snapshot = AmendmentInputSnapshot(profile=row["profile"], question_json=question_json)
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
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if "amendment_input_snapshots" not in tables and version >= 67:
            yield "invalid_amendment_input", "amendment_input_snapshots"
            return
        if version >= 68:
            if "amendment_input_texts" not in tables:
                yield "invalid_amendment_input", "amendment_input_texts"
                return
            columns = {
                row[1] for row in cursor.execute("PRAGMA table_info(amendment_input_snapshots)")
            }
            if "text_sha256" not in columns:
                yield "invalid_amendment_input", "amendment_input_snapshots"
                return
        if "amendment_input_texts" in tables:
            for row in cursor.execute("SELECT text_sha256, text FROM amendment_input_texts"):
                try:
                    _checked_text(row["text_sha256"], row["text"])
                except AmendmentInputError:
                    yield (
                        "invalid_amendment_input",
                        _reference("amendment_input_texts", row["text_sha256"]),
                    )
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
                raw = _object(row["result_json"])
                if raw is not None and "amendment_scan_input" in raw:
                    yield (
                        "invalid_amendment_input",
                        _reference("computation_results", row["result_id"]),
                    )
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
                    raw = _object(row["meta_json"])
                    if raw is not None and "amendment_scan_input" in raw:
                        yield "invalid_amendment_input", _reference(table, row["custom_id"])
                    continue
                if not isinstance(meta, DigestItemMeta):
                    continue
                if meta.amendment_scan_input is not None:
                    try:
                        meta.require_amendment_scan_carrier(
                            row["call_kind"], row["term"], row["number"]
                        )
                        _validate_scan_context(connection, meta, row["number"])
                    except ValueError:
                        yield "invalid_amendment_input", _reference(table, row["custom_id"])
                if meta.amendment_input_ref is None:
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


def write_shared_amendment_input(
    connection: sqlite3.Connection, snapshot: AmendmentInputSnapshot
) -> None:
    snapshot = AmendmentInputSnapshot.model_validate(snapshot.model_dump())
    previous = read_amendment_input(connection, snapshot.snapshot_id)
    if previous is not None:
        if previous != snapshot:
            raise AmendmentInputError()
        return
    question = read_json(snapshot.question_json)
    assert isinstance(question, dict), "the snapshot validator requires a question object"
    context = question["ctx"]
    assert isinstance(context, dict), "the snapshot validator requires a context object"
    text = context.pop("text")
    assert isinstance(text, str), "the snapshot validator requires exact amendment text"
    digest = hashlib.sha256(text.encode()).hexdigest()
    connection.execute(
        "INSERT INTO amendment_input_texts VALUES (?,?) ON CONFLICT DO NOTHING", (digest, text)
    )
    connection.execute(
        "INSERT INTO amendment_input_snapshots"
        " (snapshot_id,profile,question_sha256,question_json,text_sha256) VALUES (?,?,?,?,?)",
        (
            snapshot.snapshot_id,
            snapshot.profile,
            snapshot.question_sha256,
            canonical_json(question),
            digest,
        ),
    )
    if read_amendment_input(connection, snapshot.snapshot_id) != snapshot:
        raise AmendmentInputError()


def prune_amendment_inputs(connection: sqlite3.Connection) -> dict[str, int]:
    if (
        connection.execute(
            "SELECT 1 FROM amendment_input_snapshots"
            " UNION ALL SELECT 1 FROM amendment_input_texts LIMIT 1"
        ).fetchone()
        is None
    ):
        return {}
    validate_amendment_inputs(connection)
    failures = connection.execute(
        """
        SELECT r.*, i.meta_json FROM llm_batch_items i
        JOIN llm_batches batch USING (batch_id)
        JOIN computation_results r ON r.provider=batch.provider
          AND r.batch_id=i.batch_id AND r.custom_id=i.custom_id
          AND r.term=i.term AND r.number=i.number
        JOIN bills b ON b.term=i.term AND b.number=i.number
        WHERE i.call_kind='amendments' AND i.consumed_at IS NOT NULL
          AND i.accounted_at IS NOT NULL AND batch.forgotten_at IS NOT NULL
          AND (
            json_type(i.meta_json, '$.amendment_input_ref')='object'
            OR json_extract(i.meta_json, '$.amendment_scan_input.version') IN (3,4)
          )
          AND json_extract(r.result_json, '$.validation')='error'
          AND b.status IN ('analyzed', 'linked', 'skipped_prefilter',
                           'skipped_text_prefilter', 'skipped_closed')
          AND b.awaiting_batch_since IS NULL AND b.ready_analysis_json IS NULL
          AND b.review_work_json IS NULL
          AND NOT EXISTS (SELECT 1 FROM document_work w
            WHERE w.term=i.term AND w.number=i.number AND w.state != 'complete')
          AND NOT EXISTS (SELECT 1 FROM publications p
            WHERE p.term=i.term AND p.number=i.number AND p.status NOT IN ('sent', 'skipped'))
        """
    ).fetchall()
    retired = 0
    for row in failures:
        result = read_computation_result(row)
        meta = batch_item_meta("amendments", row["meta_json"], submitted=True)
        if (
            result.purpose != "amendments"
            or result.validation != "error"
            or result.amendment_input_ref is not None
            or result.amendment_scan_input is not None
            or not isinstance(meta, DigestItemMeta)
            or result.input_binding != meta.input_binding
            or result.request_sha256 != meta.request_sha256
        ):
            continue
        retired += connection.execute(
            "UPDATE llm_batch_items SET meta_json=json_remove("
            "meta_json,'$.amendment_input_ref','$.amendment_scan_input')"
            " WHERE batch_id=? AND custom_id=?",
            (result.batch_id, result.custom_id),
        ).rowcount
    snapshots = connection.execute(
        """
        DELETE FROM amendment_input_snapshots
        WHERE snapshot_id NOT IN (
          WITH refs(snapshot_id) AS (
            SELECT json_extract(meta_json,'$.amendment_input_ref.snapshot_id')
            FROM llm_batch_intents
            UNION ALL
            SELECT json_extract(meta_json,'$.amendment_input_ref.snapshot_id')
            FROM llm_batch_items
            UNION ALL
            SELECT json_extract(result_json,'$.amendment_input_ref.snapshot_id')
            FROM computation_results
          )
          SELECT snapshot_id FROM refs WHERE snapshot_id IS NOT NULL
        )
        """
    ).rowcount
    texts = connection.execute(
        """
        DELETE FROM amendment_input_texts WHERE text_sha256 NOT IN (
          WITH refs(digest) AS (
            SELECT text_sha256 FROM amendment_input_snapshots
            UNION ALL
            SELECT json_extract(meta_json,'$.amendment_scan_input.context_sha256')
            FROM llm_batch_items
            UNION ALL
            SELECT json_extract(result_json,'$.amendment_scan_input.context_sha256')
            FROM computation_results
          )
          SELECT digest FROM refs WHERE digest IS NOT NULL
        )
        """
    ).rowcount
    return {
        "amendment_item_refs": retired,
        "amendment_input_snapshots": snapshots,
        "amendment_input_texts": texts,
    }


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


def capture_submitted_amendment_scan_input(
    request: BatchRequest, item: LlmBatchItem, queued: BatchItemMeta | DigestItemMeta
) -> tuple[AmendmentScanInputWitness, str | None] | None:
    question, meta = request.question, item.meta
    if (
        not isinstance(question, AmendmentsQuestion)
        or question.ctx.source_kind not in ("committee_amendments", "senate_amendments")
        or not isinstance(meta, DigestItemMeta)
        or not isinstance(queued, DigestItemMeta)
        or meta != queued.model_copy(update={"request_sha256": request.payload_sha256})
        or meta.amendment_scan_input is not None
        or meta.source_url is None
        or request.payload_sha256 is None
    ):
        return None
    scan = question.ctx.scan
    if scan is None or scan.scan_input is None:
        return None
    try:
        context_json = canonical_json(question.ctx.model_dump(mode="json", exclude={"scan"}))
        question_sha256 = hashlib.sha256(
            canonical_json(question.model_dump(mode="json")).encode()
        ).hexdigest()
        version = 2 if question.ctx.source_kind == "senate_amendments" else 1
        retained_json = None
        try:
            witness = AmendmentScanInputWitness(
                version=version,
                source_url=meta.source_url,
                request_sha256=request.payload_sha256,
                question_sha256=question_sha256,
                context_json=context_json,
                scan_input=scan.scan_input,
            )
        except ValueError:
            if (
                version != 2
                or question.ctx.number != item.number
                or question.ctx.source_check != meta.amendment_source
            ):
                return None
            witness = AmendmentScanInputWitness(
                version=3,
                source_url=meta.source_url,
                request_sha256=request.payload_sha256,
                question_sha256=question_sha256,
                context_sha256=hashlib.sha256(context_json.encode()).hexdigest(),
                scan_input=scan.scan_input,
            )
            if witness.restore_context(scan, retained_json=context_json) != question.ctx:
                return None
            retained_json = context_json
        candidate = meta.model_copy(update={"amendment_scan_input": witness})
        candidate.require_amendment_scan_carrier(item.call_kind, item.term, item.number)
    except ValueError, TypeError, RecursionError:
        return None
    return witness, retained_json


def validate_item_input(connection: sqlite3.Connection, item: LlmBatchItem) -> None:
    if isinstance(item.meta, DigestItemMeta):
        item.meta.require_amendment_scan_carrier(item.call_kind, item.term, item.number)
        _validate_scan_context(connection, item.meta, item.number)
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
    witness = result.amendment_scan_input
    if reference is None and witness is None:
        return
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        context = None
        if witness is not None and witness.external_context:
            _, context = _load_amendment_scan_context(connection, witness)
            if context.number != result.number or not isinstance(result.record, AmendmentsRecord):
                raise AmendmentInputError()
            if context.source_check is not None:
                validate_senate_scan(result.record.amendments.source_evidence, context.source_check)
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
                or (witness is not None and meta.amendment_scan_input != witness)
                or meta.input_binding != result.input_binding
                or meta.request_sha256 != result.request_sha256
            ):
                raise AmendmentInputError()
            if context is not None and context.source_check != meta.amendment_source:
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
