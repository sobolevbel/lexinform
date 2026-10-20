"""Where the text of a bill comes from, per source system.

`TextSources` picks the source for a bill; the analysis and the text prefilter only know the
`TextSource` port. Sejm prints come with a legislative process (fresh metadata and stages); an
RPW entry has no process at all, only its file on orka.sejm.gov.pl, and a register entry has no
text anywhere yet, so it is the one source the model still reads as metadata alone.
"""

import logging
import re
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlparse

from lexinform.document_versions import normative_name
from lexinform.errors import ServiceUnavailableError
from lexinform.models import (
    FULL_TEXT_SOURCES,
    ORKA_BASE_URL,
    AnalysisRecord,
    Bill,
    LocatedText,
    PrintInfo,
    ProcessDetail,
    RclDocument,
    RclProject,
    ReviewPurpose,
    ReviewSource,
    TextDocument,
    latest_text_document,
    submission_pdf_url,
    third_reading_kept_the_text,
)
from lexinform.models.rcl import text_rank, text_role
from lexinform.models.sejm import print_part_number
from lexinform.ports import ConsultationDocuments, SejmGateway, TextSource


def _rcl_link_conflict(project: RclProject, document: RclDocument) -> bool:
    url = urlparse(document.url)
    match = re.match(r"/docs/+\d+/(\d+)/", url.path)
    return (
        url.hostname == "legislacja.rcl.gov.pl"
        and match is not None
        and int(match[1]) != project.id
    )


log = logging.getLogger(__name__)


def fetch_print(gateway: SejmGateway, bill: Bill) -> PrintInfo | None:
    """The print, or None when the API has none for this number (a warning, not a failure)."""
    try:
        info = gateway.get_print(bill.term, bill.number)
    except ServiceUnavailableError:
        raise
    except Exception as exc:
        log.warning("print %s unavailable: %s", bill.number, exc)
        return None
    amendments = gateway.self_amendments(bill.term, bill.number)
    related = {extra.number: extra for extra in (*info.additional_prints, *amendments)}
    return info.model_copy(update={"additional_prints": tuple(related.values())})


def print_of_project(gateway: SejmGateway, project: RclProject, term: int) -> str | None:
    """The print number of a project that already went to the Sejm; None while it has not.
    The RM number the hand-over stage carries names the process (`ProcessSummary.rcl_num`)."""
    if project.print_number:
        return project.print_number
    if not project.rm_number:
        return None
    try:
        handover = next(
            (st.modified for st in reversed(project.stages) if st.is_sejm and st.reached), None
        )
        found = gateway.find_process_by_rcl_num(term, project.rm_number, since=handover)
    except ServiceUnavailableError:
        raise
    except Exception as exc:
        log.warning("looking up the print of %s failed: %s", project.rm_number, exc)
        return None
    return found.number if found is not None else None


def original_document(print_info: PrintInfo | None) -> TextDocument | None:
    if print_info is None or not print_info.pdf_candidates:
        return None
    candidates = print_info.pdf_candidates
    documents = tuple(
        TextDocument(
            url=pdf.url,
            kind="print",
            compare_versions=True,
            required_urls=tuple(
                other.url
                for other in candidates
                if other.url != pdf.url
                and print_part_number(other.name, print_info.number) is not None
            )
            if print_part_number(pdf.name, print_info.number) is not None
            else (),
            amendment_urls=tuple(
                a.url
                for extra in print_info.additional_prints
                if extra.title.strip().casefold().startswith("autopoprawka")
                for a in extra.pdf_candidates[:1]
            ),
            version_basis=(f"sejm/{print_info.term}/print/{print_info.number}",),
        )
        for pdf in candidates
    )
    return documents[0].model_copy(update={"alternatives": documents[1:]})


