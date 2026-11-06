"""Identify a Sejm committee recommendation for the exact Senate resolution."""

import hashlib
import re
from dataclasses import dataclass
from typing import Literal, TypedDict

from lexinform.models import Stage, flatten_stages, senate_position_reported


@dataclass(frozen=True)
class CommitteeRecommendationOrigin:
    report: Stage
    resolution: Stage


class CommitteeReportEvidence(TypedDict):
    page: Literal[1]
    heading_sha256: str


def official_recommendation_stages(
    stages: tuple[Stage, ...], *, report: str, resolution: str, report_url: str
) -> tuple[Stage, Stage]:
    flat = flatten_stages(stages)
    reports = [
        stage
        for stage in flat
        if stage.stage_type == "CommitteeReport" and stage.print_number == report
    ]
    targets = [
        stage
        for stage in flat
        if stage.stage_type == "SenatePosition" and stage.print_number == resolution
    ]
    if len(reports) != 1 or len(targets) != 1:
        raise ValueError("Expected one official report and one Senate position")
    candidate, target = reports[0], targets[0]
    if (
        candidate.report_file != report_url
        or candidate.sub_committee
        or candidate.decision is not None
        or candidate.proposal != "przyjąć część poprawek"
        or candidate.date is None
        or target.date is None
        or candidate.date <= target.date
        or senate_position_reported(candidate, stages) != target
    ):
        raise ValueError("Official report must answer the exact earlier amending Senate position")
    return candidate, target


def report_evidence(first_page: str, report: str, resolution: str) -> CommitteeReportEvidence:
    page = " ".join(first_page.casefold().split())
    header, marker, body = page.partition("marszałek sejmu,")
    citations = re.findall(r"\(druk nr ([1-9][0-9]*(?:-[a-z]+)?)\)", header)
    if (
        not marker
        or not re.match(rf"druk nr {re.escape(report.casefold())}(?:\s|$)", header)
        or re.search(r"\bs\s*p\s*r\s*a\s*w\s*o\s*z\s*d\s*a\s*n\s*i\s*e komisji\b", header) is None
        or "o uchwale senatu w sprawie" not in header
        or citations != [resolution]
        or len(re.findall(r"\bdruki?\s+nr\b", header)) != 2
    ):
        raise ValueError("Report heading must name the exact Senate resolution once")
    if (
        re.search(
            r"\bwnos(?:i|zą):\s+w\s*y\s*s\s*o\s*k\s*i\s+s\s*e\s*j\s*m"
            r"\s+raczy poprawki senatu zawarte w:",
            body,
        )
        is None
        or re.search(r"\bpkt [1-9][0-9]* [–-] (?:przyjąć|odrzucić)\b", body) is None
    ):
        raise ValueError("Report must contain a committee recommendation for Senate amendments")
    return {"page": 1, "heading_sha256": hashlib.sha256(header.rstrip().encode()).hexdigest()}
