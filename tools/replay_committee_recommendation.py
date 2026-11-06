"""Prove one Sejm committee report recommends action on a named Senate resolution offline."""

import argparse
import gzip
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.adapters.sejm_api import parse_print, parse_process_detail
from lexinform.committee_recommendation import (
    official_recommendation_stages,
    report_evidence,
)
from lexinform.models import TextDocument
from lexinform.senate_documents import inspect_senate_resolution, senate_subject, senate_subject_key
from lexinform.services.documents import TextLoader
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_runtime import CorpusReads, code_version, source_fingerprints
from tools.replay_proposal_edition import PinnedInput, official_print

_PRINTS = TypeAdapter(list[dict[str, Any]])
_PROCESSES = TypeAdapter(dict[str, dict[str, Any]])


class RecommendationCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["sejm-committee-recommendation-v1"] = "sejm-committee-recommendation-v1"
    term: int = Field(gt=0, le=99, strict=True)
    process: str = Field(pattern=r"^[1-9][0-9]{0,8}$")
    report: str = Field(pattern=r"^[1-9][0-9]{0,8}(?:-A)?$")
    resolution: str = Field(pattern=r"^[1-9][0-9]{0,8}$")
    resolution_attachment: str = Field(pattern=r"^[^/\\]+\.docx$")
    official_prints: PinnedInput
    official_processes_gzip: PinnedInput
    report_pdf: PinnedInput
    resolution_docx: PinnedInput


def prove(
    case: RecommendationCase,
    process: dict[str, Any],
    listing: list[dict[str, Any]],
    report_pdf: bytes,
    resolution_docx: bytes,
) -> dict[str, object]:
    numbers = (case.process, case.report, case.resolution)
    if len(set(numbers)) != 3 or (process.get("term"), process.get("number")) != (
        case.term,
        case.process,
    ):
        raise ValueError("Expected three distinct prints and the exact official process")
    details = parse_process_detail(process)
    report, resolution = (
        official_print(listing, term=case.term, number=number) for number in numbers[1:]
    )
    if any(
        type(row["term"]) is not int or row.get("processPrint") != [case.process]
        for row in (report, resolution)
    ):
        raise ValueError("Expected exact singleton official process links")
    report_info, resolution_info = (
        parse_print(row, term=case.term) for row in (report, resolution)
    )
    report_attachment = report_info.main_pdf
    resolution_attachments = [
        item for item in resolution_info.attachments if item.name == case.resolution_attachment
    ]
    if (
        report_attachment is None
        or len(report_info.pdf_candidates) != 1
        or report_attachment.name != f"{case.report}.pdf"
        or len(resolution_attachments) != 1
        or not report_pdf.startswith(b"%PDF-")
        or not resolution_docx.startswith(b"PK")
        or digest(report_pdf) != case.report_pdf.sha256
        or digest(resolution_docx) != case.resolution_docx.sha256
    ):
        raise ValueError("Expected distinct pinned report PDF and resolution DOCX attachments")
    stage, target = official_recommendation_stages(
        details.stages,
        report=case.report,
        resolution=case.resolution,
        report_url=report_attachment.url,
    )

    def report_body(url: str, *, max_bytes: int | None = None) -> bytes:
        if url != stage.report_file or (max_bytes is not None and len(report_pdf) > max_bytes):
            raise ValueError("Unexpected report read")
        return report_pdf

    extractor = DocumentTextExtractor(PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor())
    loader = TextLoader({"api.sejm.gov.pl": report_body}, extractor, max_bytes=len(report_pdf))
    _, loaded = loader.select_document(
        TextDocument(url=report_attachment.url, kind="committee_amendments")
    )
    if loaded.first_pdf_page is None:
        raise ValueError("Report heading has no readable bounded first page")
    evidence = report_evidence(loaded.first_pdf_page.text, case.report, case.resolution)
    word = DocxTextExtractor().extract_blocks(resolution_docx)
    if word.body_state != "read":
        raise ValueError("Resolution DOCX has no readable body")
    inspected = inspect_senate_resolution(word.legacy_text, word_eof=True)
    subject = senate_subject(resolution_info.title)
    if (
        not inspected.complete
        or inspected.outcome != "amendments"
        or inspected.adopted_on != target.date
        or not subject
        or senate_subject_key(inspected.subject) != senate_subject_key(subject)
    ):
        raise ValueError("Target DOCX does not confirm the official amending Senate resolution")
    return {
        "relation": "recommendation_for",
        "term": case.term,
        "owner_print": case.process,
        "report_print": case.report,
        "report_url": report_attachment.url,
        "report_sha256": loaded.sha256,
        "report_stage": stage.model_dump(mode="json"),
        "report_evidence": evidence,
        "target_kind": "senate_amendment_resolution",
        "target_print": case.resolution,
        "target_url": resolution_attachments[0].url,
        "target_sha256": word.input_sha256,
        "target_stage": target.model_dump(mode="json"),
        "adoption_outcome": "not_asserted",
    }


def replay(corpus: Path, collection: Path, case: RecommendationCase) -> dict[str, object]:
    corpus = corpus.resolve(strict=True)
    collection = collection.resolve(strict=True)
    original, reports = CorpusReads(corpus), CorpusReads(collection)
    before = source_fingerprints()
    with reports.guard():
        report_pdf = case.report_pdf.read(collection)
    with original.guard():
        listing = _PRINTS.validate_json(case.official_prints.read(corpus))
        processes = _PROCESSES.validate_json(
            gzip.decompress(case.official_processes_gzip.read(corpus))
        )
        target = case.resolution_docx.read(corpus)
        proof = prove(case, processes[case.process], listing, report_pdf, target)
    if original.changed_inputs() or reports.changed_inputs() or before != source_fingerprints():
        raise ValueError("Replay inputs or code changed")
    return {
        "version": case.version,
        "status": "recorded",
        "proof": proof,
        "corpus_inputs": original.initial_sha256,
        "collection_inputs": reports.initial_sha256,
        "code_version": code_version(),
        "source_pins_sha256": digest(json.dumps(before, sort_keys=True).encode()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--collection", type=Path, required=True)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--case-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if any(
            args.output.resolve().is_relative_to(root.resolve())
            for root in (args.corpus, args.collection)
        ):
            raise ValueError("Replay output must be outside the source roots")
        raw = read_artifact(args.case)
        if digest(raw) != args.case_sha256:
            raise ValueError("Replay case SHA-256 differs")
        result = replay(args.corpus, args.collection, RecommendationCase.model_validate_json(raw))
        write_json(args.output, {**result, "case_sha256": args.case_sha256})
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
