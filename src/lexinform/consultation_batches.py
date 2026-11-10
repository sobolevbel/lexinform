"""Bound consultation input while retaining complete source rows and explicit references."""

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime

import tiktoken

from lexinform.document_fingerprints import text_digest
from lexinform.models.batch import BatchRequest, DocumentReviewQuestion
from lexinform.models.documents import DocumentPart
from lexinform.models.reviews import (
    ConsultationCandidate,
    ConsultationFinding,
    ConsultationRow,
    ReviewContext,
    ReviewDocument,
)

CONSULTATION_INPUT_LIMIT = 64_000
CONSULTATION_REVIEW_BUDGET = 2.0
CONSULTATION_SYNTHESIS_RESERVE = 0.12
CONSULTATION_MODEL = "gpt-6.1-sol"
CONSULTATION_DELIVERY_PREFIX = "consultation-parts:"
_ENVELOPE_TOKENS = 4_000
_PROSE_BREAK = re.compile(r"\f|\n[^\S\n\f]*\n")
_ARTICLE = re.compile(
    r"^[ \t]*(?P<quoted>„[ \t]*)?Art\.[ \t]*(?P<number>[1-9][0-9]*[a-z]?)\.(?=\s|$)",
    re.I | re.M,
)
_ARTICLE_MENTION = re.compile(r"\bart\.\s*([1-9][0-9]*[a-z]?)\b", re.I)
_REFERENCE = re.compile(r"\b(?:pkt|lp)\.?\s*(\d+)\s+(?:tego\s+)?(?:raportu|tabeli)\b", re.I)


class ConsultationInputTooLargeError(ValueError):
    pass


def consultation_rollout_allows(
    cutoff: datetime | None,
    when: datetime,
    *,
    partitioned: bool,
    number: str,
    projects: frozenset[str],
) -> bool:
    return (not projects or number in projects) and (
        cutoff is None or (partitioned and when.utcoffset() is not None and when >= cutoff)
    )


def _count_tokens(text: str) -> int:
    return len(tiktoken.get_encoding("o200k_base").encode_ordinary(text))


def consultation_evidence_segment(
    source: ReviewDocument, span: ConsultationRow
) -> dict[str, str | None]:
    segment = {
        "text": source.text[span.start : span.end],
        "locator": span.locator,
        "label": span.label,
    }
    if span.kind == "paragraph":
        segment["kind"] = "paragraph"
    return segment


@dataclass(frozen=True)
class _Unit:
    doc: int
    start: int
    end: int
    row: ConsultationRow | None = None

    def payload(self, source: ReviewDocument) -> dict[str, str | None]:
        if self.row is None:
            return {"text": source.text[self.start : self.end]}
        return consultation_evidence_segment(source, self.row)


def _prose(index: int, text: str, start: int, end: int) -> Iterator[_Unit]:
    for paragraph in _PROSE_BREAK.finditer(text, start, end):
        yield _Unit(index, start, paragraph.end())
        start = paragraph.end()
    if start < end:
        yield _Unit(index, start, end)


def _units(index: int, source: ReviewDocument) -> Iterator[_Unit]:
    cursor = 0
    for row in source.consultation_rows:
        yield from _prose(index, source.text, cursor, row.start)
        yield _Unit(index, row.start, row.end, row)
        cursor = row.end
    yield from _prose(index, source.text, cursor, len(source.text))


def consultation_archive_fingerprint(parts: tuple[DocumentPart, ...]) -> str | None:
    """Identify an exact collection of complete report members independent of packaging."""
    if len(parts) < 2 or any(
        not (part.members and part.sha256 and part.text_sha256 and part.text.strip()) or part.losses
        for part in parts
    ):
        return None
    members = sorted((part.sha256, part.text_sha256) for part in parts)
    return hashlib.sha256(json.dumps(members).encode()).hexdigest()


def _sources(context: ReviewContext) -> tuple[ReviewDocument, ...]:
    result = []
    seen: set[tuple[str, str]] = set()
    archives: set[str] = set()
    for document in context.documents:
        if document.role != "response" or not document.text.strip():
            continue
        if document.complete and (archive := document.consultation_archive_fingerprint):
            if archive in archives:
                continue
            archives.add(archive)
        if digest := document.member_sha256 or document.file_sha256:
            key = (digest, document.sha256)
            if key in seen:
                continue
            seen.add(key)
        result.append(document)
    return tuple(result)


