"""Verify a technical literal claim against a pinned, durable multipart primary intent."""

import argparse
import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from lexinform.adapters.content_source_refs import ContentSourceRows
from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.input_receipts import (
    intent_metadata,
    parse_batch_request,
    validate_multipart_intent,
)
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.models import BatchIntent, EvidenceCitation, ReviewDocument
from lexinform.models.batch import AnalysisQuestion, BatchItemMeta
from lexinform.models.input_receipts import RclArchiveInputReceipt, SejmSelfAmendmentInputReceipt
from lexinform.models.physical_inventory import Sha256
from lexinform.primary_claim_input import _validate_key_change_input
from lexinform.primary_claims import KeyChangeEvidence
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_runtime import CorpusReads, code_version, source_fingerprints
from tools.replay_primary_claims import technical_answer
from tools.replay_proposal_edition import PinnedInput
from tools.sqlite_snapshot import open_snapshot

_MAX_BYTES = 32_000_000


class MultipartCitationCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["primary-multipart-citation-v1"] = "primary-multipart-citation-v1"
    custom_id: str = Field(min_length=1, max_length=256)
    request_sha256: Sha256
    original: PinnedInput
    source_part_index: int = Field(default=0, ge=0, le=1, strict=True)
    claim: str = Field(min_length=1, max_length=256)
    locator: str = Field(min_length=1, max_length=128)
    quote_start: int = Field(ge=0, strict=True)
    quote_end: int = Field(gt=0, strict=True)
    quote_sha256: Sha256


