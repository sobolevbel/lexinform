import re

from lexinform.models import Bill, ReviewSource, TextDocument
from lexinform.models.reviews import DocumentRole


def commission_sources(bill: Bill) -> tuple[ReviewSource, ...]:
    project = bill.rcl
    if project is None:
        return ()
    stages = [stage for stage in project.reached_stages if stage.is_legal_commission]
    if not stages:
        return ()
    stage = stages[-1]
    documents = [(doc, folder.kind) for folder in stage.folders for doc in folder.documents] + [
        (doc, "other") for doc in stage.loose_documents or ()
    ]
    sources: dict[int, ReviewSource] = {}
    for document, folder in documents:
        name = document.name.casefold()
        if not document.readable or re.search(r"zaproszen|klauzul|rozdzielnik", name):
            continue
        if re.search(r"raport.*konsult|za[łl][aą]cznik.*osr", name):
            continue
        role: DocumentRole
        if re.search(r"protok[oó][łl]", name) and not re.search(r"za[łl][aą]cznik", name):
            role = "commission_minutes"
        elif folder == "response" or re.search(r"odpowied|odniesieni|ze stanowiskiem", name):
            role = "commission_response"
        elif re.search(r"uwag|stanowisk", name) and not re.search(r"projekt.*po[ _]+uwag", name):
            role = "commission_position"
        else:
            continue
        sources[document.id] = ReviewSource(
            identity=f"rcl/{document.id}",
            role=role,
            author=document.author or "",
            document=TextDocument(url=document.url, kind="rcl"),
        )
    return tuple(sources.values())


def commission_text_matches(role: DocumentRole, text: str) -> bool:
    if role == "commission_minutes":
        return bool(
            re.search(r"(?im)^\s*protok[oó][łl]\b", text[:3000])
            and re.search(r"komisj\w*\s+prawnicz", text[:6000], re.I)
        )
    if role in {"commission_position", "commission_response"}:
        return bool(
            re.search(r"\buwag\w*|odniesieni", text, re.I)
            and re.search(r"\bart\.?\s*\d+|§\s*\d+", text, re.I)
        )
    return True