def _target_references(
    units: tuple[_Unit, ...], sources: tuple[ReviewDocument, ...]
) -> dict[int, set[int]]:
    labels: dict[tuple[int, str, str], list[int]] = {}
    for index, unit in enumerate(units):
        if unit.row is not None and unit.row.kind == "row" and unit.row.label:
            table = unit.row.locator.rsplit("/", 1)[0]
            labels.setdefault((unit.doc, table, unit.row.label), []).append(index)
    refs: dict[int, set[int]] = {}
    for index, unit in enumerate(units):
        if unit.row is None or unit.row.kind != "row":
            continue
        table = unit.row.locator.rsplit("/", 1)[0]
        text = sources[unit.doc].text[unit.start : unit.end]
        for match in _REFERENCE.finditer(text):
            target = labels.get((unit.doc, table, match[1]), ())
            if len(target) == 1 and target[0] != index:
                refs.setdefault(index, set()).add(target[0])
    return refs


def _part_document(source: ReviewDocument, units: list[_Unit]) -> ReviewDocument:
    pieces: list[str] = []
    rows: list[ConsultationRow] = []
    position = 0
    previous_end = units[0].start
    for unit in units:
        if unit.start != previous_end:
            pieces.append("\n\n")
            position += 2
        text = source.text[unit.start : unit.end]
        pieces.append(text)
        if unit.row is not None:
            rows.append(
                unit.row.model_copy(update={"start": position, "end": position + len(text)})
            )
        previous_end = unit.end
        position += len(text)
    text = "".join(pieces)
    return source.model_copy(
        update={
            "text": text,
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
            "normalized_sha256": text_digest(text),
            "consultation_rows": tuple(rows),
        }
    )


