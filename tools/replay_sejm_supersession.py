"""Verify that an adopted Sejm text follows a specific committee draft on pinned evidence."""

import argparse
import datetime as dt
import gzip
import json
import re
from contextlib import closing
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.adapters.sejm_api import parse_process_detail
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    DocumentPart,
    DocumentSelection,
    DocumentSelectionRecord,
    PdfTextExtraction,
    SourceObservation,
    Stage,
    TextDocument,
    flatten_stages,
    latest_text_document,
)
from lexinform.models.artifacts import ArtifactReadIdentity
from lexinform.models.candidate_trace import (
    CandidateDispositionTrace,
    SejmAdoptedText,
    SejmSupersededDraft,
)
from lexinform.models.reviews import date_in_quote
from lexinform.pdf_projection import legacy_pdf_text
from lexinform.services.document_history import DocumentSourceRead, record_document_source
from lexinform.services.documents import TextLoader
from tools.audit_sejm_adoption import audit_adoption
from tools.corpus_artifacts import create_only, digest, read_artifact, write_json
from tools.replay_proposal_edition import PinnedInput, official_print


class SupersessionCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    term: int = Field(gt=0, le=99, strict=True)
    owner: str = Field(pattern=r"^[1-9][0-9]{0,8}$")
    report: str = Field(pattern=r"^[1-9][0-9]{0,8}$")
    adoption_index: int = Field(ge=0, strict=True)
    report_draft_page: int = Field(ge=2, le=600, strict=True)
    processes: PinnedInput
    prints: PinnedInput
    report_pdf: PinnedInput
    adopted_pdf: PinnedInput


def _body(
    extraction: PdfTextExtraction, *, first: int, opening: re.Pattern[str], closing: re.Pattern[str]
) -> tuple[re.Match[str], dict[str, object]]:
    pages = extraction.pages
    if (
        extraction.error_type is not None
        or first < 1
        or first > len(pages)
        or any(page.error_type is not None or not page.text.strip() for page in pages)
    ):
        raise ValueError("Expected a complete readable PDF page sequence")
    first_page, last_page = pages[first - 1], pages[-1]
    start = opening.fullmatch(" ".join(first_page.text.split()))
    end = closing.fullmatch(" ".join(last_page.text.split()))
    if start is None or end is None:
        raise ValueError("Expected exact opening and final commencement boundaries")
    return start, {
        "artifact_sha256": extraction.input_sha256,
        "first_page": first,
        "first_page_sha256": first_page.text_sha256,
        "last_page": len(pages),
        "last_page_sha256": last_page.text_sha256,
        "last_article": int(end["article"]),
        "commencement_sha256": digest(end["clause"].encode()),
    }


def prove_bodies(
    case: SupersessionCase,
    report: PdfTextExtraction,
    adopted: PdfTextExtraction,
    *,
    report_date: dt.date,
    adoption_date: dt.date,
) -> dict[str, object]:
    if report.input_sha256 == adopted.input_sha256 or not report.pages:
        raise ValueError("Expected distinct report and adopted bodies")
    cover = " ".join(report.pages[0].text.split())
    if (
        re.match(rf"Druk nr {case.report}\b.*?\bS P R A W O Z D A N I E KOMISJI\b", cover) is None
        or re.findall(r"\(druk nr ([0-9]+)\)", cover) != [case.owner]
        or "uchwalić raczy załączony projekt ustawy." not in cover
    ):
        raise ValueError("Report cover must name its owner and attached draft")
    article = (
        r".*?\bArt\. (?P<article>[1-9][0-9]*)\. "
        r"(?P<clause>Ustawa wchodzi w życie z dniem [1-9][0-9]* \w+ [0-9]{4} r\., "
        r"z wyjątkiem art\. [1-9][0-9]*, który wchodzi w życie "
        r"z dniem następującym po dniu ogłoszenia\.)"
    )
    title = r"(?P<title>.+?) Rozdział 1 .+? Art\. 1\. .+"
    prior_heading, prior = _body(
        report,
        first=case.report_draft_page,
        opening=re.compile(
            rf"Liczba stron : (?P<count>[1-9][0-9]*) Data : (?P<date>[0-9-]+) Nazwa pliku : \S+ 1 "
            rf"X kadencja/druk {case.owner} Projekt U S T AWA z dnia {title}"
        ),
        closing=re.compile(article),
    )
    adopted_heading, successor = _body(
        adopted,
        first=1,
        opening=re.compile(
            r"Tekst ustawy przekazany do Senatu zgodnie z art\. 52 regulaminu Sejmu "
            rf"USTAWA z dnia (?P<date>[0-9]+ \S+ [0-9]{{4}}) r\. {title}"
        ),
        closing=re.compile(article + r" MARSZAŁEK SEJMU / [–-] / [^/]+"),
    )
    if int(prior_heading["count"]) != len(report.pages) - case.report_draft_page + 1:
        raise ValueError("Report draft page count differs from its printed count")
    if prior_heading["date"] != report_date.isoformat() or not date_in_quote(
        adoption_date, adopted_heading["date"]
    ):
        raise ValueError("PDF dates differ from the official report and adoption stages")
    title_text = prior_heading["title"]
    if (
        title_text != adopted_heading["title"]
        or prior["commencement_sha256"] == successor["commencement_sha256"]
    ):
        raise ValueError("Expected the same titled bill with distinct literal commencement clauses")
    return {
        "title_sha256": digest(title_text.encode()),
        "report_cover_sha256": report.pages[0].text_sha256,
        "prior": prior,
        "successor": successor,
    }


