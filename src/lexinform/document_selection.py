import re
from typing import Literal

from lexinform.document_versions import draft_version_span
from lexinform.models.documents import DocumentFact, DocumentPart, DocumentSelection
from lexinform.sections import document_kind, trim_print


def bill_selection(
    selection: DocumentSelection, *, source_basis: tuple[str, ...]
) -> DocumentSelection:
    parts = tuple(_part_role(part) for part in selection.parts)
    facts: list[DocumentFact] = []
    for index, part in enumerate(parts):
        if part.role != "bill":
            continue
        if source_basis and part.url:
            facts.append(
                DocumentFact(
                    kind="official_link",
                    value=source_basis[0],
                    provenance="source_relation",
                    part_index=index,
                    source_url=part.url,
                    text_sha256=part.text_sha256,
                    block_id=f"part:{index}",
                    start=0,
                    end=0,
                    quote="",
                )
            )
        article = re.search(r"Art\.\s*1\b", part.text)
        if document_kind(part.text) == "bill" and article:
            facts.append(_span_fact("bill_structure", "bill", index, part, 0, article.end()))
        version = draft_version_span(part.text)
        if version:
            value, start, end = version
            facts.append(_span_fact("draft_date", value.isoformat(), index, part, start, end))
        commencement = re.search(r"ustawa\s+wchodzi\s+w\s+życie", part.text, re.I)
        annex_required = "załącznik" in trim_print(part.text).text.casefold()
        annex_present = any(p.role == "normative_annex" for p in parts)
        if (
            commencement
            and not any(p.losses for p in parts)
            and (not annex_required or annex_present)
        ):
            facts.append(
                _span_fact(
                    "coverage",
                    "body_through_commencement",
                    index,
                    part,
                    commencement.start(),
                    commencement.end(),
                )
            )
    required = {"official_link", "bill_structure", "draft_date", "coverage"}
    confirmed = any(
        {fact.kind for fact in facts if fact.part_index == index} >= required
        for index in range(len(parts))
    )
    limitations: list[str] = []
    if any(part.role in ("osr", "justification") for part in parts):
        limitations.append("support_applicability_unknown")
    if not confirmed:
        limitations.append("missing_evidence")
    return DocumentSelection.model_validate(
        {
            **selection.model_dump(),
            "parts": parts,
            "facts": facts,
            "state": "selected" if confirmed else "unverified",
            "limitations": limitations,
        }
    )


def _part_role(part: DocumentPart) -> DocumentPart:
    kind = document_kind(part.text)
    if part.role == "unknown" and kind in ("bill", "justification", "osr"):
        return part.model_copy(update={"role": kind})
    return part


def _span_fact(
    kind: Literal["bill_structure", "draft_date", "coverage"],
    value: str,
    index: int,
    part: DocumentPart,
    start: int,
    end: int,
) -> DocumentFact:
    return DocumentFact(
        kind=kind,
        value=value,
        provenance="extracted_text",
        part_index=index,
        source_url=part.url,
        text_sha256=part.text_sha256,
        block_id=f"part:{index}",
        start=start,
        end=end,
        quote=part.text[start:end],
    )
