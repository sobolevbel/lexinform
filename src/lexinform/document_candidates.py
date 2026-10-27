"""Select the existing legal package from physical parts without file or network I/O."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Self

from lexinform.document_versions import comparable_versions, normative_annex_key, regulation_support
from lexinform.models.documents import (
    DocumentPart,
    DocumentSelection,
    ExtractedDocument,
    ExtractedParts,
)
from lexinform.models.rcl import READABLE_EXTENSIONS, TextRole, text_rank, text_role
from lexinform.sections import APPENDIX_KINDS, PAGE_BREAK, Kind, document_kind

_ROLE_OF_KIND: dict[Kind, TextRole] = {
    "bill": "bill",
    "justification": "justification",
    "osr": "osr",
}

_NOT_THE_BILL = APPENDIX_KINDS | {"letter"}


@dataclass(frozen=True)
class DocumentCandidate:
    source: str
    text: str | None
    parts: tuple[DocumentPart, ...] = ()
    observed_parts: tuple[DocumentPart, ...] = ()

    @property
    def is_bill(self) -> bool:
        return self.text is not None and document_kind(self.text) == "bill"


@dataclass(frozen=True)
class CandidateSet:
    entries: tuple[DocumentCandidate, ...]

    @classmethod
    def from_parts(cls, parts: Sequence[DocumentPart]) -> Self:
        return cls(
            tuple(
                DocumentCandidate(str(part.members), part.text, (part,), (part,)) for part in parts
            )
        )

    def bill_indices(self) -> tuple[int, ...]:
        return tuple(index for index, item in enumerate(self.entries) if item.is_bill)


@dataclass(frozen=True)
class SelectedCandidate:
    index: int
    observed_parts: tuple[DocumentPart, ...]


@dataclass(frozen=True)
class AmbiguousCandidates:
    selection: DocumentSelection


def select_candidate_versions(
    candidates: CandidateSet,
) -> SelectedCandidate | AmbiguousCandidates | None:
    bill_indices = candidates.bill_indices()
    if not bill_indices:
        return None
    versions = comparable_versions([candidates.entries[index].text or "" for index in bill_indices])
    if not versions:
        return AmbiguousCandidates(
            DocumentSelection(
                state="ambiguous",
                parts=tuple(
                    part for index in bill_indices for part in candidates.entries[index].parts
                ),
                rejected=tuple(
                    f"{candidates.entries[index].source}: conflicting bill version"
                    for index in bill_indices
                ),
            )
        )
    observed = tuple(
        part
        for index in bill_indices
        for part in (candidates.entries[index].observed_parts or candidates.entries[index].parts)
    )
    return SelectedCandidate(bill_indices[versions[0]], observed)


def select_document_parts(
    extracted: ExtractedParts, *, max_part_chars: int | None
) -> ExtractedDocument:
    parts: list[DocumentPart] = []
    for part in extracted.parts:
        kind = document_kind(part.text) if part.text.strip() else "unknown"
        parts.append(part.model_copy(update={"role": _ROLE_OF_KIND.get(kind, "unknown")}))
    rejected = list(extracted.rejected)
    if len(parts) == 1 and not parts[0].members:
        part = parts[0]
        return ExtractedDocument(
            text=part.text,
            selection=DocumentSelection(parts=(part,), rejected=tuple(rejected)),
        )
    return _select_parts(parts, rejected, max_part_chars=max_part_chars)


def _select_parts(
    parts: list[DocumentPart], rejected: list[str], *, max_part_chars: int | None
) -> ExtractedDocument:
    candidates: list[tuple[tuple[int, int, int, int], DocumentPart]] = []
    supporting: list[DocumentPart] = []
    annexes: list[DocumentPart] = []
    for part in parts:
        name = part.members[-1]
        base = name.rsplit("/", 1)[-1]
        kind = document_kind(part.text) if part.text.strip() else "unknown"
        if kind != "bill" and _normative_annex(base, part.text):
            annexes.append(part.model_copy(update={"role": "normative_annex"}))
            continue
        if kind in _NOT_THE_BILL or not part.text.strip():
            rejected.append(f"{part.members}: {kind}")
            continue
        role = _ROLE_OF_KIND.get(kind) or (_member_role(base) if kind == "unknown" else None)
        if role in ("justification", "osr"):
            if regulation_support(part.text, name=base):
                rejected.append(f"{part.members}: supporting draft regulation")
                continue
            supporting.append(part.model_copy(update={"role": role}))
            continue
        if role != "bill":
            continue
        if kind == "unknown" and max_part_chars and len(part.text) > max_part_chars:
            rejected.append(f"{part.members}: unrecognised long document")
            continue
        rank = (
            0 if kind == "bill" else 1,
            len(part.members),
            *text_rank(base, base.rsplit(".", 1)[-1].lower()),
        )
        candidates.append((rank, part.model_copy(update={"role": "bill"})))
    if not candidates:
        return ExtractedDocument(
            text=PAGE_BREAK.join(p.text for p in supporting),
            selection=DocumentSelection(
                observed_parts=tuple(parts),
                state="unsupported",
                parts=tuple(supporting),
                rejected=tuple(rejected),
            ),
        )
    candidates.sort(key=lambda item: (item[0], item[1].members))
    rank, main = candidates[0]
    peers = [p for r, p in candidates if r[0] == rank[0]]
    peer_set = CandidateSet.from_parts(peers)
    confirmed = [peers[index] for index in peer_set.bill_indices()]
    if len(confirmed) > 1:
        indices = comparable_versions([p.text for p in confirmed])
        if not indices:
            return ExtractedDocument(
                selection=DocumentSelection(
                    observed_parts=tuple(parts),
                    state="ambiguous",
                    parts=tuple(confirmed),
                    rejected=tuple(rejected),
                )
            )
        main = confirmed[indices[0]]
    family = main.members[:-1]
    kept = [main]
    kept.extend(
        sorted(
            (p for p in annexes if p.members[:-1] == family),
            key=lambda p: normative_annex_key(p.text),
        )
    )
    for role in ("justification", "osr"):
        matches = [p for p in supporting if p.role == role and p.members[:-1] == family]
        if matches:
            kept.append(
                min(
                    matches,
                    key=lambda p: (
                        text_rank(p.members[-1], p.members[-1].rsplit(".", 1)[-1].lower()),
                        p.members,
                    ),
                )
            )
    losses = tuple(rejected)
    return ExtractedDocument(
        text=PAGE_BREAK.join(p.text for p in kept),
        selection=DocumentSelection(
            observed_parts=tuple(parts),
            parts=tuple(kept),
            rejected=losses,
            role=("bill heading",) if document_kind(main.text) == "bill" else (),
            completeness=()
            if losses or any(p.losses for p in kept)
            else ("all selected members extracted",),
        ),
    )


def _member_role(name: str) -> TextRole | None:
    base = name.rsplit("/", 1)[-1]
    if not base or base.startswith(".") or name.startswith("__MACOSX/"):
        return None
    if base.rsplit(".", 1)[-1].lower() not in READABLE_EXTENSIONS:
        return None
    return text_role(base)


def _normative_annex(name: str, text: str) -> bool:
    return bool(
        re.search(
            r"(?im)^\s*za[łl][aą]cznik[^\n]*do (?:(?:niniejszej|projektu) )?ustawy\b",
            text[:1200],
        )
        or re.search(r"za[łl][aą]cznik.*do (?:(?:niniejszej|projektu) )?ustawy", name, re.I)
    )
