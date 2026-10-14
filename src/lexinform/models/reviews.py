import calendar
import datetime as dt
import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from lexinform.models.sejm import TextDocument, is_rcl_number

ReviewPurpose = Literal["consultation_review", "act_application"]
DocumentRole = Literal["response", "position", "project_text", "comparison_text", "final_act"]


class ReviewDocument(BaseModel):
    model_config = ConfigDict(frozen=True)

    identity: str
    url: str
    role: DocumentRole
    text: str
    sha256: str
    normalized_sha256: str
    complete: bool = True
    file_sha256: str | None = None
    author: str = ""


class ReviewContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    number: str
    title: str
    purpose: ReviewPurpose
    documents: tuple[ReviewDocument, ...]
    act_eli: str | None = None
    promulgation_date: dt.date | None = None
    canonical_date: dt.date | None = None
    comparison_sha256: str | None = None

    @property
    def text(self) -> str:
        return "\n\n".join(document.text for document in self.documents)

    def memo_key(self, term: int) -> str:
        facts = {
            "term": None if is_rcl_number(self.number) else term,
            "number": self.number,
            "purpose": self.purpose,
            "documents": sorted(
                {(document.role, document.normalized_sha256) for document in self.documents}
            ),
            "act_eli": self.act_eli,
            "promulgation_date": str(self.promulgation_date),
            "canonical_date": str(self.canonical_date),
            "comparison_sha256": self.comparison_sha256,
        }
        return (
            "document-review:"
            + hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()
        )


class EvidenceCitation(BaseModel):
    document_id: str
    locator: str
    quote: str


class ConsultationFinding(BaseModel):
    proposal: str
    respondent: str
    outcome: Literal["accepted", "rejected", "partial", "unknown"]
    reason: str
    affected_groups: tuple[str, ...]
    evidence: EvidenceCitation | None
    text_change: str | None
    text_evidence: EvidenceCitation | None


class EffectiveRule(BaseModel):
    locator: str
    change: str
    affected_groups: tuple[str, ...]
    conditions: tuple[str, ...]
    exceptions: tuple[str, ...]
    old_applications: str
    documents: tuple[str, ...]
    date_kind: Literal["absolute", "relative", "conditional", "unknown"]
    date: dt.date | None
    days_after_promulgation: int | None = Field(ge=0)
    trigger: str
    evidence: EvidenceCitation | None
    date_evidence: EvidenceCitation | None

    def event_ref(self, eli: str) -> str:
        fact = (eli, _normalized(self.locator), str(self.date))
        return "effective/" + hashlib.sha256(json.dumps(fact).encode()).hexdigest()


class DocumentReview(BaseModel):
    purpose: ReviewPurpose
    complete: bool
    summary: str
    consultation_findings: tuple[ConsultationFinding, ...]
    effective_rules: tuple[EffectiveRule, ...]
    unresolved: tuple[str, ...]


class DocumentReviewRecord(BaseModel):
    review: DocumentReview
    context: ReviewContext
    model: str = ""
    prompt_version: str = ""
    created_at: dt.datetime
    input_tokens: int | None = 0
    output_tokens: int | None = 0
    cache_read_input_tokens: int | None = 0
    cache_creation_input_tokens: int | None = 0
    baseline_day: dt.date | None = None
    source_key: str = ""
    checked_at: dt.datetime | None = None
    canonical_dates: tuple[dt.date, ...] = ()


class ReviewWork(BaseModel):
    model_config = ConfigDict(frozen=True)

    memo_key: str
    purpose: ReviewPurpose
    context: ReviewContext | None = None
    input_bill_json: str = ""
    since: dt.datetime
    baseline_day: dt.date
    checked_at: dt.datetime | None = None
    limitation: str = ""
    source_key: str = ""
    act_eli: str | None = None


def _normalized(text: str) -> str:
    return " ".join(text.split()).casefold()


def _has_locator(text: str, locator: str) -> bool:
    key = _normalized(locator).rstrip(".")
    return bool(key and re.search(r"(?<!\w)" + re.escape(key) + r"(?!\w)", _normalized(text)))


def _response_rows(text: str) -> tuple[str, ...]:
    rows: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if re.match(r"^\d+\t", line) or line.startswith("Lp.\t"):
            if current:
                rows.append("\n".join(current))
            current = [line] if re.match(r"^\d+\t", line) else []
        elif current:
            current.append(line)
    if current:
        rows.append("\n".join(current))
    return tuple(rows)