def _intent(db: sqlite3.Connection, case: MultipartCitationCase) -> BatchIntent:
    row = db.execute(
        "SELECT * FROM llm_batch_intents WHERE custom_id=?", (case.custom_id,)
    ).fetchone()
    if row is None:
        raise ValueError("The exact durable original intent is unavailable")
    request = parse_batch_request(row["request_json"])
    request.verify_payload()
    if (request.custom_id, request.payload_sha256) != (case.custom_id, case.request_sha256):
        raise ValueError("The durable original request differs from its pin")
    return BatchIntent(
        request=request,
        meta=intent_metadata(request, row["meta_json"]),
        provider=row["provider"],
        state=row["state"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _verify(
    db: sqlite3.Connection, case: MultipartCitationCase, data: bytes
) -> tuple[dict[str, object], bool]:
    intent = _intent(db, case)
    question, meta = intent.request.question, intent.meta
    receipt = meta.prepared_input_receipt
    if (
        not isinstance(question, AnalysisQuestion)
        or not isinstance(meta, BatchItemMeta)
        or not isinstance(receipt, (RclArchiveInputReceipt, SejmSelfAmendmentInputReceipt))
    ):
        raise ValueError("The exact original multipart profile is unavailable")
    validate_multipart_intent(db, intent)
    selected = ContentSourceRows(db).get_document_selection(receipt.source_selection_id)
    if selected is None:
        raise ValueError("The verified original selection is unavailable")
    part_index = case.source_part_index
    if part_index == 1 and not isinstance(receipt, SejmSelfAmendmentInputReceipt):
        raise ValueError("Only the verified Sejm self-amendment has a non-main citation profile")
    part = selected.selection.parts[part_index]
    role = "self_amendment" if part_index == 1 else "bill"
    if (
        part.role != role
        or part.source_sha256 != case.original.sha256
        or digest(data) != part.source_sha256
        or len(data) > _MAX_BYTES
    ):
        raise ValueError("Pinned source body differs from the selected artifact")
    reader = DocumentTextExtractor(
        PypdfTextExtractor(),
        DocxTextExtractor(),
        DocTextExtractor(),
        max_member_bytes=_MAX_BYTES,
        max_part_chars=400_000,
    )
    extracted = reader.extract_document(data)
    candidates = [
        member
        for member in extracted.selection.parts
        if (member.members, member.sha256, member.text_sha256)
        == (part.members, part.sha256, part.text_sha256)
    ]
    if len(candidates) != 1:
        raise ValueError("The original body does not yield the exact selected source text")
    text = candidates[0].text
    quote = text[case.quote_start : case.quote_end]
    if (
        not 0 <= case.quote_start < case.quote_end <= len(text)
        or len(quote) > 1200
        or digest(quote.encode()) != case.quote_sha256
    ):
        raise ValueError("The selected literal quote differs from its pin")
    answer = technical_answer(text)
    record = answer.model_copy(
        update={
            "analysis": answer.analysis.model_copy(update={"key_changes": [case.claim]}),
            "request_sha256": case.request_sha256,
            "source_kind": question.ctx.source_kind,
            "source_url": meta.source_url,
            "input_chars": len(question.ctx.text),
        }
    )
    document = ReviewDocument(
        identity=part.url,
        url=part.url,
        role="project_text",
        text=text,
        sha256=part.text_sha256,
        normalized_sha256=part.text_sha256,
        file_sha256=part.sha256,
        complete=False,
    )
    evidence = KeyChangeEvidence(
        0,
        digest(case.claim.encode()),
        part.text_sha256,
        EvidenceCitation(document_id=part.url, locator=case.locator, quote=quote),
    )
    checked = _validate_key_change_input(
        record,
        evidence,
        (document,),
        intent=intent,
        verified_multipart_selection=selected,
        multipart_part_index=part_index,
    )
    return {
        "profile": receipt.profile,
        **({"source_part_index": part_index} if part_index else {}),
        "request_sha256": case.request_sha256,
        "source_selection_id": receipt.source_selection_id,
        "source_observation_id": receipt.source_observation_id,
        "artifact_sha256": part.source_sha256,
        "member_sha256": part.sha256,
        "source_text_sha256": part.text_sha256,
        "quote_sha256": case.quote_sha256,
        "claim_text_sha256": evidence.claim_text_sha256,
        "check": asdict(checked),
    }, checked.status == "retained"


def replay(
    database: Path, database_sha256: str, corpus: Path, case: MultipartCitationCase
) -> dict[str, object]:
    root = corpus.resolve(strict=True)
    reads, before = CorpusReads(root), source_fingerprints()
    with open_snapshot(database) as snapshot:
        if snapshot.sha256 != database_sha256:
            raise ValueError("Pinned original intent snapshot SHA differs")
        with reads.guard():
            result, retained = _verify(snapshot.connection, case, case.original.read(root))
        if not snapshot.unchanged():
            raise ValueError("Original intent snapshot changed during the audit")
    if reads.changed_inputs() or before != source_fingerprints():
        raise ValueError("Replay inputs or code changed")
    return {
        "version": case.version,
        "status": "recorded" if retained else "not_observed",
        "scope": "Literal text in a durable offline prepared context.",
        "database_sha256": database_sha256,
        "proof": result,
        "read_inputs": reads.initial_sha256,
        "code_version": code_version(),
        "source_pins_sha256": digest(json.dumps(before, sort_keys=True).encode()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--database-sha256", required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--case-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        output, database = args.output.resolve(), args.database.resolve()
        protected = {
            database,
            *(Path(str(database) + suffix) for suffix in ("-wal", "-shm", "-journal")),
        }
        if output in protected or output.is_relative_to(args.corpus.resolve()):
            raise ValueError("Replay output must be outside the corpus and snapshot files")
        raw = read_artifact(args.case)
        if digest(raw) != args.case_sha256:
            raise ValueError("Replay case SHA differs")
        result = replay(
            database,
            args.database_sha256,
            args.corpus,
            MultipartCitationCase.model_validate_json(raw),
        )
        write_json(args.output, {**result, "case_sha256": args.case_sha256})
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        parser.error(str(exc))
    raise SystemExit(0 if result["status"] == "recorded" else 2)


if __name__ == "__main__":
    main()
