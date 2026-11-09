import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from lexinform.models.documents import DocumentPart
from lexinform.models.rcl import RclDocument, RclProject, rcl_num_key
from lexinform.models.sejm import TextDocument
from lexinform.rcl_letters import parse_letter
from lexinform.sections import PAGE_BREAK, document_kind

_RM_NUMBER = re.compile(r"\bRM\s*[-–]\s*([0-9]+)\s*[-–]\s*([0-9]+)\s*[-–]\s*([0-9]+)\b", re.I)


def _rm_numbers(text: str) -> set[str]:
    return {"-".join(("RM", *match.groups())) for match in _RM_NUMBER.finditer(text)}


@dataclass(frozen=True)
class RclResubmissionSources:
    project_code: str
    rm_key: str
    later: RclDocument
    older: RclDocument
    amendment: RclDocument
    secretary: RclDocument


def resubmission_sources(
    project: RclProject, requested: TextDocument
) -> RclResubmissionSources | None:
    reached = project.reached_stages
    rm_key = rcl_num_key(project.rm_number or "")
    project_code = project.wykaz_number
    if (
        not reached
        or reached[-1].name != "Rada Ministrów"
        or not rm_key
        or not project_code
        or requested.kind != "rcl"
        or not requested.compare_versions
        or len(requested.alternatives) != 2
    ):
        return None
    stage = reached[-1]
    older, amendment = requested.alternatives
    candidates = (requested, older, amendment)
    if (
        older.amendment_urls != (amendment.url,)
        or requested.amendment_urls
        or amendment.amendment_urls
        or any(
            item.kind != "rcl"
            or item.official_link_conflict
            or item.required_urls
            or item.normative_urls
            or item.extra_urls
            or len(item.version_basis) != 2
            or item.version_basis[0] != f"rcl/{project.id}/stage/{stage.id}"
            for item in candidates
        )
        or older.alternatives
        or amendment.alternatives
    ):
        return None
    folders = tuple(folder for folder in stage.folders if folder.kind == "project")
    if len(folders) != 1:
        return None
    (folder,) = folders
    if len(folder.documents) != 4:
        return None
    by_url = {item.url: item for item in folder.documents}
    later_doc, older_doc, amendment_doc = (by_url.get(item.url) for item in candidates)
    if later_doc is None or older_doc is None or amendment_doc is None:
        return None
    secretary_docs = tuple(
        item for item in folder.documents if item.name.casefold() == "pismo srm.pdf"
    )
    if len(secretary_docs) != 1:
        return None
    (secretary,) = secretary_docs
    expected_names = (
        "nowy tekst projektu ustawy ",
        "projekt ustawy ",
        "autopoprawka do projektu ustawy ",
    )
    bill_docs = (later_doc, older_doc, amendment_doc)
    suffixes = {
        item.name.casefold().removeprefix(prefix)
        for item, prefix in zip(bill_docs, expected_names, strict=True)
    }
    if (
        len(suffixes) != 1
        or later_doc.created is None
        or older_doc.created is None
        or older_doc.created != amendment_doc.created
        or later_doc.created != secretary.created
        or later_doc.created <= older_doc.created
        or any(
            item.name != candidate.version_basis[1]
            or not item.name.casefold().startswith(prefix)
            or f"({project_code})" not in item.name
            or _rm_numbers(item.name) != {rm_key}
            for item, candidate, prefix in zip(bill_docs, candidates, expected_names, strict=True)
        )
    ):
        return None
    for item in (*bill_docs, secretary):
        parsed = urlsplit(item.url)
        extension = "pdf" if item == secretary else "zip"
        if (
            parsed.scheme != "https"
            or parsed.netloc != "legislacja.rcl.gov.pl"
            or parsed.query
            or parsed.fragment
            or not re.fullmatch(
                rf"/docs/+2/{project.id}/{stage.id}/{folder.id}/dokument{item.id}\.{extension}",
                parsed.path,
            )
        ):
            return None
    return RclResubmissionSources(
        project_code, rm_key, later_doc, older_doc, amendment_doc, secretary
    )


@dataclass(frozen=True)
class RclResubmissionProof:
    bill: DocumentPart
    cover: DocumentPart
    amendment: DocumentPart
    secretary: DocumentPart


def resubmission_proof(
    sources: RclResubmissionSources,
    *,
    bill: DocumentPart,
    cover: DocumentPart,
    amendment: DocumentPart,
    secretary: DocumentPart,
) -> RclResubmissionProof | None:
    if (
        bill.role != "bill"
        or len(bill.members) != 1
        or not bill.members[0].casefold().endswith(".docx")
        or document_kind(bill.text) != "bill"
        or bill.url != sources.later.url
        or cover.url != sources.later.url
        or not bill.container_sha256
        or cover.container_sha256 != bill.container_sha256
        or amendment.url != sources.amendment.url
        or not amendment.container_sha256
        or secretary.url != sources.secretary.url
        or secretary.members
        or any(
            not part.sha256
            or part.losses
            or not part.text.strip()
            or hashlib.sha256(part.text.encode()).hexdigest() != part.text_sha256
            for part in (bill, cover, amendment, secretary)
        )
    ):
        return None
    if any(
        len(part.members) != 1 or not part.members[0].casefold().endswith(".pdf")
        for part in (cover, amendment)
    ):
        return None
    cover_pages = cover.text.split(PAGE_BREAK)
    if (
        cover.pages != 3
        or len(cover_pages) != 3
        or any(not page.strip() for page in cover_pages)
        or amendment.pages != 1
        or secretary.pages != 1
    ):
        return None
    old_date = sources.amendment.created
    published_date = sources.later.created
    cover_date = parse_letter(cover_pages[0]).letter_date
    secretary_date = parse_letter(secretary.text).letter_date
    if (
        old_date is None
        or published_date is None
        or cover_date is None
        or secretary_date is None
        or parse_letter(amendment.text).letter_date != old_date
        or not old_date < cover_date < secretary_date <= published_date
    ):
        return None
    first, second, attachments = (" ".join(page.casefold().split()) for page in cover_pages)
    amendment_text, secretary_text = (
        " ".join(part.text.casefold().split()) for part in (amendment, secretary)
    )
    code = sources.project_code.casefold()
    incorporated = re.search(
        r"uwzględniono autopoprawkę polegającą na rezygnacji z nowelizacji "
        r"(ust\. \d+ w art\. \d+[a-z]*);",
        first,
    )
    if (
        incorporated is None
        or f"rezygnacji z nowelizacji {incorporated[1]}" not in amendment_text
        or "autopoprawka art." not in amendment_text
        or f"(nr {code} w wykazie prac legislacyjnych rady ministrów)" not in first
        or "w załączeniu przekazuję poprawiony projekt ustawy" not in first
        or "o jego rozpatrzenie na najbliższym posiedzeniu rady ministrów." not in first
        or "dyskusję i ustalenia poczynione na radzie ministrów z dnia" not in first
        or "dokument podpisany elektronicznie" not in second
        or "1. projekt ustawy" not in attachments
        or f"({code})" not in attachments
        or f"(nr {code}," not in amendment_text
        or _rm_numbers(amendment_text) != {sources.rm_key}
        or f"({code})" not in secretary_text
        or _rm_numbers(secretary_text) != {sources.rm_key}
        or "do rozpatrzenia przez radę ministrów nowego tekstu projektu ustawy"
        not in secretary_text
        or "podpisano kwalifikowanym podpisem elektronicznym" not in secretary_text
    ):
        return None
    return RclResubmissionProof(bill, cover, amendment, secretary)