class SejmTextSource:
    """Numbered prints: the process (metadata, stages) and the print's main PDF."""

    def __init__(
        self, gateway: SejmGateway, *, consultation_documents: ConsultationDocuments | None = None
    ) -> None:
        self._gateway = gateway
        self._consultation_documents = consultation_documents

    def locate(self, bill: Bill) -> LocatedText:
        detail = self._gateway.get_process(bill.term, bill.number)
        document = latest_text_document(detail.stages)
        if document is None:
            document = original_document(fetch_print(self._gateway, bill))
        return LocatedText(summary=detail, stages=detail.stages, document=document)

    def supporting_documents(self, bill: Bill, purpose: ReviewPurpose) -> tuple[ReviewSource, ...]:
        sub = bill.submission
        if self._consultation_documents is None or sub is None or not sub.consultation_results:
            return ()
        return self._consultation_documents.read(sub)

    def newer(
        self, bill: Bill, detail: ProcessDetail, print_info: PrintInfo | None
    ) -> TextDocument | None:
        """The document to re-analyse from, if the bill text changed since the stored analysis:
        a committee report with the amended text, the text after the 3rd reading, or an updated
        print.

        The text after a third reading that adopted what it was given — no amendments, no
        minority motions — is the analysed text again, and is not read a second time.
        """
        record = bill.analysis
        if record is None:
            return None
        candidate = latest_text_document(detail.stages) or original_document(print_info)
        if candidate is None:
            return None
        if candidate.kind == "text_after3" and third_reading_kept_the_text(detail.stages):
            voted = latest_text_document(detail.stages, before_third_reading=True)
            voted = voted or original_document(print_info)
            if voted is not None and voted.url == record.source_url:
                return None
        if candidate.url != record.source_url:
            return candidate
        checked = record.source_checked_at or record.created_at
        if candidate.amendment_urls and print_info is not None:
            known = {part.url for part in record.selection.parts} if record.selection else set()
            if any(
                (
                    extra.document_date is not None
                    and extra.document_date > _as_utc(checked).date()
                    or extra.change_date is not None
                    and _as_utc(extra.change_date) > _as_utc(checked)
                )
                and any(
                    pdf.url in candidate.amendment_urls and pdf.url not in known
                    for pdf in extra.pdf_candidates
                )
                for extra in print_info.additional_prints
            ):
                return candidate
        if (
            print_info is not None
            and print_info.change_date is not None
            and _as_utc(print_info.change_date) > _as_utc(checked)
        ) or (
            candidate.kind in ("committee_report", "text_after3")
            and _as_utc(detail.change_date) > _as_utc(checked)
        ):
            return candidate
        return None


class MetadataOnlySource:
    """Bills without a readable text: the model works from title and description."""

    def locate(self, bill: Bill) -> LocatedText:
        return LocatedText()

    def supporting_documents(self, bill: Bill, purpose: ReviewPurpose) -> tuple[ReviewSource, ...]:
        return ()


class SubmissionTextSource:
    """Bills the Sejm has received but not numbered yet (`RPW/…`): the file on orka.sejm.gov.pl.

    There is no process to read at this stage — `/bills` is all the metadata there is — so this
    locates the document and nothing else. The address is built from the RPW number by the
    convention `submission_pdf_url` knows.
    """

    def __init__(
        self,
        base_url: str = ORKA_BASE_URL,
        *,
        consultation_documents: ConsultationDocuments | None = None,
    ) -> None:
        self._base_url = base_url
        self._consultation_documents = consultation_documents

    def locate(self, bill: Bill) -> LocatedText:
        url = submission_pdf_url(bill.term, bill.number, base_url=self._base_url)
        return LocatedText(document=TextDocument(url=url, kind="print"))

    def supporting_documents(self, bill: Bill, purpose: ReviewPurpose) -> tuple[ReviewSource, ...]:
        sub = bill.submission
        if self._consultation_documents is None or sub is None or not sub.consultation_results:
            return ()
        return self._consultation_documents.read(sub)


class SejmTextIdentity:
    def __init__(self, orka_base_url: str = ORKA_BASE_URL) -> None:
        self._orka_base_url = orka_base_url

    def same_text(self, bill: Bill, previous: AnalysisRecord, document: TextDocument) -> bool:
        """The original druk republishes its BILL submission: 524 of 524 term-10 pairs."""
        sub = bill.submission
        if sub is None or not sub.is_bill or document.kind != "print":
            return False
        if previous.text_source not in FULL_TEXT_SOURCES:
            return False
        original = submission_pdf_url(bill.term, sub.number, base_url=self._orka_base_url)
        return previous.source_url == original and document.url != original


