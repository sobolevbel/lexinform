"""Prove one official Sejm self-amendment relation to a native candidate edition offline."""

import argparse
import datetime as dt
import gzip
import json
from contextlib import closing
from pathlib import Path
from typing import Any, Literal

import httpx2 as httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.adapters.sejm_api import SejmApiClient, parse_print, parse_process_detail
from lexinform.document_editions import candidate_edition
from lexinform.models import Bill, BillStatus, DocumentSelectionRecord, PrintInfo, TextDocument
from lexinform.services.documents import TextLoader
from lexinform.services.sources import SejmTextSource
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_runtime import CorpusReads, code_version, source_fingerprints

_MAX_PDF_BYTES = 32_000_000
_REPLAY_TIME = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)
_OFFICIAL_PRINTS = TypeAdapter(list[dict[str, Any]])
_OFFICIAL_PROCESSES = TypeAdapter(dict[str, dict[str, Any]])


class PinnedInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    def read(self, root: Path) -> bytes:
        relative = Path(self.path)
        path = root / relative
        if relative.is_absolute() or not path.resolve().is_relative_to(root):
            raise ValueError("Replay input must be inside the corpus")
        raw = read_artifact(path)
        if digest(raw) != self.sha256:
            raise ValueError(f"Replay input SHA-256 differs: {self.path}")
        return raw


class ProposalCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["sejm-proposal-edition-v1"] = "sejm-proposal-edition-v1"
    term: int = Field(gt=0)
    parent: str = Field(pattern=r"^[0-9]+$")
    proposal: str = Field(pattern=r"^[0-9]+-[A-Z]+$")
    official_prints: PinnedInput
    official_processes_gzip: PinnedInput
    base_pdf: PinnedInput
    proposal_pdf: PinnedInput


def official_print(listing: list[dict[str, Any]], *, term: int, number: str) -> dict[str, Any]:
    matches = [row for row in listing if row.get("number") == number]
    if len(matches) != 1 or matches[0].get("term") != term:
        raise ValueError(f"Expected one exact same-term official print: {number}")
    return matches[0]


def prove_proposal(
    *,
    parent: PrintInfo,
    proposal: PrintInfo,
    document: TextDocument,
    record: DocumentSelectionRecord,
    base_sha256: str,
    proposal_sha256: str,
) -> dict[str, object]:
    base_pdf, proposal_pdf = parent.main_pdf, proposal.main_pdf
    if (
        proposal.term != parent.term
        or proposal.number == parent.number
        or proposal.process_prints != (parent.number,)
        or not proposal.title.casefold().startswith("autopoprawka")
        or base_pdf is None
        or proposal_pdf is None
        or base_sha256 == proposal_sha256
    ):
        raise ValueError("Expected an exact same-term self-amendment and distinct official PDFs")
    if (
        document.kind != "print"
        or document.version_basis != (f"sejm/{parent.term}/print/{parent.number}",)
        or document.url != base_pdf.url
        or document.amendment_urls != (proposal_pdf.url,)
        or document.alternatives
        or document.required_urls
        or document.normative_urls
        or document.extra_urls
        or document.official_link_conflict
    ):
        raise ValueError("Expected the original single-PDF print and one related proposal")
    expected = (
        ("bill", base_pdf.url, base_sha256),
        ("self_amendment", proposal_pdf.url, proposal_sha256),
    )
    parts = record.selection.parts
    observed = tuple((part.role, part.url, part.source_sha256) for part in parts)
    if observed != expected or any(part.members for part in parts):
        raise ValueError("The exact official PDF identities must occur as distinct selected roles")
    edition = candidate_edition(record, recorded_at=_REPLAY_TIME)
    if edition is None:
        raise ValueError("The native selection has no candidate normative edition")
    return {
        "relation": "proposes_change_to",
        "term": parent.term,
        "parent_print": parent.number,
        "proposal_print": proposal.number,
        "official_process_prints": proposal.process_prints,
        "base_url": base_pdf.url,
        "base_sha256": base_sha256,
        "proposal_url": proposal_pdf.url,
        "proposal_sha256": proposal_sha256,
        "selection_id": record.selection_id,
        "selection_state": record.selection.state,
        "edition_id": edition.edition_id,
        "edition_members": edition.member_count,
    }


