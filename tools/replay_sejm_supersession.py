"""Verify that an adopted Sejm text follows a specific committee draft on pinned evidence."""

import argparse
import datetime as dt
import gzip
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.adapters.sejm_api import parse_process_detail
from lexinform.models import PdfTextExtraction, flatten_stages, latest_text_document
from lexinform.models.reviews import date_in_quote
from tools.audit_sejm_adoption import audit_adoption
from tools.corpus_artifacts import digest, read_artifact, write_json
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


def replay(corpus: Path, bodies: Path, case: SupersessionCase) -> dict[str, object]:
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
    evidence = prove_bodies(
        case,
        pdf.extract_pages(case.report_pdf.read(bodies)),
        pdf.extract_pages(case.adopted_pdf.read(bodies)),
        report_date=reports[0].date,
        adoption_date=adoption_stage.date,
    )
    return {
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--bodies", type=Path, required=True)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--case-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if any(
            args.output.resolve().is_relative_to(root.resolve())
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
        )
        write_json(args.output, {"case_sha256": args.case_sha256, **result})
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
