"""Validate technical claim receipts against the durable parents still available."""

import hashlib
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from typing import Literal

from lexinform.adapters.computation_history import read_computation_result
from lexinform.adapters.extraction_history import read_extraction_result
from lexinform.adapters.input_receipts import (
    read_reanalysis_baseline,
    validate_primary_receipt_parents,
)
from lexinform.models.analysis import AnalysisRecord
from lexinform.models.batch import BatchItemMeta, batch_item_meta
from lexinform.models.block_input import PreparedBlockCopy
from lexinform.models.claim_evidence import (
    PRIMARY_COMPARISON_RECEIPT_PROFILE,
    PrimaryClaimTechnicalBlock,
    PrimaryClaimTechnicalReceipt,
    PrimaryComparisonCitation,
    PrimaryComparisonTechnicalReceipt,
)
from lexinform.models.computations import ComputationResult
from lexinform.models.input_receipts import (
    MultipartTextInputReceipt,
    PrimaryInputReceipt,
    RclPairedArchiveInputReceipt,
)
from lexinform.models.reviews import ReviewDocument
from lexinform.primary_claim_input import multipart_claim_part_role, multipart_claim_span_role
from lexinform.primary_claims import validate_retained_claim_citation

MAX_TECHNICAL_CLAIM_RECEIPTS_PER_RESULT = 32


@dataclass(frozen=True)
class CheckedPrimaryClaimReceipt:
    receipt: PrimaryClaimTechnicalReceipt
    verification: Literal[
        "exact_retained_input",
        "submitted_input_unavailable",
        "checkpoint_required",
        "source_role_unverified",
    ]


ComparisonVerification = Literal[
    "exact_retained_input", "submitted_input_unavailable", "checkpoint_required"
]


@dataclass(frozen=True)
class CheckedPrimaryComparisonReceipt:
    receipt: PrimaryComparisonTechnicalReceipt
    before_verification: ComparisonVerification
    after_verification: ComparisonVerification


def primary_claim_block_copies(
    receipt: PrimaryInputReceipt | MultipartTextInputReceipt | None,
) -> tuple[PreparedBlockCopy, ...]:
    if isinstance(receipt, RclPairedArchiveInputReceipt):
        companion = receipt.paired_block_companion
        if companion is None:
            return ()
        return tuple(
            row
            for row in companion.rows
            if multipart_claim_part_role(receipt, row.part_index)
            == receipt.source_parts[row.part_index].role
        )
    return (
        receipt.block_companion.rows
        if receipt is not None and receipt.block_companion is not None
        else ()
    )


def retained_primary_claim_block(
    connection: sqlite3.Connection,
    receipt: PrimaryInputReceipt | MultipartTextInputReceipt | None,
    span: tuple[int, int],
    *,
    text: str | None = None,
) -> PrimaryClaimTechnicalBlock | None:
    if receipt is None:
        return None
    left, right = span
    copy = next(
        (
            row
            for row in primary_claim_block_copies(receipt)
            if row.start <= left < right <= row.end
        ),
        None,
    )
    if copy is None:
        return None
    if text is not None and hashlib.sha256(text[copy.start : copy.end].encode()).hexdigest() != (
        copy.text_sha256
    ):
        raise ValueError("Retained block copy differs from the exact submitted text")
    with closing(connection.cursor()) as cursor:
        cursor.row_factory = sqlite3.Row
        row = cursor.execute(
            "SELECT * FROM extraction_results WHERE extraction_id=?", (copy.extraction_id,)
        ).fetchone()
    if row is None:
        raise ValueError("Retained block extraction is unavailable")
    extraction = read_extraction_result(row)
    part = next(
        (item for item in extraction.selected_parts if item.physical_index == copy.physical_index),
        None,
    )
    witness = part.block_copy_witness if part is not None else None
    if (
        isinstance(receipt, RclPairedArchiveInputReceipt)
        and receipt.paired_block_companion is not None
    ):
        role = receipt.source_parts[copy.part_index].role
        if (
            role == "self_amendment"
            or receipt.paired_block_companion.base_bill_capture_id is not None
        ):
            member = (
                extraction.observed_member_block_copy
                if role == "self_amendment"
                else extraction.selected_bill_block_sample
            )
            witness = (
                member.witness
                if member is not None and member.physical_index == copy.physical_index
                else None
            )
    segment = next((s for s in receipt.segments if s.start <= copy.start < copy.end <= s.end), None)
    if (
        witness is None
        or witness.witness_id != copy.witness_id
        or copy.run_index >= len(witness.runs)
        or segment is None
        or segment.source_start is None
    ):
        raise ValueError("Retained claim block has no exact physical parent")
    run = witness.runs[copy.run_index]
    source_left = segment.source_start + left - segment.start
    source_right = source_left + right - left
    if not run.source_start <= source_left < source_right <= run.source_end:
        raise ValueError("Retained claim block differs from its exact source range")
    block_left = run.block_start + source_left - run.source_start
    return PrimaryClaimTechnicalBlock(
        prepared_copy=copy,
        block_id=run.block_id,
        block_text_sha256=run.block_text_sha256,
        block_span=(block_left, block_left + right - left),
    )


