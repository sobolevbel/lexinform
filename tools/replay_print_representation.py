"""Prove a bounded representation of one native Sejm edition core offline."""

import argparse
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.adapters.sejm_api import parse_print
from lexinform.document_editions import candidate_edition
from lexinform.models import DocumentSelectionRecord, TextDocument
from lexinform.sections import document_kind, law_body
from lexinform.services.documents import TextLoader
from lexinform.services.sources import original_document
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_runtime import CorpusReads, code_version, source_fingerprints
from tools.replay_proposal_edition import PinnedInput, official_print

_PRINTS = TypeAdapter(list[dict[str, Any]])
_MAX_BYTES = 8_388_608
_ARTICLE_ONE = re.compile(r"\bArt\. 1\.")
_COMMENCEMENT = re.compile(
    r"\bArt\. [1-9][0-9]*\. Ustawa wchodzi w życie po upływie [1-9][0-9]* dni od dnia ogłoszenia\.$"
)
_ANNEX = re.compile(r"\bzałączni\w*", re.IGNORECASE)
_ANNEX_HEADING = re.compile(r"(?:^|[\n\f])[^\S\n\f]*załączni", re.IGNORECASE)


class RepresentationCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["sejm-print-core-representation-v1"] = "sejm-print-core-representation-v1"
    term: int = Field(gt=0, le=99, strict=True)
    number: str = Field(pattern=r"^[1-9][0-9]{0,8}$")
    official_prints: PinnedInput
    pdf: PinnedInput
    docx: PinnedInput
    pdf_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    docx_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def exact_core(text: str, *, pdf: bool) -> str:
    normalized = " ".join(text.split())
    core = " ".join((law_body(text, preserve_numbers=True) or "").split())
    first = _ARTICLE_ONE.search(normalized)
    if (
        first is None
        or not _ARTICLE_ONE.match(core)
        or not normalized.startswith(core, first.start())
        or _COMMENCEMENT.search(core) is None
        or len(core) < 200
        or _ANNEX.search(core) is not None
        or _ANNEX_HEADING.search(text) is not None
        or document_kind(text) != "bill"
    ):
        raise ValueError("Expected one literal normative core through final commencement")
    tail = normalized[first.start() + len(core) :].lstrip()
    if (pdf and not tail.startswith("UZASADNIENIE ")) or (not pdf and tail):
        raise ValueError("Expected PDF justification boundary and exact draft-only Word end")
    return core


def official_original(
    listing: list[dict[str, Any]], *, term: int, number: str
) -> tuple[TextDocument, str]:
    row = official_print(listing, term=term, number=number)
    pdf_name, docx_name = f"{number}.pdf", f"{number}-ustawa.docx"
    attachments = row.get("attachments")
    if (
        type(row["term"]) is not int
        or row.get("processPrint") != [number]
        or not isinstance(attachments, list)
        or sorted(attachments) != sorted([pdf_name, docx_name, f"{number}-uzasadnienie.docx"])
        or any(
            number in (item.get("processPrint") or ())
            and str(item.get("title") or "").strip().casefold().startswith("autopoprawka")
            for item in listing
        )
    ):
        raise ValueError(
            "Expected one original official PDF, draft DOCX and separate justification"
        )
    info = parse_print(row, term=term)
    document = original_document(info)
    if document is None or (
        document.alternatives
        or document.required_urls
        or document.normative_urls
        or document.amendment_urls
        or document.extra_urls
        or document.official_link_conflict
    ):
        raise ValueError("Expected one native original candidate without additional relations")
    return document, next(item.url for item in info.attachments if item.name == docx_name)


def prove(
    case: RepresentationCase, listing: list[dict[str, Any]], pdf: bytes, docx: bytes
) -> dict[str, object]:
    document, word_url = official_original(listing, term=case.term, number=case.number)
    if (
        not pdf.startswith(b"%PDF-")
        or not docx.startswith(b"PK")
        or max(len(pdf), len(docx)) > _MAX_BYTES
        or digest(pdf) != case.pdf.sha256
        or digest(docx) != case.docx.sha256
        or case.pdf.sha256 == case.docx.sha256
    ):
        raise ValueError("Expected distinct pinned PDF and DOCX bodies within bounds")
    pdf_reader, word_reader = PypdfTextExtractor(), DocxTextExtractor()
    pages = pdf_reader.extract_pages(pdf)
    word = word_reader.extract_blocks(docx)
    if (
        not pages.pages
        or pages.error_type is not None
        or any(page.error_type is not None or not page.text.strip() for page in pages.pages)
        or word.body_state != "read"
        or digest(pages.legacy_text.encode()) != case.pdf_text_sha256
        or digest(word.legacy_text.encode()) != case.docx_text_sha256
    ):
        raise ValueError("Expected the exact readable native extracts")
    source_core = exact_core(pages.legacy_text, pdf=True)
    if source_core != exact_core(word.legacy_text, pdf=False):
        raise ValueError("Native normative cores differ")
    bodies = {document.url: pdf, word_url: docx}

    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        body = bodies[url]
        if max_bytes is not None and len(body) > max_bytes:
            raise ValueError("Pinned body exceeds loader limit")
        return body

    loader = TextLoader(
        {"api.sejm.gov.pl": download},
        DocumentTextExtractor(pdf_reader, word_reader, DocTextExtractor()),
        max_bytes=_MAX_BYTES,
    )
    selected, loaded = loader.select_document(document)
    record = DocumentSelectionRecord.from_selection(loaded.selection)
    edition = candidate_edition(record, recorded_at=dt.datetime(1970, 1, 1, tzinfo=dt.UTC))
    parts = loaded.selection.parts
    if (
        selected != document
        or edition is None
        or edition.member_count != 1
        or len(parts) != 1
        or (part := parts[0]).members
        or (part.role, part.url, part.source_sha256, part.text_sha256)
        != ("bill", document.url, case.pdf.sha256, case.pdf_text_sha256)
        or loader.read(word_url).text != word.legacy_text
    ):
        raise ValueError("Exact official PDF does not bind the native single-member edition")
    return {
        "relation": "representation_of",
        "scope": "native_normative_core_only",
        "term": case.term,
        "number": case.number,
        "selection_id": record.selection_id,
        "selection_state": loaded.selection.state,
        "edition_id": edition.edition_id,
        "pdf_url": document.url,
        "pdf_sha256": case.pdf.sha256,
        "pdf_text_sha256": case.pdf_text_sha256,
        "docx_url": word_url,
        "docx_sha256": case.docx.sha256,
        "docx_text_sha256": case.docx_text_sha256,
        "core_chars": len(source_core),
        "core_sha256": digest(source_core.encode()),
        "adoption_outcome": "not_asserted",
    }


def replay(corpus: Path, case: RepresentationCase) -> dict[str, object]:
    root = corpus.resolve(strict=True)
    reads, before = CorpusReads(root), source_fingerprints()
    with reads.guard():
        result = prove(
            case,
            _PRINTS.validate_json(case.official_prints.read(root)),
            case.pdf.read(root),
            case.docx.read(root),
        )
    if reads.changed_inputs() or before != source_fingerprints():
        raise ValueError("Replay inputs or code changed")
    return {
        "version": case.version,
        "status": "recorded",
        "proof": result,
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
        result = replay(args.corpus, RepresentationCase.model_validate_json(raw))
        write_json(args.output, {**result, "case_sha256": args.case_sha256})
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
