"""Client for the official Sejm REST API (https://api.sejm.gov.pl).

Verified behaviour of the API (September 2026):
- `/processes` honours `limit`, `offset`, `modifiedSince` (full ISO datetime, date-only is an
  error) and `documentType` (the Polish display string, e.g. "projekt ustawy"); `sort_by` and
  `passed` are ignored, results come in ascending print-number order. We therefore paginate by
  offset until an empty page.
- All timestamps (`changeDate`, `modifiedSince`) are naive local time of the Sejm servers
  (Europe/Warsaw); `Z` or an offset suffix is rejected. `SEJM_TZ` converts both ways.
- `/prints` ignores `limit`/`modifiedSince`, so we never list it; we only fetch single prints.
- Attachments are served from `/prints/{number}/{attachment name}`. `HEAD` on them returns no
  `Content-Length` and takes 5-15 s on a file the server has not rendered yet, so sizes are
  enforced while streaming the `GET` instead.
"""

import logging
import time
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx2 as httpx

from lexinform.errors import AttachmentTooLargeError, SejmApiUnavailableError
from lexinform.models import (
    BILL_DOCUMENT_TYPE,
    ActInfo,
    ApplicantType,
    Attachment,
    BillSubmission,
    Committee,
    CommitteeSitting,
    DocumentType,
    Mp,
    PrintInfo,
    ProcessDetail,
    ProcessSummary,
    SejmSitting,
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
        self._base_url = base_url.rstrip("/")
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

    def get_act(self, eli: str) -> ActInfo | None:
        """The published act from the ELI API; None when the act is not (yet) indexed there."""
        response = self._request("GET", f"/eli/acts/{eli}", allow_404=True)
        if response.status_code == 404:
            return None
        return parse_act(response.json(), fetched_at=datetime.now(UTC), base_url=self._base_url)

    def get_voting(self, term: int, sitting: int, number: int) -> tuple[Vote, ...]:
        data = self._get_json(f"/sejm/term{term}/votings/{sitting}/{number}")
        return tuple(parse_vote(v) for v in data.get("votes") or ())

    def list_mps(self, term: int) -> tuple[Mp, ...]:
        data = self._get_json(f"/sejm/term{term}/MP")
        return tuple(parse_mp(m) for m in data) if isinstance(data, list) else ()

    def get_committee(self, term: int, code: str) -> Committee:
        data = self._get_json(f"/sejm/term{term}/committees/{quote(code)}")
        return parse_committee(data, term=term)

    def list_committee_sittings(self, term: int, code: str) -> tuple[CommitteeSitting, ...]:
        data = self._get_json(f"/sejm/term{term}/committees/{quote(code)}/sittings")
        if not isinstance(data, list):
            raise SejmApiError(f"Unexpected /committees/{code}/sittings payload")
        return tuple(parse_committee_sitting(item, code=code) for item in data)

    def list_sittings(self, term: int) -> tuple[SejmSitting, ...]:
        data = self._get_json(f"/sejm/term{term}/proceedings")
        if not isinstance(data, list):
            raise SejmApiError("Unexpected /proceedings payload")
        return tuple(parse_sitting(item) for item in data)

    def get_sitting(self, term: int, number: int) -> SejmSitting:
        return parse_sitting(self._get_json(f"/sejm/term{term}/proceedings/{number}"))

    def download(self, url: str, *, max_bytes: int | None = None) -> bytes:
        """Stream the attachment; stop as soon as `max_bytes` is exceeded."""
        response = self._request("GET", url, stream=True)
        try:
            chunks: list[bytes] = []
            received = 0
            for chunk in response.iter_bytes():
                received += len(chunk)
                if max_bytes is not None and received > max_bytes:
                    raise AttachmentTooLargeError(url, max_bytes)
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            response.close()

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------ internals

    def _get_json(self, path: str, params: dict[str, str | int] | None = None) -> Any:
        return self._request("GET", path, params=params).json()

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str | int] | None = None,
        allow_404: bool = False,
        stream: bool = False,
    ) -> httpx.Response:
        """One request with retries; `stream=True` returns before the body is read (caller
        closes the response)."""
        attempt = 0
        while True:
            attempt += 1
            try:
                request = self._client.build_request(method, url, params=params)
                response = self._client.send(request, stream=stream)
            except httpx.TransportError as exc:
                if attempt > self._max_retries:
                    raise SejmApiUnavailableError(
                        f"{method} {url} failed after {attempt} attempts: {exc}"
                    ) from exc
                self._wait(attempt, f"{method} {url}: {exc}")
                continue
            if response.status_code >= 500 or response.status_code == 429:
                response.close()
                if attempt <= self._max_retries:
                    self._wait(attempt, f"{method} {url}: HTTP {response.status_code}")
                    continue
                raise SejmApiUnavailableError(
                    f"{method} {url}: HTTP {response.status_code} after {attempt} attempts"
                )
            if response.status_code == 404 and allow_404:
                return response
            if response.status_code >= 400:
                response.close()
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
        "eli": item.get("ELI") or None,
        "display_address": item.get("displayAddress") or None,
        "isap_url": _link(item.get("links"), "isap"),
    }


