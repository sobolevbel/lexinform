"""Where the text of a bill comes from, per source system.

`TextSources` picks the source for a bill; the analysis and the text prefilter only know the
`TextSource` port. Sejm prints come with a legislative process (fresh metadata and stages);
RPW entries have nothing readable (the PDF is behind a bot wall), so the model sees metadata only.
"""

import logging
from datetime import UTC, datetime

from lexinform.models import (
    Bill,
    LocatedText,
    PrintInfo,
    ProcessDetail,
    TextDocument,
    latest_text_document,
)
from lexinform.ports import SejmGateway, TextSource

log = logging.getLogger(__name__)


def fetch_print(gateway: SejmGateway, bill: Bill) -> PrintInfo | None:
    """The print, or None when the API has none for this number (a warning, not a failure)."""
    try:
        return gateway.get_print(bill.term, bill.number)
    except Exception as exc:
        log.warning("print %s unavailable: %s", bill.number, exc)
        return None


def original_document(print_info: PrintInfo | None) -> TextDocument | None:
    pdf = print_info.main_pdf if print_info else None
    return TextDocument(url=pdf.url, kind="print") if pdf else None


class SejmTextSource:
    """Numbered prints: the process (metadata, stages) and the print's main PDF."""

    def __init__(self, gateway: SejmGateway) -> None:
        self._gateway = gateway

    def locate(self, bill: Bill) -> LocatedText:
        detail = self._gateway.get_process(bill.term, bill.number)
        document = original_document(fetch_print(self._gateway, bill))
        return LocatedText(summary=detail, stages=detail.stages, document=document)

    def newer(
        self, bill: Bill, detail: ProcessDetail, print_info: PrintInfo | None
    ) -> TextDocument | None:
        """The document to re-analyse from, if the bill text changed since the stored analysis:
        a committee report with the amended text, the text after the 3rd reading, or an updated
        print."""
        record = bill.analysis
        if record is None:
            return None
        candidate = latest_text_document(detail.stages) or original_document(print_info)
        if candidate is None:
            return None
        if candidate.url != record.source_url:
            return candidate
        if (
            candidate.kind == "print"
            and print_info is not None
            and print_info.change_date is not None
            and _as_utc(print_info.change_date) > _as_utc(record.created_at)
        ):
            return candidate
        return None


class MetadataOnlySource:
    """Bills without a readable text: the model works from title and description."""

    def locate(self, bill: Bill) -> LocatedText:
        return LocatedText()


class RclTextSource:
    """Government projects on RCL: the newest bill text with its uzasadnienie and OSR, as
    separate files (no network: the project page was read by discovery or tracking)."""

    def locate(self, bill: Bill) -> LocatedText:
        project = bill.rcl
        if project is None:
            return LocatedText()
        documents = project.text_documents()
        if "bill" not in documents:
            return LocatedText()  # nothing readable (legacy .doc only): metadata
        extras = tuple(
            documents[role].url for role in ("justification", "osr") if role in documents
        )
        return LocatedText(
            document=TextDocument(url=documents["bill"].url, kind="rcl", extra_urls=extras)
        )


class TextSources:
    """Routes a bill to the source of its text."""

    def __init__(
        self,
        sejm: TextSource,
        *,
        rcl: TextSource | None = None,
        metadata: TextSource | None = None,
    ) -> None:
        self._sejm = sejm
        self._metadata = metadata or MetadataOnlySource()
        self._rcl = rcl or self._metadata

    def locate(self, bill: Bill) -> LocatedText:
        if bill.is_rcl:
            return self._rcl.locate(bill)
        source = self._sejm if bill.has_process else self._metadata
        return source.locate(bill)


def _as_utc(value: datetime) -> datetime:
    """Naive datetimes in our own records are UTC; API timestamps arrive already aware."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