def citation_document(
    citation: EvidenceCitation | None, context: ReviewContext, roles: set[DocumentRole]
) -> ReviewDocument | None:
    if citation is None or not citation.quote.strip() or not citation.locator.strip():
        return None
    if not re.match(
        r"(?:art\.?\s*\d+|§\s*\d+|pkt\.?\s*\d+|lp\.?\s*\d+|\d+[.)]?\s+\S)",
        citation.locator.strip(),
        re.IGNORECASE,
    ):
        return None
    return next(
        (
            document
            for document in context.documents
            if document.identity == citation.document_id
            and document.role in roles
            and " ".join(citation.quote.split()) in " ".join(document.text.split())
            and _has_locator(citation.quote, citation.locator)
        ),
        None,
    )


def validate_review(review: DocumentReview, context: ReviewContext) -> DocumentReview:
    if review.purpose != context.purpose:
        raise ValueError("document review purpose does not match its request")
    findings: list[ConsultationFinding] = []
    unresolved = list(review.unresolved)
    required_role = "response" if context.purpose == "consultation_review" else "final_act"
    if not any(document.role == required_role for document in context.documents):
        unresolved.append("Official response/final act unavailable; source coverage is incomplete.")
    for finding in review.consultation_findings:
        evidence = citation_document(finding.evidence, context, {"response"})
        if evidence is not None and finding.evidence is not None:
            quoted = " ".join(finding.evidence.quote.split())
            table_rows = [
                row for row in _response_rows(evidence.text) if quoted in " ".join(row.split())
            ]
            if table_rows and not any(quoted == " ".join(row.split()) for row in table_rows):
                evidence = None
        outcome = finding.outcome
        quote = _normalized(finding.evidence.quote.rsplit("\t", 1)[-1]) if finding.evidence else ""
        locator = _normalized(finding.evidence.locator) if finding.evidence else ""
        decision_lead = quote.removeprefix(locator).lstrip(" .:–-")
        decision_lead = decision_lead.split(".", maxsplit=1)[0]
        negative = bool(
            re.search(
                r"\bnie\s+(?:uwzględnion\w*|przyjęt\w*)|\bnieuwzględnion\w*|\bodrzucon\w*",
                decision_lead,
            )
        )
        partial = bool(re.search(r"częściowo|częściow\w* uwzględn|w części", decision_lead))
        positive = bool(re.search(r"\buwzględnion\w*|\bprzyjęt\w*", decision_lead))
        positive = positive and re.search(r"\bnie\b", decision_lead) is None
        supported = evidence is not None and (
            (outcome == "rejected" and negative and not partial)
            or (outcome == "partial" and partial)
            or (outcome == "accepted" and positive and not negative and not partial)
        )
        if re.search(
            r"do wiadomości|do protokołu|do dalszej analizy|propon\w*|powinn\w*", decision_lead
        ):
            supported = False
        if not supported:
            outcome = "unknown"
            unresolved.append(f"Outcome unconfirmed: {finding.proposal}")
        text_evidence = citation_document(finding.text_evidence, context, {"project_text"})
        comparison = next(
            (
                doc
                for doc in context.documents
                if doc.role == "comparison_text"
                and doc.normalized_sha256 == context.comparison_sha256
            ),
            None,
        )
        changed = (
            finding.text_change
            if text_evidence is not None
            and comparison is not None
            and finding.text_evidence is not None
            and _normalized(finding.text_evidence.quote) not in _normalized(comparison.text)
            else None
        )
        findings.append(
            finding.model_copy(
                update={
                    "outcome": outcome,
                    "text_change": changed,
                    "text_evidence": finding.text_evidence if changed else None,
                    "reason": finding.reason if supported else "",
                    "affected_groups": finding.affected_groups if supported else (),
                    "evidence": finding.evidence if evidence is not None else None,
                    "respondent": evidence.author
                    if evidence is not None and evidence.author
                    else finding.respondent
                    if supported and _normalized(finding.respondent) in quote
                    else "",
                }
            )
        )
    rules: list[EffectiveRule] = []
    for rule in review.effective_rules:
        if citation_document(rule.evidence, context, {"final_act"}) is None:
            unresolved.append(f"Application unconfirmed: {rule.locator}")
            continue
        date_document = citation_document(rule.date_evidence, context, {"final_act"})
        date_quote = _normalized(rule.date_evidence.quote) if rule.date_evidence else ""
        generic_scope = "ustawa wchodzi w życie" in date_quote and "z wyjątkiem" not in date_quote
        if generic_scope and date_document is not None:
            source = _normalized(date_document.text)
            offsets = [match.end() for match in re.finditer(re.escape(date_quote), source)]
            generic_scope = bool(offsets) and all(
                "z wyjątkiem" not in source[offset : offset + 120] for offset in offsets
            )
        scope_matches = _has_locator(date_quote, rule.locator) or generic_scope
        has_effect_cue = bool(
            re.search(r"wchod\w* w życie|stosuje się|ma zastosowanie", date_quote)
        )
        day = rule.date
        kind = rule.date_kind
        if (
            date_document is None
            or rule.date_evidence is None
            or not scope_matches
            or not has_effect_cue
        ):
            day, kind = None, "unknown"
        elif kind == "absolute":
            if day is None or not date_in_quote(day, rule.date_evidence.quote):
                day, kind = None, "unknown"
        elif kind == "relative":
            day = relative_effective_date(
                context.promulgation_date,
                rule.days_after_promulgation,
                rule.date_evidence.quote,
            )
            if day is None:
                kind = "unknown"
        elif kind == "conditional":
            day = None
        else:
            day = None
        if (
            generic_scope
            and context.canonical_date is not None
            and day is not None
            and day != context.canonical_date
        ):
            day, kind = None, "unknown"
            unresolved.append(f"General date conflicts with official act metadata: {rule.locator}")
        if kind == "unknown":
            unresolved.append(f"Date unconfirmed: {rule.locator}")
        rules.append(rule.model_copy(update={"date": day, "date_kind": kind}))
    if context.purpose == "act_application" and not rules:
        unresolved.append("No application rule is confirmed by the final act.")
    complete = (
        review.complete
        and all(document.complete for document in context.documents)
        and not unresolved
    )
    return review.model_copy(
        update={
            "consultation_findings": tuple(findings),
            "effective_rules": tuple(rules),
            "complete": complete,
            "unresolved": tuple(dict.fromkeys(unresolved)),
        }
    )