def _batch_meta(
    cursor: sqlite3.Cursor, result: ComputationResult, record: AnalysisRecord
) -> BatchItemMeta | None:
    row = cursor.execute(
        "SELECT i.*, b.provider FROM llm_batch_items i"
        " LEFT JOIN llm_batches b USING(batch_id) WHERE i.batch_id=? AND i.custom_id=?",
        (result.batch_id, result.custom_id),
    ).fetchone()
    if row is None:
        return None
    if (row["provider"], row["term"], row["number"], row["call_kind"]) != (
        result.provider,
        result.term,
        result.number,
        result.purpose,
    ):
        raise ValueError("technical claim receipt has a foreign batch item")
    meta = batch_item_meta(row["call_kind"], row["meta_json"])
    if not isinstance(meta, BatchItemMeta) or (
        meta.input_binding,
        meta.request_sha256,
        meta.generation,
        meta.input_chars,
    ) != (result.input_binding, result.request_sha256, result.generation, record.input_chars):
        raise ValueError("technical claim receipt has a foreign saved request")
    return meta


def _verify_receipt(
    connection: sqlite3.Connection,
    receipt: PrimaryClaimTechnicalReceipt,
    record: AnalysisRecord,
    meta: BatchItemMeta | None,
    owner: tuple[int, str],
) -> CheckedPrimaryClaimReceipt:
    receipt.evidence.require_claim(record.analysis)
    if receipt.submitted_span[1] > record.input_chars:
        raise ValueError("technical claim receipt escapes its saved request length")
    prepared = meta.prepared_input_receipt if meta is not None else None
    if prepared is None:
        if receipt.block is not None:
            raise ValueError("technical claim receipt has no retained block receipt")
        return CheckedPrimaryClaimReceipt(receipt, "submitted_input_unavailable")
    if (prepared.request_sha256, prepared.text_sha256, prepared.text_chars) != (
        receipt.request_sha256,
        receipt.submitted_text_sha256,
        record.input_chars,
    ):
        raise ValueError("technical claim receipt differs from its prepared input")
    assert meta is not None, "a prepared receipt comes from the bound batch item"
    validate_primary_receipt_parents(connection, meta, owner)
    text = meta.text or None
    if receipt.block is not None and receipt.block != retained_primary_claim_block(
        connection, prepared, receipt.submitted_span, text=text
    ):
        raise ValueError("technical claim receipt differs from its exact retained block")
    if text is None:
        return CheckedPrimaryClaimReceipt(receipt, "submitted_input_unavailable")
    if len(text) != prepared.text_chars or hashlib.sha256(text.encode()).hexdigest() != (
        receipt.submitted_text_sha256
    ):
        raise ValueError("technical claim receipt differs from its exact submitted text")
    document = ReviewDocument(
        identity="submitted-context",
        url="",
        role="project_text",
        text=text,
        sha256=receipt.submitted_text_sha256,
        normalized_sha256=receipt.submitted_text_sha256,
        complete=False,
    )
    checked = validate_retained_claim_citation(record, receipt.evidence, (document,))
    if checked.status != "quote_present" or checked.observed_text_span != receipt.submitted_span:
        raise ValueError("technical claim receipt differs from its exact literal citation")
    if (
        isinstance(prepared, MultipartTextInputReceipt)
        and multipart_claim_span_role(prepared, receipt.submitted_span) != "normative"
    ):
        return CheckedPrimaryClaimReceipt(receipt, "source_role_unverified")
    status: Literal["checkpoint_required", "exact_retained_input"] = (
        "checkpoint_required"
        if receipt.input_checkpoint_sha256 is not None
        else "exact_retained_input"
    )
    return CheckedPrimaryClaimReceipt(receipt, status)


def _verify_comparison_side(
    connection: sqlite3.Connection,
    side: PrimaryComparisonCitation,
    result: ComputationResult,
    meta: BatchItemMeta,
) -> ComparisonVerification:
    prepared = meta.prepared_input_receipt
    if (
        side.result_id != result.result_id
        or side.request_sha256 != result.request_sha256
        or prepared is None
        or (prepared.request_sha256, prepared.text_sha256, prepared.text_chars)
        != (side.request_sha256, side.submitted_text_sha256, meta.input_chars)
        or side.submitted_span[1] > prepared.text_chars
    ):
        raise ValueError("Comparison citation differs from its exact prepared input")
    validate_primary_receipt_parents(connection, meta, (result.term, result.number))
    if not meta.text:
        return "submitted_input_unavailable"
    left, right = side.submitted_span
    if (
        len(meta.text) != prepared.text_chars
        or hashlib.sha256(meta.text.encode()).hexdigest() != side.submitted_text_sha256
        or meta.text[left:right] != side.citation.quote
    ):
        raise ValueError("Comparison citation differs from its exact submitted text range")
    return "checkpoint_required" if side.input_checkpoint_sha256 else "exact_retained_input"