def _snapshot(
    case: SupersessionCase,
    report_document: TextDocument,
    report_bytes: bytes,
    report: PdfTextExtraction,
    report_date: dt.date,
    adopted_stage: Stage,
    adopted_bytes: bytes,
    recorded_at: dt.datetime,
) -> tuple[str, dict[str, str]]:
    first, last = case.report_draft_page, len(report.pages)
    text = legacy_pdf_text(page.text for page in report.pages[first - 1 :])
    report_read = ArtifactReadIdentity(
        requested_url=report_document.url, sha256=report.input_sha256, byte_size=len(report_bytes)
    )
    prior = DocumentSelectionRecord.from_selection(
        DocumentSelection(
            question="sejm_committee_draft",
            policy_version="sejm-committee-draft-pages-v1",
            parts=(
                DocumentPart(
                    url=report_document.url,
                    sha256=report_read.sha256,
                    role="bill",
                    pages=last,
                    page_start=first,
                    page_end=last,
                    text_end=len(text),
                    text_sha256=digest(text.encode()),
                ),
            ),
        )
    )
    if (
        adopted_stage.text_after3 is None
        or adopted_stage.decision is None
        or adopted_stage.date is None
    ):
        raise ValueError("The adopted stage needs its official attachment, date and decision")
    adopted_document = TextDocument(url=adopted_stage.text_after3, kind="text_after3")

    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        if url != adopted_document.url or (
            max_bytes is not None and len(adopted_bytes) > max_bytes
        ):
            raise ValueError("Unexpected adopted attachment read")
        return adopted_bytes

    loader = TextLoader(
        {"api.sejm.gov.pl": download},
        DocumentTextExtractor(PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor()),
        max_bytes=len(adopted_bytes),
    )
    selected, loaded = loader.select_document(adopted_document, retain_routine_trace=True)
    source = loader.capture_source_read(adopted_document, selected, loaded)
    if not isinstance(source, DocumentSourceRead) or source.candidate_trace is None:
        raise ValueError("Expected the exact native adopted text selection")

    report_observation = SourceObservation(
        term=case.term,
        number=case.owner,
        purpose="analysis",
        document=report_document,
        selection_id=prior.selection_id,
        recorded_at=recorded_at,
    )
    adopted_observation = SourceObservation(
        term=case.term,
        number=case.owner,
        purpose="analysis",
        document=source.document,
        selection_id=source.selection.selection_id,
        recorded_at=recorded_at,
    )
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        with repo.atomic():
            record_document_source(
                repo, prior, report_observation, (report_read,), reads=(report_read,)
            )
            record_document_source(
                repo,
                source.selection,
                adopted_observation,
                source.artifacts,
                reads=source.reads,
                extractions=source.extractions,
            )
            report_edition = repo.get_selection_edition(prior.selection_id)
            adopted_edition = repo.get_selection_edition(source.selection.selection_id)
            if report_edition is None or adopted_edition is None:
                raise ValueError("Expected two exact edition parents")
            (part,) = source.selection.selection.parts
            draft = SejmSupersededDraft(
                report=case.report,
                report_date=report_date,
                observation_id=report_observation.observation_id,
                edition_id=report_edition.edition_id,
                pdf_sha256=report_read.sha256,
                text_sha256=prior.selection.parts[0].text_sha256,
                first_page=first,
                last_page=last,
            )
            proof = SejmAdoptedText(
                term=case.term,
                number=case.owner,
                stage_name=adopted_stage.stage_name,
                decision=adopted_stage.decision,
                stage_date=adopted_stage.date,
                pdf_sha256=part.sha256,
                pdf_text_sha256=part.text_sha256,
                selection_id=source.selection.selection_id,
                edition_id=adopted_edition.edition_id,
                supersedes=draft,
            )
            trace = CandidateDispositionTrace.model_validate(
                {**source.candidate_trace.model_dump(), "sejm_adopted_text": proof}
            )
            repo.save_candidate_disposition_trace(adopted_observation.observation_id, trace)
        checkpoint = repo.dump()
        for _ in range(2):
            repo.restore(checkpoint)
            if (
                repo.get_candidate_disposition_trace(trace.trace_id) != trace
                or repo.dump() != checkpoint
            ):
                raise ValueError("Supersession changed during snapshot restore")
    return checkpoint, {
        "trace_id": trace.trace_id,
        "report_edition_id": report_edition.edition_id,
        "adopted_edition_id": adopted_edition.edition_id,
        "report_selection_id": prior.selection_id,
        "adopted_selection_id": source.selection.selection_id,
    }