def _link(links: Any, rel: str) -> str | None:
    if not isinstance(links, list):
        return None
    for li in links:
        if li.get("rel") == rel and li.get("href"):
            return str(li["href"])
    return None


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
        sitting=_int(item.get("sitting")),
        voting_number=_int(item.get("votingNumber")),
        date=_aware_datetime(item.get("date")),
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


def parse_mp(item: dict[str, Any]) -> Mp:
    return Mp(
        id=_int(item.get("id")) or 0,
        first_name=str(item.get("firstName") or ""),
        last_name=str(item.get("lastName") or ""),
        second_name=item.get("secondName") or None,
        accusative_name=item.get("accusativeName") or None,
        club=str(item.get("club") or "niez."),
    )


def parse_committee(item: dict[str, Any], *, term: int) -> Committee:
    return Committee(
        term=term, code=str(item["code"]), name=str(item.get("name") or item["code"]).strip()
    )


def parse_committee_sitting(item: dict[str, Any], *, code: str) -> CommitteeSitting:
    start = _datetime(item.get("startDateTime"))
    video = item.get("video") or ()
    player = next((v.get("playerLink") for v in video if v.get("playerLink")), None)
    return CommitteeSitting(
        code=str(item.get("code") or code),
        num=_int(item.get("num")) or 0,
        date=_date(item.get("date")) or (start.date() if start else date.min),
        start_time=start.time() if start else None,
        room=item.get("room") or None,
        status=str(item.get("status") or "PLANNED"),
        agenda=str(item.get("agenda") or ""),
        video_url=str(player) if player else None,
    )


def parse_sitting(item: dict[str, Any]) -> SejmSitting:
    return SejmSitting(
        number=_int(item.get("number")) or 0,
        dates=tuple(_date(d) for d in item.get("dates") or () if d),
        title=str(item.get("title") or "").strip(),
        current=bool(item.get("current", False)),
        agenda=str(item.get("agenda") or ""),
    )


def parse_process_detail(item: dict[str, Any]) -> ProcessDetail:
    return ProcessDetail(
        **_summary_fields(item),
        stages=tuple(parse_stage(s) for s in item.get("stages") or ()),
        title_final=item.get("titleFinal"),
    )


def parse_act(item: dict[str, Any], *, fetched_at: datetime, base_url: str) -> ActInfo:
    eli = str(item["ELI"])
    return ActInfo(
        eli=eli,
        display_address=str(item.get("displayAddress") or eli),
        title=str(item.get("title") or "").strip(),
        act_date=_date(item.get("announcementDate")),
        promulgation_date=_date(item.get("promulgation")),
        entry_into_force=_date(item.get("entryIntoForce")),
        in_force=item.get("inForce"),
        status=item.get("status"),
        text_pdf_url=f"{base_url}/eli/acts/{eli}/text.pdf" if item.get("textPDF") else None,
        isap_url=(
            f"https://isap.sejm.gov.pl/isap.nsf/DocDetails.xsp?id={item['address']}"
            if item.get("address")
            else None
        ),
        fetched_at=fetched_at,
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