def _verify_comparison(
    connection: sqlite3.Connection,
    cursor: sqlite3.Cursor,
    receipt: PrimaryComparisonTechnicalReceipt,
    result: ComputationResult,
    record: AnalysisRecord,
    meta: BatchItemMeta | None,
) -> CheckedPrimaryComparisonReceipt:
    if (
        result.purpose != "reanalysis"
        or meta is None
        or meta.queued_at is None
        or meta.reanalysis_baseline is None
        or meta.reanalysis_baseline.result_id != receipt.before.result_id
    ):
        raise ValueError("Comparison receipt requires the recorded reanalysis baseline")
    previous = read_reanalysis_baseline(
        connection,
        meta.reanalysis_baseline,
        owner=(result.term, result.number),
        custom_id=result.custom_id,
        before=meta.queued_at,
    )
    previous_record = previous.record
    if (
        not isinstance(previous_record, AnalysisRecord)
        or previous.input_binding is None
        or (previous.input_binding.term, previous.input_binding.number)
        != (previous.term, previous.number)
    ):
        raise ValueError("Comparison receipt requires a bound baseline result")
    previous_meta = _batch_meta(cursor, previous, previous_record)
    if previous_meta is None:
        raise ValueError("Comparison receipt requires the baseline batch metadata")
    receipt.require_claim(record.analysis)
    return CheckedPrimaryComparisonReceipt(
        receipt,
        _verify_comparison_side(connection, receipt.before, previous, previous_meta),
        _verify_comparison_side(connection, receipt.after, result, meta),
    )


def read_primary_claim_receipts(
    connection: sqlite3.Connection, result_id: str
) -> tuple[CheckedPrimaryClaimReceipt | CheckedPrimaryComparisonReceipt, ...]:
    with closing(connection.cursor()) as cursor:
        cursor.row_factory = sqlite3.Row
        rows = cursor.execute(
            "SELECT receipt_id,result_id,receipt_json FROM primary_claim_technical_receipts"
            " WHERE result_id=? ORDER BY receipt_id LIMIT ?",
            (result_id, MAX_TECHNICAL_CLAIM_RECEIPTS_PER_RESULT + 1),
        ).fetchall()
        if not rows:
            return ()
        if len(rows) > MAX_TECHNICAL_CLAIM_RECEIPTS_PER_RESULT:
            raise ValueError("too many technical claim receipts for one result")
        parent = cursor.execute(
            "SELECT * FROM computation_results WHERE result_id=?", (result_id,)
        ).fetchone()
        if parent is None:
            raise ValueError("technical claim receipt requires its exact paid result")
        result = read_computation_result(parent)
        record = result.record
        if (
            not isinstance(record, AnalysisRecord)
            or result.validation != "validated"
            or result.request_sha256 is None
            or result.input_binding is None
            or (result.input_binding.term, result.input_binding.number)
            != (result.term, result.number)
        ):
            raise ValueError("technical claim receipt requires a bound primary result")
        meta = _batch_meta(cursor, result, record)
        owner = (result.term, result.number)
        checked: list[CheckedPrimaryClaimReceipt | CheckedPrimaryComparisonReceipt] = []
        for row in rows:
            if row["receipt_id"].startswith(PRIMARY_COMPARISON_RECEIPT_PROFILE + ":"):
                comparison = PrimaryComparisonTechnicalReceipt.read(row["receipt_json"])
                if comparison.receipt_id != row["receipt_id"]:
                    raise ValueError("Comparison receipt identity mismatch")
                checked.append(
                    _verify_comparison(connection, cursor, comparison, result, record, meta)
                )
                continue
            receipt = PrimaryClaimTechnicalReceipt.read(row["receipt_json"])
            if (receipt.receipt_id, receipt.result_id, receipt.request_sha256) != (
                row["receipt_id"],
                result.result_id,
                result.request_sha256,
            ):
                raise ValueError("technical claim receipt identity or request mismatch")
            checked.append(_verify_receipt(connection, receipt, record, meta, owner))
        return tuple(checked)


def validate_primary_claim_receipts(connection: sqlite3.Connection) -> None:
    for row in connection.execute(
        "SELECT DISTINCT result_id FROM primary_claim_technical_receipts ORDER BY result_id"
    ):
        read_primary_claim_receipts(connection, row[0])
