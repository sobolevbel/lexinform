import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from lexinform.document_facts import span_fact
from lexinform.document_versions import draft_version, draft_version_span
from lexinform.models.documents import DocumentFact, DocumentLimitation, DocumentLink, DocumentPart
from lexinform.sections import trim_print

_ANNEX = re.compile(
    r"załącznik(?:a|u|iem|owi)?(?:\s+nr\s+(\d+))?\s+do\s+"
    r"(?:(niniejszej|projektu)\s+)?ustawy\b(?!\s+z\s+dnia)",
    re.I,
)
_FOREIGN_ANNEX = re.compile(r"załącznik.{0,40}do\s+(?:rozporządzenia|ustawy\s+z\s+dnia)", re.I)
_ARTICLE = re.compile(r"\bArt\.\s*(\d+)\s*[.)]", re.I)
_SUPPORT_NORM = re.compile(
    r"\bart\.?\s*(\d+)(?:\s+ust\.?\s*(\d+))?\s+projektu(?:\s+ustawy)?\b[^\n;]{0,160}",
    re.I,
)
_AMOUNT = re.compile(r"(?<![\d.,])(\d+(?:[ \u00a0]\d{3})*(?:[,.]\d{1,2})?)\s*(zł|PLN)\b", re.I)

_QUANTITY = re.compile(r"\b(opłat\w*|świadczeni\w*|wynagrodzeni\w*|kwot\w*|kara|kary|karę)\b", re.I)


def _quantity(text: str) -> str:
    if re.search(
        r"nie\s+(?:mniej|więcej)|co\s+najmniej|najwyżej|maksymal|minimal|\b(?:do|od)\s*$",
        text[-100:],
        re.I,
    ):
        return ""
    matches = list(_QUANTITY.finditer(text[-100:]))
    if not matches:
        return ""
    value = matches[-1][1].casefold()
    return next(
        stem
        for stem in ("opłat", "świadczeni", "wynagrodzeni", "kwot", "kar")
        if value.startswith(stem)
    )


@dataclass(frozen=True)
class BundleEvidence:
    links: tuple[DocumentLink, ...]
    facts: tuple[DocumentFact, ...]
    limitations: tuple[DocumentLimitation, ...]
    annexes_complete: bool


