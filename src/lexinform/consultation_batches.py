"""Bound consultation input while retaining complete source rows and explicit references."""

import hashlib
import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime

import tiktoken

from lexinform.document_fingerprints import text_digest
from lexinform.models.reviews import ConsultationRow, ReviewContext, ReviewDocument

CONSULTATION_INPUT_LIMIT = 64_000
CONSULTATION_REVIEW_BUDGET = 2.0
CONSULTATION_MODEL = "gpt-6.1-sol"
CONSULTATION_DELIVERY_PREFIX = "consultation-parts:"
_ENVELOPE_TOKENS = 4_000
_PROSE_BREAK = re.compile(r"\f|\n[^\S\n\f]*\n")
_REFERENCE = re.compile(r"\b(?:pkt|lp)\.?\s*(\d+)\s+(?:tego\s+)?(?:raportu|tabeli)\b", re.I)


class ConsultationInputTooLargeError(ValueError):
    pass


def consultation_rollout_allows(
    cutoff: datetime | None, when: datetime, *, partitioned: bool
) -> bool:
    return cutoff is None or (partitioned and when.utcoffset() is not None and when >= cutoff)


def _count_tokens(text: str) -> int:
    return len(tiktoken.get_encoding("o200k_base").encode_ordinary(text))


@dataclass(frozen=True)
class _Unit:
    doc: int
    start: int
    end: int
    row: ConsultationRow | None = None

    def payload(self, source: ReviewDocument) -> dict[str, str | None]:
        text = source.text[self.start : self.end]
        if self.row is None:
            return {"text": text}
        return {"text": text, "locator": self.row.locator, "label": self.row.label}


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


def _sources(context: ReviewContext) -> tuple[ReviewDocument, ...]:
    result = []
    seen: set[tuple[str, str]] = set()
    for document in context.documents:
        if document.role != "response" or not document.text.strip():
            continue
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
        if unit.row is not None and unit.row.label:
            table = unit.row.locator.rsplit("/", 1)[0]
            labels.setdefault((unit.doc, table, unit.row.label), []).append(index)
    refs: dict[int, set[int]] = {}
    for index, unit in enumerate(units):
        if unit.row is None:
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

    root_key = context.memo_key(term)
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