def replay(
    corpus: Path,
    bodies: Path,
    case: SupersessionCase,
    *,
    snapshot_output: Path | None = None,
    recorded_at: dt.datetime | None = None,
) -> dict[str, object]:
    raw: dict[str, Any] = json.loads(gzip.decompress(case.processes.read(corpus)))[case.owner]
    adopted = audit_adoption(
        corpus / case.processes.path,
        snapshot_sha256=case.processes.sha256,
        term=case.term,
        number=case.owner,
        stage_index=case.adoption_index,
    )
    listing = json.loads(case.prints.read(corpus))
    detail = parse_process_detail(raw)
    report_url = (
        f"https://api.sejm.gov.pl/sejm/term{case.term}/prints/{case.report}/{case.report}.pdf"
    )
    row = official_print(listing, term=case.term, number=case.report)
    if row.get("processPrint") != [case.owner] or f"{case.report}.pdf" not in row.get(
        "attachments", []
    ):
        raise ValueError("Expected the same owner's official report attachment")
    before = detail.stages[: case.adoption_index]
    reports = [
        stage
        for stage in flatten_stages(before)
        if stage.print_number == case.report and stage.report_file == report_url
    ]
    previous = latest_text_document(before)
    adoption_stage = detail.stages[case.adoption_index]
    if (
        len(reports) != 1
        or not reports[0].carries_bill_text
        or reports[0].proposal != "załączony projekt ustawy"
        or reports[0].date is None
        or adoption_stage.date is None
        or reports[0].date >= adoption_stage.date
        or sum(s.text_after3 == adoption_stage.text_after3 for s in flatten_stages(detail.stages))
        != 1
        or previous is None
        or previous.url != report_url
        or previous.kind != "committee_report"
    ):
        raise ValueError("Expected the exact preceding committee draft in the official stage tree")
    pdf = PypdfTextExtractor()
    report_bytes, adopted_bytes = case.report_pdf.read(bodies), case.adopted_pdf.read(bodies)
    report_pdf = pdf.extract_pages(report_bytes)
    evidence = prove_bodies(
        case,
        report_pdf,
        pdf.extract_pages(adopted_bytes),
        report_date=reports[0].date,
        adoption_date=adoption_stage.date,
    )
    result: dict[str, object] = {
        "relation": "supersedes",
        "scope": "Sejm process text at third reading",
        "term": case.term,
        "owner": case.owner,
        "report_url": report_url,
        "adopted_url": adopted["adopted_text_locator"],
        "report_date": reports[0].date.isoformat(),
        "adoption_date": adopted["stage_date"],
        "adoption_stage_sha256": adopted["stage_sha256"],
        "evidence": evidence,
    }

    if snapshot_output is not None:
        if recorded_at is None or recorded_at.utcoffset() is None:
            raise ValueError("Snapshot requires an explicit recording timestamp with timezone")
        checkpoint, parents = _snapshot(
            case,
            previous,
            report_bytes,
            report_pdf,
            reports[0].date,
            adoption_stage,
            adopted_bytes,
            recorded_at,
        )
        payload = checkpoint.encode()
        create_only(snapshot_output, payload)
        result["snapshot"] = {**parents, "sql_sha256": digest(payload), "sql_bytes": len(payload)}
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--bodies", type=Path, required=True)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--case-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--snapshot-output", type=Path)
    parser.add_argument("--recorded-at", type=dt.datetime.fromisoformat)
    args = parser.parse_args()
    try:
        outputs = [
            path.resolve() for path in (args.output, args.snapshot_output) if path is not None
        ]
        if len(set(outputs)) != len(outputs) or any(path.exists() for path in outputs):
            raise ValueError("Replay requires distinct new output paths")
        if any(
            path.is_relative_to(root.resolve())
            for path in outputs
            for root in (args.corpus, args.bodies)
        ):
            raise ValueError("Output must be outside the source roots")
        raw = read_artifact(args.case)
        if digest(raw) != args.case_sha256:
            raise ValueError("Case SHA-256 differs from its expected pin")
        result = replay(
            args.corpus.resolve(strict=True),
            args.bodies.resolve(strict=True),
            SupersessionCase.model_validate_json(raw),
            snapshot_output=args.snapshot_output,
            recorded_at=args.recorded_at,
        )
        write_json(args.output, {"case_sha256": args.case_sha256, **result})
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