def inspect_bundle(parts: tuple[DocumentPart, ...], *, fact_offset: int) -> BundleEvidence:
    facts: list[DocumentFact] = []
    links: list[DocumentLink] = []
    limitations: list[DocumentLimitation] = []
    complete = True
    mains = [index for index, part in enumerate(parts) if part.role == "bill"]
    for main_index in mains:
        main = parts[main_index]
        normative = trim_print(main.text).text
        normative = re.split(
            r"(?:^|[\n\f])\s*(?:UZASADNIENIE|OCENA SKUTKÓW REGULACJI|Nazwa projektu)\b",
            normative,
            maxsplit=1,
            flags=re.I,
        )[0]
        required: dict[str, re.Match[str]] = {}
        known: list[tuple[int, int]] = []
        for match in _ANNEX.finditer(normative):
            if match[2]:
                required.setdefault(match[1] or "1", match)
                known.append(match.span())
            elif not normative[
                max(
                    normative.rfind("\n", 0, match.start()), normative.rfind("\f", 0, match.start())
                )
                + 1 : match.start()
            ].strip():
                known.append(match.span())
        known += [match.span() for match in _FOREIGN_ANNEX.finditer(normative)]
        mentions = list(re.finditer(r"\bzałącznik[\w]*\b", normative, re.I))
        if any(
            not any(start <= mention.start() < end for start, end in known) for mention in mentions
        ):
            complete = False
            limitations.append("annex_link_unknown")
        found: set[str] = set()
        available_numbers: set[str] = set()
        unlabelled_annex = False
        for index, part in enumerate(parts):
            if part.role != "normative_annex":
                continue
            label = re.search(r"(?im)^\s*Załącznik(?:\s+nr\s+(\d+))?", part.text[:1200])
            if label:
                available_numbers.add(label[1] or "1")
            else:
                unlabelled_annex = True
            heading = _ANNEX.search(part.text[:1200])
            foreign = _FOREIGN_ANNEX.search(part.text[:1200])
            status: Literal["unknown", "confirmed", "conflicting"] = "unknown"
            evidence: list[int] = []
            if heading:
                number = heading[1] or "1"
                if heading[2] or number in required:
                    status = "confirmed"
                    found.add(number)
                    evidence.append(fact_offset + len(facts))
                    facts.append(span_fact("annex_heading", number, index, part, *heading.span()))
                    if number in required:
                        reference = required[number]
                        source = main.text.find(reference[0])
                        if source >= 0:
                            evidence.append(fact_offset + len(facts))
                            facts.append(
                                span_fact(
                                    "annex_reference",
                                    number,
                                    main_index,
                                    main,
                                    source,
                                    source + len(reference[0]),
                                )
                            )
            elif foreign and "rozporządzenia" in foreign[0].casefold():
                status = "conflicting"
                limitations.append("foreign_annex")
                evidence.append(fact_offset + len(facts))
                facts.append(span_fact("annex_heading", "foreign", index, part, *foreign.span()))
            if status == "unknown":
                complete = False
                limitations.append("annex_link_unknown")
            links.append(
                DocumentLink(
                    kind="normative_annex",
                    source_part=index,
                    target_part=main_index,
                    status=status,
                    evidence=tuple(evidence),
                )
            )
        for number in required:
            available_numbers.update(
                match[1] or "1"
                for index, content in enumerate(parts)
                if index == main_index or content.role == "normative_continuation"
                for match in re.finditer(
                    r"(?:^|[\n\f])\s*Załącznik(?:\s+nr\s+(\d+))?\b", content.text, re.I
                )
            )
            inline = next(
                (
                    (index, match)
                    for index, content in enumerate(parts)
                    if index == main_index or content.role == "normative_continuation"
                    for match in re.finditer(
                        r"(?i)(?:^|[\n\f])\s*Załącznik(?:\s+nr\s+(\d+))?\s+do\s+"
                        r"(?:(?:niniejszej|projektu)\s+)?ustawy\b(?!\s+z\s+dnia\s+\d)",
                        content.text,
                    )
                    if (match[1] or "1") == number
                ),
                None,
            )
            if inline is not None:
                index, heading = inline
                evidence_index = fact_offset + len(facts)
                facts.append(
                    span_fact("annex_heading", number, index, parts[index], *heading.span())
                )
                links.append(
                    DocumentLink(
                        kind="normative_annex",
                        source_part=index,
                        target_part=main_index,
                        status="confirmed",
                        evidence=(evidence_index,),
                    )
                )
            if number not in found and inline is None:
                complete = False
                limitations.append(
                    "annex_link_unknown"
                    if number in available_numbers or unlabelled_annex
                    else "missing_own_annex"
                )
        for index, part in enumerate(parts):
            if part.role not in ("osr", "justification", "unknown"):
                continue
            status = "unknown"
            evidence = []
            if part.role == "unknown":
                limitations.append("support_applicability_unknown")
                links.append(
                    DocumentLink(
                        kind="supporting",
                        source_part=index,
                        target_part=main_index,
                        status="unknown",
                    )
                )
                continue
            version = draft_version_span(part.text)
            if version is not None and version[0] == draft_version(main.text):
                status = "confirmed"
                evidence.append(fact_offset + len(facts))
                facts.append(
                    span_fact(
                        "support_reference",
                        version[0].isoformat(),
                        index,
                        part,
                        version[1],
                        version[2],
                    )
                )
            for reference in _SUPPORT_NORM.finditer(part.text):
                target = _article_amount(main.text, reference[1], reference[2])
                stated = list(_AMOUNT.finditer(reference[0]))
                if target is None or len(stated) != 1:
                    continue
                quantity = _quantity(reference[0][: stated[0].start()])
                if not quantity or quantity != target[3]:
                    continue
                value = _amount(stated[0][1])
                if value != target[0]:
                    status = "conflicting"
                elif status != "conflicting":
                    status = "confirmed"
                evidence.append(fact_offset + len(facts))
                facts.append(
                    span_fact(
                        "norm_value",
                        str(target[0]) + " PLN",
                        main_index,
                        main,
                        target[1],
                        target[2],
                    )
                )
                evidence.append(fact_offset + len(facts))
                facts.append(
                    span_fact(
                        "support_reference", str(value) + " PLN", index, part, *reference.span()
                    )
                )
            if status == "unknown":
                limitations.append("support_applicability_unknown")
            if status == "conflicting":
                limitations.append("support_conflict")
            links.append(
                DocumentLink(
                    kind="supporting",
                    source_part=index,
                    target_part=main_index,
                    status=status,
                    evidence=tuple(evidence),
                )
            )
    return BundleEvidence(tuple(links), tuple(facts), tuple(dict.fromkeys(limitations)), complete)


def _amount(value: str) -> Decimal:
    return Decimal(re.sub(r"\s", "", value).replace(",", "."))


def _article_amount(
    text: str, article: str, paragraph: str | None
) -> tuple[Decimal, int, int, str] | None:
    headings = list(_ARTICLE.finditer(text))
    targets = [index for index, match in enumerate(headings) if match[1] == article]
    if len(targets) != 1:
        return None
    index = targets[0]
    start = headings[index].end()
    end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
    if paragraph:
        section = re.search(r"(?:^|\n)\s*" + re.escape(paragraph) + r"\.\s", text[start:end])
        if section is None:
            return None
        start += section.end()
        following = re.search(r"\n\s*\d+\.\s", text[start:end])
        if following:
            end = start + following.start()
    amounts = list(_AMOUNT.finditer(text[start:end]))
    if len(amounts) != 1:
        return None
    amount = amounts[0]
    return (
        _amount(amount[1]),
        start + amount.start(),
        start + amount.end(),
        _quantity(text[start : start + amount.start()]),
    )