def plan_consultation_parts(
    context: ReviewContext,
    term: int,
    count: Callable[[str], int] | None = None,
    *,
    input_limit: int = CONSULTATION_INPUT_LIMIT,
) -> tuple[ReviewContext, ...]:
    """Assign every source unit and its unambiguous references to a bounded request."""
    if context.purpose != "consultation_review":
        raise ValueError("Consultation input is required")
    if count is None:
        count = _count_tokens
    sources = _sources(context)
    units = tuple(unit for i, doc in enumerate(sources) for unit in _units(i, doc))
    refs = _target_references(units, sources)
    sizes = [
        count(json.dumps(unit.payload(sources[unit.doc]), ensure_ascii=False)) + 2 for unit in units
    ]
    groups: list[set[int]] = []
    selected: set[int] = set()
    base = _ENVELOPE_TOKENS + count(context.title)
    used = base
    for index in range(len(units)):
        needed = {index, *refs.get(index, ())}
        new = needed - selected
        added = sum(sizes[i] for i in new)
        if used + added > input_limit and selected:
            groups.append(selected)
            selected = set()
            used = base
            new = needed
            added = sum(sizes[i] for i in new)
        if used + added > input_limit:
            raise ConsultationInputTooLargeError(
                "A consultation source unit and its references exceed the input limit"
            )
        selected.update(new)
        used += added
    if selected:
        groups.append(selected)

    root_key = hashlib.sha256(
        json.dumps(
            [
                context.number,
                None if context.number.startswith("RCL/") else term,
                [
                    (source.identity, source.url, source.sha256, source.file_sha256)
                    for source in sources
                ],
            ],
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    result = []
    for index, group in enumerate(groups, 1):
        by_doc: dict[int, list[_Unit]] = {}
        for i in sorted(group):
            unit = units[i]
            by_doc.setdefault(unit.doc, []).append(unit)
        result.append(
            context.model_copy(
                update={
                    "documents": tuple(
                        _part_document(sources[i], values) for i, values in by_doc.items()
                    ),
                    "consultation_partition": f"{root_key}/{index}",
                    "comparison_sha256": None,
                    "source_observation_ids": (),
                    "source_observation_term": None,
                }
            )
        )
    return tuple(result)


def prepare_consultation_parts(
    context: ReviewContext, term: int, prepare: Callable[[BatchRequest], BatchRequest]
) -> tuple[tuple[ReviewContext, BatchRequest], ...]:
    """Shrink the packing limit once if a serialized request exceeds the provider limit."""
    for input_limit in (CONSULTATION_INPUT_LIMIT, CONSULTATION_INPUT_LIMIT - 8_000):
        parts = plan_consultation_parts(context, term, input_limit=input_limit)
        try:
            return tuple(
                (
                    part,
                    prepare(
                        BatchRequest(
                            custom_id=part.memo_key(term),
                            term=term,
                            number=context.number,
                            question=DocumentReviewQuestion(kind="consultation_review", ctx=part),
                            model=CONSULTATION_MODEL,
                        )
                    ),
                )
                for part in parts
            )
        except ConsultationInputTooLargeError:
            if input_limit != CONSULTATION_INPUT_LIMIT:
                raise
    raise AssertionError("Consultation preparation must return or raise within its two limits")


def consultation_draft_pair(context: ReviewContext) -> tuple[ReviewDocument, ReviewDocument] | None:
    before = [
        doc
        for doc in context.documents
        if doc.role == "comparison_text"
        and doc.normalized_sha256 == context.comparison_sha256
        and doc.complete
    ]
    after = [doc for doc in context.documents if doc.role == "project_text" and doc.complete]
    if len(before) != 1 or len(after) != 1:
        return None
    prior, current = before[0], after[0]
    if (
        prior.draft_date is None
        or current.draft_date is None
        or prior.draft_date >= current.draft_date
    ):
        return None
    return prior, current


def _article_excerpts(text: str) -> dict[str, str]:
    matches = list(_ARTICLE.finditer(text))
    counts = Counter(match["number"].lower() for match in matches)
    excerpts = {}
    next_heading = next_plain_heading = len(text)
    for match in reversed(matches):
        number = match["number"].lower()
        quoted = bool(match["quoted"])
        end = next_heading if quoted else next_plain_heading
        if counts[number] == 1 and end - match.start() <= 8_000:
            excerpts[number] = text[match.start() : end].strip()
        if not quoted:
            next_plain_heading = match.start()
        next_heading = match.start()
    return excerpts


def _comparison_excerpts(
    context: ReviewContext, findings: tuple[ConsultationFinding, ...]
) -> tuple[ReviewDocument, ...]:
    drafts = consultation_draft_pair(context)
    if drafts is None:
        return ()
    old, new = (_article_excerpts(doc.text) for doc in drafts)
    articles = dict.fromkeys(
        match[1].lower()
        for finding in findings
        if finding.evidence is not None and finding.outcome in ("accepted", "partial")
        for match in _ARTICLE_MENTION.finditer(finding.evidence.quote)
    )
    relevant = [
        article
        for article in articles
        if article in old and article in new and old[article] != new[article]
    ][:6]
    if not relevant:
        return ()
    result = []
    for source, excerpts in zip(drafts, (old, new), strict=True):
        text = "\n\n".join(excerpts[article] for article in relevant)
        result.append(
            source.model_copy(
                update={
                    "text": text,
                    "sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "normalized_sha256": text_digest(text),
                }
            )
        )
    return tuple(result)


def consultation_synthesis_context(
    context: ReviewContext, term: int, findings: tuple[ConsultationFinding, ...]
) -> tuple[ReviewContext, dict[str, ConsultationFinding]]:
    selected: dict[str, ConsultationFinding] = {}
    candidates: list[ConsultationCandidate] = []
    for index, finding in enumerate(findings, 1):
        candidate_id = str(index)
        selected[candidate_id] = finding
        candidates.append(
            ConsultationCandidate(
                id=candidate_id,
                theme=finding.theme[:120],
                proposal=finding.proposal[:720],
                outcome=finding.response_label or finding.outcome,
                practical_impact=finding.practical_impact[:500],
            )
        )
    drafts = _comparison_excerpts(context, findings)
    return (
        ReviewContext(
            number=context.number,
            title=context.title,
            purpose="consultation_review",
            documents=drafts,
            comparison_sha256=drafts[0].normalized_sha256 if drafts else None,
            consultation_partition=f"{context.memo_key(term)}/synthesis",
            consultation_candidates=tuple(candidates),
        ),
        selected,
    )