def review_signature(review: DocumentReview) -> str:
    findings = [
        finding.model_dump(exclude={"evidence", "text_evidence"})
        for finding in review.consultation_findings
    ]
    rules = [
        rule.model_dump(mode="json", exclude={"evidence", "date_evidence"})
        for rule in review.effective_rules
    ]
    return json.dumps({"findings": findings, "rules": rules}, sort_keys=True, ensure_ascii=False)


_MONTHS = (
    "stycznia",
    "lutego",
    "marca",
    "kwietnia",
    "maja",
    "czerwca",
    "lipca",
    "sierpnia",
    "września",
    "października",
    "listopada",
    "grudnia",
)


def date_in_quote(day: dt.date, quote: str) -> bool:
    text = _normalized(quote)
    forms = (
        day.isoformat(),
        day.strftime("%d.%m.%Y"),
        f"{day.day} {_MONTHS[day.month - 1]} {day.year}",
    )
    dates = re.findall(
        r"\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.\d{4}|\d{1,2} (?:" + "|".join(_MONTHS) + r") \d{4}",
        text,
    )
    return len(dates) == 1 and any(
        re.search(r"(?<!\d)" + re.escape(form) + r"(?!\d)", text) for form in forms
    )


def relative_effective_date(
    promulgation: dt.date | None, days: int | None, quote: str
) -> dt.date | None:
    if promulgation is None:
        return None
    text = _normalized(quote)
    if "z wyjątkiem" in text or "nie " in text:
        return None
    if days in (None, 0) and "z dniem ogłoszenia" in text:
        return promulgation
    if days in (None, 1) and "z dniem następującym po dniu ogłoszenia" in text:
        return promulgation + dt.timedelta(days=1)
    words = {
        "trzech": 3,
        "siedmiu": 7,
        "czternastu": 14,
        "trzydziestu": 30,
        "dwóch": 2,
        "sześciu": 6,
        "dwunastu": 12,
    }
    match = re.search(
        r"po upływie (\d+|" + "|".join(words) + r") (dni|tygodni|miesięcy|lat) od dnia ogłoszenia",
        text,
    )
    if match is None:
        return None
    value, unit = match.groups()
    count = int(value) if value.isdecimal() else words[value]
    if count < 1 or count > 3650:
        return None
    if unit == "dni" and days is not None and days != count:
        return None
    # Art. 6, ustawa o ogłaszaniu aktów normatywnych: the period expires before entry into force.
    if unit in ("dni", "tygodni"):
        return promulgation + dt.timedelta(days=count * (7 if unit == "tygodni" else 1) + 1)
    months = count * (12 if unit == "lat" else 1)
    year, month = divmod(promulgation.year * 12 + promulgation.month - 1 + months, 12)
    if year > 9999:
        return None
    month += 1
    last = calendar.monthrange(year, month)[1]
    return dt.date(year, month, min(promulgation.day, last)) + dt.timedelta(days=1)


class ReviewSource(BaseModel):
    identity: str
    document: TextDocument
    role: DocumentRole
    expected_digest: str | None = None
    author: str = ""
    snapshot: ReviewDocument | None = None
