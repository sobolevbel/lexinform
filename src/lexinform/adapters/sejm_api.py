"""Client for the official Sejm REST API (https://api.sejm.gov.pl).

Verified behaviour of the API (September 2026):
- `/processes` honours `limit`, `offset`, `modifiedSince` (full ISO datetime, date-only is an
  error) and `documentType` (the Polish display string, e.g. "projekt ustawy"); `sort_by` and
  `passed` are ignored, results come in ascending print-number order. We therefore paginate by
  offset until an empty page.
- All timestamps (`changeDate`, `modifiedSince`) are naive local time of the Sejm servers
  (Europe/Warsaw); `Z` or an offset suffix is rejected. `SEJM_TZ` converts both ways.
- `/prints` ignores `limit`/`modifiedSince`, so we never list it; we only fetch single prints.
- Attachments are served from `/prints/{number}/{attachment name}`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx2 as httpx

from lexinform.errors import SejmApiUnavailableError
from lexinform.models import (
    BILL_DOCUMENT_TYPE,
    ApplicantType,
    Attachment,
    BillSubmission,
    Committee,
    DocumentType,
    PrintInfo,
    ProcessDetail,
    ProcessSummary,
    Stage,
    Vote,
    VotingSummary,
)

__all__ = ["BILL_DOCUMENT_TYPE", "SejmApiClient", "SejmApiError"]

log = logging.getLogger(__name__)

SEJM_TZ = ZoneInfo("Europe/Warsaw")


def to_sejm_local(value: datetime) -> datetime:
    """Aware datetime -> naive wall clock as the Sejm API expects it."""
    if value.tzinfo is None:
        return value
    return value.astimezone(SEJM_TZ).replace(tzinfo=None)


def from_sejm_local(value: datetime) -> datetime:
    """Naive Sejm API timestamp -> aware datetime (UTC)."""
    if value.tzinfo is not None:
        return value
    return value.replace(tzinfo=SEJM_TZ).astimezone(UTC)


class SejmApiError(RuntimeError):
    """A 4xx answer for one request (missing print, bad number): a per-item problem."""


class SejmApiClient:
    def __init__(
        self,
        base_url: str = "https://api.sejm.gov.pl",
        *,
        page_size: int = 100,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_seconds: float = 1.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={"Accept": "application/json", "User-Agent": "lexinform (+github)"},
            transport=transport,
            follow_redirects=True,
        )
        self._page_size = page_size
        self._max_retries = max_retries
        self._backoff = backoff_seconds
        self._sleep = sleep

    # ------------------------------------------------------------------ public API

    def iter_processes(
        self,
        term: int,
        *,
        modified_since: datetime | None = None,
        document_type: str | None = None,
    ) -> Iterator[ProcessSummary]:
        params: dict[str, str | int] = {"limit": self._page_size}
        if modified_since is not None:
            local = to_sejm_local(modified_since).replace(microsecond=0)
            params["modifiedSince"] = local.isoformat()
        if document_type:
            params["documentType"] = document_type
        offset = 0
        previous_first: str | None = None
        while True:
            page = self._get_json(
                f"/sejm/term{term}/processes", params={**params, "offset": offset}
            )
            if not isinstance(page, list):
                raise SejmApiError(f"Unexpected /processes payload: {type(page).__name__}")
            if not page:
                return
            first = str(page[0].get("number"))
            if first == previous_first:
                # The server ignored `offset`: stop instead of looping forever.
                log.warning("/processes returned the same page twice at offset %d", offset)
                return
            previous_first = first
            for item in page:
                yield parse_process_summary(item)
            if len(page) < self._page_size:
                return
            offset += len(page)

    BILLS_PAGE_SIZE = 500

    def iter_bills(
        self, term: int, *, received_from: date | None = None
    ) -> Iterator[BillSubmission]:
        """GET /bills: submitted bills incl. those without a print number yet."""
        params: dict[str, str | int] = {"limit": self.BILLS_PAGE_SIZE}
        if received_from is not None:
            params["dateOfReceiptFrom"] = received_from.isoformat()
        offset = 0
        while True:
            page = self._get_json(f"/sejm/term{term}/bills", params={**params, "offset": offset})
            if not isinstance(page, list) or not page:
                return
            for item in page:
                yield parse_submission(item, term=term)
            if len(page) < self.BILLS_PAGE_SIZE:
                return
            offset += len(page)

    def find_submission(self, term: int, print_number: str) -> BillSubmission | None:
        page = self._get_json(f"/sejm/term{term}/bills", params={"print": print_number})
        items = (
            [i for i in page if str(i.get("print")) == print_number]
            if isinstance(page, list)
            else []
        )
        return parse_submission(items[0], term=term) if items else None

    def get_process(self, term: int, number: str) -> ProcessDetail:
        data = self._get_json(f"/sejm/term{term}/processes/{quote(number)}")
        return parse_process_detail(data)

    def get_print(self, term: int, number: str) -> PrintInfo:
        data = self._get_json(f"/sejm/term{term}/prints/{quote(number)}")
        return parse_print(data, term=term)

    def get_voting(self, term: int, sitting: int, number: int) -> tuple[Vote, ...]:
        data = self._get_json(f"/sejm/term{term}/votings/{sitting}/{number}")
        return tuple(parse_vote(v) for v in data.get("votes") or ())

    def get_committee(self, term: int, code: str) -> Committee:
        data = self._get_json(f"/sejm/term{term}/committees/{quote(code)}")
        return parse_committee(data, term=term)

    def attachment_size(self, url: str) -> int | None:
        response = self._request("HEAD", url)
        length = response.headers.get("content-length")
        return int(length) if length and length.isdigit() else None

    def download(self, url: str) -> bytes:
        return self._request("GET", url).content

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------ internals

    def _get_json(self, path: str, params: dict[str, str | int] | None = None) -> Any:
        return self._request("GET", path, params=params).json()

    def _request(
        self, method: str, url: str, *, params: dict[str, str | int] | None = None
    ) -> httpx.Response:
        attempt = 0
        while True:
            attempt += 1
            try:
                response = self._client.request(method, url, params=params)
            except httpx.TransportError as exc:
                if attempt > self._max_retries:
                    raise SejmApiUnavailableError(
                        f"{method} {url} failed after {attempt} attempts: {exc}"
                    ) from exc
                self._wait(attempt, f"{method} {url}: {exc}")
                continue
            if response.status_code >= 500 or response.status_code == 429:
                if attempt <= self._max_retries:
                    self._wait(attempt, f"{method} {url}: HTTP {response.status_code}")
                    continue
                raise SejmApiUnavailableError(
                    f"{method} {url}: HTTP {response.status_code} after {attempt} attempts"
                )
            if response.status_code >= 400:
                raise SejmApiError(f"{method} {url}: HTTP {response.status_code}")
            return response

    def _wait(self, attempt: int, reason: str) -> None:
        delay = self._backoff * (2 ** (attempt - 1))
        log.warning("Sejm API retry %d in %.1fs (%s)", attempt, delay, reason)
        self._sleep(delay)


# ---------------------------------------------------------------------- parsing helpers


def _date(value: Any) -> Any:
    if not value:
        return None
    return datetime.fromisoformat(str(value)).date() if "T" in str(value) else str(value)


def _datetime(value: Any) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value))


def _aware_datetime(value: Any) -> datetime | None:
    parsed = _datetime(value)
    return from_sejm_local(parsed) if parsed is not None else None


def _document_type_enum(value: Any) -> DocumentType:
    try:
        return DocumentType(str(value))
    except ValueError:
        return DocumentType.OTHER


def _summary_fields(item: dict[str, Any]) -> dict[str, Any]:
    change_date = _aware_datetime(item.get("changeDate")) or _aware_datetime(
        item.get("webGeneratedDate")
    )
    if change_date is None:
        raise SejmApiError(f"Process {item.get('number')} has no changeDate")
    return {
        "term": int(item["term"]),
        "number": str(item["number"]),
        "title": str(item.get("title") or "").strip(),
        "description": (item.get("description") or None),
        "document_type": str(item.get("documentType") or ""),
        "document_type_enum": _document_type_enum(item.get("documentTypeEnum")),
        "process_start_date": _date(item.get("processStartDate")),
        "document_date": _date(item.get("documentDate")),
        "change_date": change_date,
        "closure_date": _date(item.get("closureDate")),
        "passed": item.get("passed"),
        "urgency_status": item.get("urgencyStatus"),
        # UEStatus enum: NO | ADAPTATION | ENFORCEMENT (never "YES").
        "eu_related": str(item.get("UE") or "NO").upper() != "NO",
        "rcl_num": item.get("rclNum"),
        "rcl_link": item.get("rclLink"),
        "prints_considered_jointly": tuple(
            str(p) for p in item.get("printsConsideredJointly") or ()
        ),
    }


def parse_process_summary(item: dict[str, Any]) -> ProcessSummary:
    return ProcessSummary(**_summary_fields(item))


def parse_stage(item: dict[str, Any]) -> Stage:
    return Stage(
        stage_name=str(item.get("stageName") or ""),
        stage_type=str(item.get("stageType") or ""),
        date=_date(item.get("date")),
        print_number=item.get("printNumber"),
        sitting_num=item.get("sittingNum"),
        decision=item.get("decision"),
        position=item.get("position"),
        committee_code=item.get("committeeCode"),
        report_file=item.get("reportFile"),
        text_after3=item.get("textAfter3"),
        proposal=item.get("proposal"),
        sub_committee=bool(item.get("subCommittee", False)),
        voting=parse_voting(voting) if isinstance(voting := item.get("voting"), dict) else None,
        children=tuple(parse_stage(c) for c in item.get("children") or ()),
    )


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def parse_voting(item: dict[str, Any]) -> VotingSummary:
    pdf = next(
        (link.get("href") for link in item.get("links") or () if link.get("rel") == "pdf"), None
    )
    return VotingSummary(
        yes=_int(item.get("yes")) or 0,
        no=_int(item.get("no")) or 0,
        abstain=_int(item.get("abstain")) or 0,
        not_participating=_int(item.get("notParticipating")) or 0,
        total_voted=_int(item.get("totalVoted")),
        majority_type=item.get("majorityType"),
        majority_votes=_int(item.get("majorityVotes")),
        sitting=_int(item.get("sitting")),
        voting_number=_int(item.get("votingNumber")),
        date=_aware_datetime(item.get("date")),
        description=item.get("description"),
        topic=item.get("topic"),
        pdf_url=pdf,
    )


def parse_vote(item: dict[str, Any]) -> Vote:
    return Vote(
        mp=_int(item.get("MP")) or 0,
        club=str(item.get("club") or ""),
        vote=str(item.get("vote") or ""),
    )


_APPLICANTS = {
    "GOVERNMENT": ApplicantType.GOVERNMENT,
    "DEPUTIES": ApplicantType.DEPUTIES,
    "SENATE": ApplicantType.SENATE,
    "PRESIDENT": ApplicantType.PRESIDENT,
    "PRESIDIUM": ApplicantType.PRESIDIUM,
    "CITIZENS": ApplicantType.CITIZENS,
    "COMMITTEE": ApplicantType.COMMITTEE,
}


def parse_submission(item: dict[str, Any], *, term: int) -> BillSubmission:
    return BillSubmission(
        term=term,
        number=str(item["number"]),
        title=str(item.get("title") or "").strip(),
        description=item.get("description") or None,
        applicant=_APPLICANTS.get(str(item.get("applicantType") or ""), ApplicantType.UNKNOWN),
        status=str(item.get("status") or "ACTIVE"),
        submission_type=str(item.get("submissionType") or "BILL"),
        date_of_receipt=_date(item.get("dateOfReceipt")),
        print_number=str(item["print"]) if item.get("print") else None,
        eu_related=bool(item.get("euRelated", False)),
        public_consultation=bool(item.get("publicConsultation", False)),
        consultation_start=_date(item.get("publicConsultationStartDate")),
        consultation_end=_date(item.get("publicConsultationEndDate")),
        consultation_results=bool(item.get("consultationResults", False)),
        withdrawn_date=_date(item.get("withdrawnDate")),
    )


def parse_committee(item: dict[str, Any], *, term: int) -> Committee:
    return Committee(
        term=term,
        code=str(item["code"]),
        name=str(item.get("name") or item["code"]).strip(),
        name_genitive=item.get("nameGenitive"),
    )


def parse_process_detail(item: dict[str, Any]) -> ProcessDetail:
    return ProcessDetail(
        **_summary_fields(item),
        stages=tuple(parse_stage(s) for s in item.get("stages") or ()),
        title_final=item.get("titleFinal"),
        eli=item.get("ELI"),
    )


def attachment_url(base_url: str, term: int, number: str, name: str) -> str:
    return f"{base_url.rstrip('/')}/sejm/term{term}/prints/{quote(number)}/{quote(name)}"


def parse_print(
    item: dict[str, Any], *, term: int, base_url: str = "https://api.sejm.gov.pl"
) -> PrintInfo:
    number = str(item["number"])
    attachments = tuple(
        Attachment(
            print_number=number, name=str(n), url=attachment_url(base_url, term, number, str(n))
        )
        for n in item.get("attachments") or ()
    )
    additional = tuple(
        parse_print(p, term=term, base_url=base_url) for p in item.get("additionalPrints") or ()
    )
    return PrintInfo(
        term=term,
        number=number,
        title=str(item.get("title") or "").strip(),
        document_date=_date(item.get("documentDate")),
        delivery_date=_date(item.get("deliveryDate")),
        change_date=_aware_datetime(item.get("changeDate")),
        attachments=attachments,
        additional_prints=additional,
    )
