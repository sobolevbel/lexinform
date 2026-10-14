from urllib.parse import urlencode, urljoin, urlparse

from bs4 import BeautifulSoup

from lexinform.errors import SejmApiUnavailableError
from lexinform.models import BillSubmission, ReviewSource, TextDocument
from lexinform.ports import Downloader


class SejmConsultationDocuments:
    def __init__(self, download: Downloader) -> None:
        self._download = download

    def read(self, submission: BillSubmission) -> tuple[ReviewSource, ...]:
        page = f"https://www.sejm.gov.pl/Sejm{submission.term}.nsf/agent.xsp?" + urlencode(
            {"symbol": "KONSULTACJE_WYNIKI", "NrProjektu": submission.number}
        )
        body = self._download(page, max_bytes=2_000_000).decode("utf-8", errors="replace")
        return parse_consultation_documents(body, submission=submission, page=page)


def parse_consultation_documents(
    body: str, *, submission: BillSubmission, page: str
) -> tuple[ReviewSource, ...]:
    soup = BeautifulSoup(body, "html.parser")
    text = soup.get_text(" ", strip=True)
    if any(
        marker in text.casefold()
        for marker in ("request rejected", "human verification", "your support id")
    ):
        raise SejmApiUnavailableError("consultation reports page is blocked")
    if submission.number not in text:
        return ()
    documents: list[ReviewSource] = []
    for anchor in soup.select("a[href]"):
        label = anchor.get_text(" ", strip=True).casefold()
        if "raport" not in label or "statystycz" in label:
            continue
        href = anchor.get("href")
        if not isinstance(href, str) or not href or href.startswith("#"):
            continue
        url = urljoin(page, href)
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in {
            "www.sejm.gov.pl",
            "sejm.gov.pl",
            "orka.sejm.gov.pl",
            "api.sejm.gov.pl",
        }:
            continue
        documents.append(
            ReviewSource(
                identity=url,
                document=TextDocument(url=url, kind="consultation_position"),
                role="position",
            )
        )
    return tuple(documents)
