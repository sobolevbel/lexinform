import hashlib
import json
import re

from lexinform.models import Bill, PrintInfo, ReviewSource, TextDocument, flatten_stages
from lexinform.models.reviews import DocumentRole

COMMISSION_ROLES: set[DocumentRole] = {
    "commission_position",
    "commission_response",
    "commission_minutes",
    "commission_report",
}


def commission_sources(bill: Bill) -> tuple[ReviewSource, ...]:
    project = bill.rcl
    if project is None:
        return ()
    sources: dict[int, ReviewSource] = {}
    for stage in project.reached_stages:
        if not stage.is_review_body:
            continue
        documents = [(doc, folder.kind) for folder in stage.folders for doc in folder.documents] + [
            (doc, "other") for doc in stage.loose_documents or ()
        ]
        for document, folder in documents:
            name = document.name.casefold()
            if not document.readable or re.search(r"zaproszen|klauzul|rozdzielnik", name):
                continue
            if re.search(r"raport.*konsult|za[łl][aą]cznik.*osr", name):
                continue
            role: DocumentRole
            if re.search(r"protok[oó][łl].*rozb(?:[._\s]|ie[zż]no)", name):
                role = "commission_response"
            elif re.search(r"protok[oó][łl]", name) and not re.search(r"za[łl][aą]cznik", name):
                role = "commission_minutes"
            elif folder == "response" or re.search(r"odpowied|odniesieni|ze stanowiskiem", name):
                role = "commission_response"
            elif folder == "positions" or (
                re.search(r"uwag|stanowisk", name) and not re.search(r"projekt.*po[ _]+uwag", name)
            ):
                role = "commission_position"
            elif re.search(r"opini|sprawozdani|rozstrzygni|ustale[nń]", name):
                role = "commission_report"
            else:
                continue
            sources[document.id] = ReviewSource(
                identity=f"rcl/{document.id}",
                role=role,
                author=document.author or "",
                body=stage.name,
                forum="government",
                revision=str(stage.modified or ""),
                document=TextDocument(url=document.url, kind="rcl"),
            )
    return tuple(sources.values())


def parliamentary_commission_sources(bill: Bill) -> tuple[ReviewSource, ...]:
    sources: dict[str, ReviewSource] = {}
    for stage in flatten_stages(bill.stages):
        if stage.stage_type != "CommitteeReport" or not stage.report_file:
            continue
        url = stage.report_file
        name = stage.committee_name or stage.committee_code or "Komisja Sejmu"
        previous = sources.get(url)
        body = "; ".join(dict.fromkeys([*(previous.body.split("; ") if previous else []), name]))
        sources[url] = ReviewSource(
            identity=url,
            role="commission_report",
            body=body,
            forum="sejm",
            revision=str(stage.date or ""),
            document=TextDocument(url=url, kind="committee_amendments"),
        )
    for source in bill.committee_documents or ():
        sources.setdefault(source.document.url, source)
    if bill.senate is not None:
        for document in bill.senate.review_documents:
            sources[document.url] = ReviewSource(
                identity=document.url,
                role=document.role,
                body=document.body,
                forum="senate",
                revision=str(document.date or ""),
                document=TextDocument(url=document.url, kind="committee_amendments"),
            )
    return tuple(sources.values())


def filed_commission_sources(info: PrintInfo) -> tuple[ReviewSource, ...]:
    sources: list[ReviewSource] = []
    for extra in info.additional_prints:
        if extra.process_prints and info.number not in extra.process_prints:
            continue
        title = " ".join(extra.title.casefold().split())
        if not re.match(
            r"(?:opinia|stanowisko|uwagi|(?:dodatkowe )?sprawozdanie) "
            r"(?:wspóln\w* )?(?:pod)?komisj",
            title,
        ):
            continue
        pdf = extra.main_pdf
        if pdf is None:
            continue
        sources.append(
            ReviewSource(
                identity=f"sejm/{info.term}/filed/{extra.number}",
                role="commission_report" if "sprawozdanie" in title else "commission_position",
                document=TextDocument(url=pdf.url, kind="committee_amendments"),
                body=extra.title,
                forum="sejm",
                revision=str(extra.change_date or extra.document_date or ""),
            )
        )
    return tuple(sources)


def legacy_filed_commission_baseline(
    bill: Bill, sources: tuple[ReviewSource, ...]
) -> tuple[ReviewSource, ...]:
    if bill.committee_documents is not None:
        return ()
    prefix = f"sejm/{bill.term}/filed/"
    return tuple(
        source
        for source in sources
        if source.identity.startswith(prefix)
        and (
            bill.seen_supplements is None
            or source.identity.removeprefix(prefix) in bill.seen_supplements
        )
    )


def commission_source_fingerprint(sources: tuple[ReviewSource, ...]) -> str:
    facts = [
        source.model_dump(mode="json", exclude={"snapshot"})
        for source in sources
        if source.role != "comparison_text"
    ]
    facts.sort(key=lambda value: json.dumps(value, sort_keys=True))
    return hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()


def commission_text_matches(role: DocumentRole, text: str) -> bool:
    if role == "commission_minutes":
        return bool(
            re.search(r"(?im)^\s*protok[oó][łl]\b", text[:3000])
            and re.search(r"komisj|komitet|rad\w*\s+legislacyjn", text[:6000], re.I)
        )
    if role == "commission_report":
        return bool(
            re.search(
                r"sprawozdani|opini|ustale[nń]|rozstrzygni|\bs\s*p\s*r\s*a\s*w\s*o\s*z\s*d\s*a\s*n\s*i\s*e\b",
                text[:6000],
                re.I,
            )
        )
    if role in {"commission_position", "commission_response"}:
        return bool(
            re.search(r"\buwag\w*|odniesieni|opini", text, re.I)
            and re.search(r"\bart\.?\s*\d+|§\s*\d+", text, re.I)
        )
    return True
