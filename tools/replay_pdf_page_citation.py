"""Verify a literal primary citation against one independently pinned PDF page block."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.models import AnalysisRecord, EvidenceCitation, PdfTextExtraction, ReviewDocument
from lexinform.models.physical_inventory import Sha256
from lexinform.pdf_projection import legacy_pdf_text, raw_pdf_page_span
from lexinform.primary_claims import KeyChangeEvidence, validate_key_change_evidence
from lexinform.sections import PAGE_BREAK
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_runtime import CorpusReads, code_version, source_fingerprints
from tools.replay_primary_claims import technical_answer
from tools.replay_proposal_edition import PinnedInput

_MAX_PDF_BYTES = 32_000_000


class RawPageSpanPin(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    start: int = Field(ge=0, strict=True)
    end: int = Field(gt=0, strict=True)
    sha256: Sha256


class PdfCitationCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["primary-pdf-page-citation-v1"] = "primary-pdf-page-citation-v1"
    pdf: PinnedInput
    extraction_sha256: Sha256
    source_text_sha256: Sha256
    document_id: str = Field(min_length=1, max_length=256)
    locator: str = Field(min_length=1, max_length=128)
    claim: str = Field(min_length=1, max_length=256)
    quote_sha256: Sha256
    quote_start: int = Field(ge=0, strict=True)
    quote_end: int = Field(gt=0, strict=True)
    page: int = Field(gt=0, strict=True)
    raw_page_span: RawPageSpanPin | None = None


def verify_pdf_page(
    record: AnalysisRecord,
    evidence: KeyChangeEvidence,
    document: ReviewDocument,
    extraction: PdfTextExtraction,
    *,
    artifact_sha256: str,
    extraction_sha256: str,
    expected_page: int,
    raw_page_span: RawPageSpanPin | None = None,
) -> dict[str, object]:
    checked = validate_key_change_evidence(record, evidence, (document,))
    if checked.observed_text_span is None:
        raise ValueError(f"PDF page requires a unique literal citation: {checked.status}")
    if (
        document.file_sha256 != artifact_sha256
        or extraction.input_sha256 != artifact_sha256
        or extraction.manifest_sha256 != extraction_sha256
        or extraction.legacy_text != document.text
        or extraction.error_type is not None
        or not extraction.pages
        or any(page.error_type is not None or PAGE_BREAK in page.text for page in extraction.pages)
    ):
        raise ValueError("Pinned PDF artifact, extraction or literal source differs")

    chunks = document.text.split(PAGE_BREAK)
    if len(chunks) != len(extraction.pages):
        raise ValueError("Legacy PDF text collapsed physical page boundaries")
    start, end = checked.observed_text_span
    offset = 0
    found: dict[str, object] | None = None
    for page, chunk in zip(extraction.pages, chunks, strict=True):
        block = legacy_pdf_text((page.text,))
        if block != chunk.strip():
            raise ValueError("Legacy PDF chunk differs from its exact normalized page")
        content_start = offset + len(chunk) - len(chunk.lstrip())
        if content_start <= start < end <= content_start + len(block):
            if page.number != expected_page:
                raise ValueError("Citation does not belong to the expected physical page")
            local = start - content_start, end - content_start
            if block[local[0] : local[1]] != evidence.citation.quote:
                raise ValueError("Literal quote differs inside its normalized page block")
            found = {
                "page": page.number,
                "block_id": page.block_id,
                "raw_page_text_sha256": page.text_sha256,
                "normalized_page_text_sha256": digest(block.encode()),
                "normalized_page_span": list(local),
            }
            if raw_page_span is not None:
                raw_span = raw_pdf_page_span(page.text, *local)
                if raw_span != (raw_page_span.start, raw_page_span.end):
                    raise ValueError("Raw PDF page span is ambiguous or differs from its pin")
                raw_hash = digest(page.text[raw_span[0] : raw_span[1]].encode())
                if raw_hash != raw_page_span.sha256:
                    raise ValueError("Raw PDF page span hash differs from its pin")
                found["raw_page_span"] = {
                    "coordinates": "pdf-extractor-page-python-string",
                    "span": list(raw_span),
                    "text_sha256": raw_hash,
                }
        offset += len(chunk) + len(PAGE_BREAK)
    if found is None:
        raise ValueError("Citation crosses a physical page boundary")
    return {
        **found,
        "coordinates": "legacy-pdf-single-page-python-string",
        "artifact_sha256": artifact_sha256,
        "extraction_sha256": extraction_sha256,
        "source_text_sha256": document.sha256,
        "quote_sha256": digest(evidence.citation.quote.encode()),
        "claim_text_sha256": evidence.claim_text_sha256,
        "evidence": asdict(checked),
    }


def replay(corpus: Path, case: PdfCitationCase) -> dict[str, object]:
    root = corpus.resolve(strict=True)
    reads, before = CorpusReads(root), source_fingerprints()
    with reads.guard():
        data = case.pdf.read(root)
        if len(data) > _MAX_PDF_BYTES or not data.startswith(b"%PDF-"):
            raise ValueError("Pinned input must be a bounded PDF")
        extracted = PypdfTextExtractor().extract_pages(data)
        text = extracted.legacy_text
        quote = text[case.quote_start : case.quote_end]
        if (
            not 0 <= case.quote_start < case.quote_end <= len(text)
            or len(quote) > 1200
            or digest(text.encode()) != case.source_text_sha256
            or digest(quote.encode()) != case.quote_sha256
        ):
            raise ValueError("Pinned legacy text or literal citation differs")
        record = technical_answer(text)
        answer = record.analysis.model_copy(update={"key_changes": [case.claim]})
        record = record.model_copy(update={"analysis": answer})
        document = ReviewDocument(
            identity=case.document_id,
            url="",
            role="project_text",
            text=text,
            sha256=case.source_text_sha256,
            normalized_sha256=case.source_text_sha256,
            file_sha256=case.pdf.sha256,
            complete=False,
        )
        evidence = KeyChangeEvidence(
            0,
            digest(record.analysis.key_changes[0].encode()),
            case.source_text_sha256,
            EvidenceCitation(document_id=case.document_id, locator=case.locator, quote=quote),
        )
        proof = verify_pdf_page(
            record,
            evidence,
            document,
            extracted,
            artifact_sha256=case.pdf.sha256,
            extraction_sha256=case.extraction_sha256,
            expected_page=case.page,
            raw_page_span=case.raw_page_span,
        )
    if reads.changed_inputs() or before != source_fingerprints():
        raise ValueError("Replay inputs or code changed")
    return {
        "version": case.version,
        "status": "recorded",
        "scope": "Technical literal citation in one normalized PDF page text block.",
        "proof": proof,
        "read_inputs": reads.initial_sha256,
        "code_version": code_version(),
        "source_pins_sha256": digest(json.dumps(before, sort_keys=True).encode()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--case-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.output.resolve().is_relative_to(args.corpus.resolve()):
            raise ValueError("Replay output must be outside the corpus")
        raw = read_artifact(args.case)
        if digest(raw) != args.case_sha256:
            raise ValueError("Replay case SHA-256 differs")
        result = replay(args.corpus, PdfCitationCase.model_validate_json(raw))
        write_json(args.output, {**result, "case_sha256": args.case_sha256})
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