def replay(corpus: Path, case: ProposalCase) -> dict[str, object]:
    root = corpus.resolve(strict=True)
    reads, before = CorpusReads(root), source_fingerprints()
    with reads.guard():
        listing_raw = case.official_prints.read(root)
        listing = _OFFICIAL_PRINTS.validate_json(listing_raw)
        base_row = official_print(listing, term=case.term, number=case.parent)
        proposal_row = official_print(listing, term=case.term, number=case.proposal)
        processes = _OFFICIAL_PROCESSES.validate_json(
            gzip.decompress(case.official_processes_gzip.read(root))
        )
        process = processes[case.parent]
        if (process.get("term"), process.get("number")) != (case.term, case.parent):
            raise ValueError("Process metadata differs from the requested print")
        base, proposal = (parse_print(row, term=case.term) for row in (base_row, proposal_row))
        base_pdf, proposal_pdf = base.main_pdf, proposal.main_pdf
        if base_pdf is None or proposal_pdf is None or base_pdf.url == proposal_pdf.url:
            raise ValueError("Separate official base and proposal PDFs are required")
        bodies = {
            base_pdf.url: case.base_pdf.read(root),
            proposal_pdf.url: case.proposal_pdf.read(root),
        }

        def download(url: str, *, max_bytes: int | None = None) -> bytes:
            body = bodies[url]
            limit = _MAX_PDF_BYTES if max_bytes is None else max_bytes
            if len(body) > limit or not body.startswith(b"%PDF-"):
                raise ValueError("A pinned original must be a PDF within the replay bound")
            return body

        prefix = f"/sejm/term{case.term}"
        paths: list[str] = []

        def get(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            paths.append(path)
            if path == f"{prefix}/prints":
                return httpx.Response(200, content=listing_raw)
            if path == f"{prefix}/prints/{case.parent}":
                return httpx.Response(200, json=base_row)
            if path == f"{prefix}/processes/{case.parent}":
                return httpx.Response(200, json=process)
            raise ValueError("Unpinned Sejm request")

        with closing(SejmApiClient(transport=httpx.MockTransport(get))) as gateway:
            summary = parse_process_detail(process)
            bill = Bill(
                summary=summary,
                status=BillStatus.ANALYZED,
                first_seen_at=_REPLAY_TIME,
                last_checked_at=_REPLAY_TIME,
            )
            document = SejmTextSource(gateway).locate(bill).document
        if document is None or document.kind != "print":
            raise ValueError("The native source did not select the original print")
        reader = DocumentTextExtractor(
            PypdfTextExtractor(),
            DocxTextExtractor(),
            DocTextExtractor(),
            max_member_bytes=_MAX_PDF_BYTES,
        )
        loader = TextLoader({"api.sejm.gov.pl": download}, reader, max_bytes=_MAX_PDF_BYTES)
        selected, loaded = loader.select_document(document)
        record = DocumentSelectionRecord.from_selection(loaded.selection)
        proof = prove_proposal(
            parent=base,
            proposal=proposal,
            document=selected,
            record=record,
            base_sha256=case.base_pdf.sha256,
            proposal_sha256=case.proposal_pdf.sha256,
        )
    if reads.changed_inputs() or before != source_fingerprints():
        raise ValueError("Replay inputs or code changed")
    emitted_sha256 = digest(loaded.text.encode()) if loaded.text is not None else None
    return {
        "version": case.version,
        "status": "recorded",
        "proof": proof,
        "native_emitted_text_sha256": emitted_sha256,
        "mock_metadata_requests": paths,
        "read_inputs": reads.initial_sha256,
        "code_version": code_version(),
        "source_pins_sha256": digest(json.dumps(before, sort_keys=True).encode()),
        "scope": "One native original-print selection from supplied pinned Sejm inputs.",
        "exclusions": [
            "Adoption",
            "supersession",
            "legal completeness",
            "historical state backfill",
        ],
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
        report = replay(args.corpus, ProposalCase.model_validate_json(raw))
        write_json(args.output, {**report, "case_sha256": args.case_sha256})
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