class RclTextSource:
    """Government projects on RCL: the newest bill text with its uzasadnienie and OSR, as
    separate files (no network: the project page was read by discovery or tracking)."""

    def locate(self, bill: Bill) -> LocatedText:
        """The project's own documents; an empty result (nothing readable, a legacy .doc alone)
        sends the bill to a metadata-only analysis."""
        project = bill.rcl
        if project is None:
            return LocatedText()
        documents = project.text_documents()
        if "bill" not in documents:
            return LocatedText()
        main = documents["bill"]
        stage = next(
            st
            for st in reversed(project.reached_stages)
            if any(d.id == main.id for d in project.stage_documents(st))
        )
        available = [d for d in project.stage_documents(stage) if d.readable]

        def candidate(doc: RclDocument) -> TextDocument:
            source_stage = next(
                st
                for st in reversed(project.reached_stages)
                if any(d.id == doc.id for d in project.stage_documents(st))
            )
            source_documents = [d for d in project.stage_documents(source_stage) if d.readable]
            extras = tuple(
                min(
                    matches,
                    key=lambda d: (
                        "nowy tekst" not in d.name.lower(),
                        text_rank(d.name, d.extension),
                        d.url,
                    ),
                ).url
                for role in ("justification", "osr")
                if (
                    matches := [
                        d
                        for d in source_documents
                        if text_role(d.name) == role
                        and d.id != doc.id
                        and d.created == doc.created
                        and not _rcl_link_conflict(project, d)
                    ]
                )
            )
            return TextDocument(
                url=doc.url,
                kind="rcl",
                compare_versions=True,
                normative_urls=tuple(
                    d.url
                    for d in source_documents
                    if d.id != doc.id
                    and d.created == doc.created
                    and normative_name(d.name)
                    and not _rcl_link_conflict(project, d)
                ),
                extra_urls=extras,
                amendment_urls=tuple(
                    dict.fromkeys(
                        d.url
                        for st in project.reached_stages
                        for d in project.stage_documents(st)
                        if d.readable
                        and d.id != doc.id
                        and "autopoprawka" in d.name.casefold()
                        and not _rcl_link_conflict(project, d)
                        and (doc.created is None or d.created is None or d.created >= doc.created)
                    )
                ),
                version_basis=(f"rcl/{project.id}/stage/{source_stage.id}", doc.name),
                official_link_conflict=_rcl_link_conflict(project, doc),
            )

        alternatives = tuple(
            candidate(d)
            for d in sorted(available, key=lambda d: d.url)
            if d.id != main.id
            and (text_role(d.name) == "bill" or "autopoprawka" in d.name.casefold())
        )
        if any(doc.id == main.id for doc in stage.loose_documents or ()):
            filed = project.model_copy(
                update={
                    "stages": tuple(
                        st.model_copy(update={"loose_documents": ()}) for st in project.stages
                    )
                }
            ).text_documents()
            previous = filed.get("bill")
            if previous is not None and previous.url not in {
                main.url,
                *(doc.url for doc in alternatives),
            }:
                alternatives = (*alternatives, candidate(previous))
        return LocatedText(
            document=candidate(main).model_copy(update={"alternatives": alternatives})
        )

    def supporting_documents(self, bill: Bill, purpose: ReviewPurpose) -> tuple[ReviewSource, ...]:
        project = bill.rcl
        if project is None:
            return ()
        stages = [stage for stage in project.stages if stage.is_consultation and stage.reached]
        responses = [
            document
            for stage in stages
            for document in stage.documents("response")
            if document.readable
        ]
        positions = [
            document
            for stage in stages
            for document in stage.documents("positions")
            if document.readable
        ]
        documents = responses or positions
        role: Literal["response", "position"] = "response" if responses else "position"
        result = tuple(
            ReviewSource(
                identity=f"rcl/{document.id}",
                document=TextDocument(url=document.url, kind="rcl"),
                role=role,
                author=document.author or "",
            )
            for document in documents
        )
        current = project.text_documents().get("bill")
        previous = bill.analysis
        if current is not None:
            result += (
                ReviewSource(
                    identity=f"rcl/{current.id}",
                    document=TextDocument(url=current.url, kind="rcl"),
                    role="project_text",
                ),
            )
        saved_review = bill.consultation_review
        baseline = (
            next(
                (doc for doc in saved_review.context.documents if doc.role == "comparison_text"),
                None,
            )
            if saved_review
            else None
        )
        if baseline is not None:
            snapshot = baseline.model_copy(update={"role": "comparison_text"})
            result += (
                ReviewSource(
                    identity=snapshot.identity,
                    document=TextDocument(url=snapshot.url, kind="rcl"),
                    role="comparison_text",
                    snapshot=snapshot,
                ),
            )
        elif previous is not None and previous.source_url and previous.text_sha256:
            result += (
                ReviewSource(
                    identity="comparison/" + previous.text_sha256,
                    document=TextDocument(url=previous.source_url, kind="rcl"),
                    role="comparison_text",
                    expected_digest=previous.text_sha256,
                ),
            )
        return result


class TextSources:
    """Routes a bill to the source of its text."""

    def __init__(
        self,
        sejm: TextSource,
        *,
        rcl: TextSource | None = None,
        submissions: TextSource | None = None,
        metadata: TextSource | None = None,
    ) -> None:
        self._sejm = sejm
        self._metadata = metadata or MetadataOnlySource()
        self._rcl = rcl or self._metadata
        self._submissions = submissions or self._metadata

    def locate(self, bill: Bill) -> LocatedText:
        if bill.is_rcl:
            return self._rcl.locate(bill)
        if bill.has_process:
            return self._sejm.locate(bill)
        if bill.is_pre_print:
            return self._submissions.locate(bill)
        return self._metadata.locate(bill)

    def supporting_documents(self, bill: Bill, purpose: ReviewPurpose) -> tuple[ReviewSource, ...]:
        if purpose == "act_application":
            act = bill.act
            if act is None or act.text_pdf_url is None:
                return ()
            return (
                ReviewSource(
                    identity=act.eli,
                    document=TextDocument(url=act.text_pdf_url, kind="final_act"),
                    role="final_act",
                ),
            )
        source = self._rcl if bill.is_rcl else self._sejm if bill.has_process else self._submissions
        return source.supporting_documents(bill, purpose)


def _as_utc(value: datetime) -> datetime:
    """Naive datetimes in our own records are UTC; API timestamps arrive already aware."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
