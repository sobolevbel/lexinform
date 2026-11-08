"""Select the existing legal package from physical parts without file or network I/O."""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Self

from lexinform.archive_policy_capture import (
    ArchivePolicyDecision,
    ArchiveVersionChoice,
    PolicyOutcome,
)
from lexinform.document_versions import (
    VersionComparison,
    compare_version_decision,
    normative_annex_key,
    regulation_support,
)
from lexinform.models.candidate_trace import ArchiveMemberPolicyReason
from lexinform.models.documents import (
    PDF_PAGE_FAILURE_PREFIX,
    ArchiveMemberReadFailure,
    DocumentPart,
    DocumentSelection,
    ExtractedDocument,
    ExtractedParts,
)
from lexinform.models.rcl import READABLE_EXTENSIONS, TextRole, names_a_bill, text_rank, text_role
from lexinform.sections import (
    APPENDIX_KINDS,
    PAGE_BREAK,
    Kind,
    document_kind,
    reads_through_justification,
)

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
    comparison: VersionComparison


@dataclass(frozen=True)
class AmbiguousCandidates:
    selection: DocumentSelection
    comparison: VersionComparison


def select_candidate_versions(
    candidates: CandidateSet,
) -> SelectedCandidate | AmbiguousCandidates | None:
    bill_indices = candidates.bill_indices()
    if not bill_indices:
        return None
    comparison = compare_version_decision(
        [candidates.entries[index].text or "" for index in bill_indices]
    )
    observed = tuple(
        part
        for index in bill_indices
        for part in (candidates.entries[index].observed_parts or candidates.entries[index].parts)
    )
    if not comparison.matching_indices:
        parts = tuple(part for index in bill_indices for part in candidates.entries[index].parts)
        return AmbiguousCandidates(
            DocumentSelection(
                state="ambiguous",
                parts=parts,
                observed_parts=observed if observed != parts else (),
                rejected=tuple(
                    f"{candidates.entries[index].source}: conflicting bill version"
                    for index in bill_indices
                ),
            ),
            comparison,
        )
    return SelectedCandidate(bill_indices[comparison.matching_indices[0]], observed, comparison)


def select_document_parts(
    extracted: ExtractedParts,
    *,
    max_part_chars: int | None,
    decision_sink: Callable[[ArchivePolicyDecision], None] | None = None,
) -> ExtractedDocument:
    pdf_extractions = {item.part_index: item.extraction for item in extracted.pdf_pages}
    parts: list[DocumentPart] = []
    for index, part in enumerate(extracted.parts):
        kind = document_kind(part.text) if part.text.strip() else "unknown"
        losses = _legacy_losses(part)
        if (pdf := pdf_extractions.get(index)) is not None:
            if failures := sum(page.error_type is not None for page in pdf.pages):
                losses += (f"{PDF_PAGE_FAILURE_PREFIX}{failures} page(s)",)
            if part.members and pdf.error_type is not None:
                losses += (f"{PDF_PAGE_FAILURE_PREFIX}an unreadable page tree",)
        parts.append(
            part.model_copy(update={"role": _ROLE_OF_KIND.get(kind, "unknown"), "losses": losses})
        )
    rejected = list(extracted.rejected)
    if len(parts) == 1 and not parts[0].members:
        part = parts[0]
        return ExtractedDocument(
            text=part.text,
            selection=DocumentSelection(parts=(part,), rejected=tuple(rejected)),
        )
    return _select_parts(
        parts,
        rejected,
        unreadable_drafts=tuple(
            failure
            for failure in extracted.unreadable_members
            if _member_role(base := failure.members[-1].rsplit("/", 1)[-1]) == "bill"
            and names_a_bill(base)
        ),
        part_archive_ordinals=extracted.part_archive_ordinals,
        max_part_chars=max_part_chars,
        decision_sink=decision_sink,
    )


def _legacy_losses(part: DocumentPart) -> tuple[str, ...]:
    mismatch = "text page coverage differs from the PDF page count"
    if part.pages <= 0 or mismatch not in part.losses:
        return part.losses
    if not part.text.strip() or (
        len(part.text.split(PAGE_BREAK)) < part.pages and reads_through_justification(part.text)
    ):
        return tuple(loss for loss in part.losses if loss != mismatch)
    return part.losses


def _select_parts(
    parts: list[DocumentPart],
    rejected: list[str],
    *,
    unreadable_drafts: tuple[ArchiveMemberReadFailure, ...],
    part_archive_ordinals: tuple[tuple[int, ...], ...],
    max_part_chars: int | None,
    decision_sink: Callable[[ArchivePolicyDecision], None] | None = None,
) -> ExtractedDocument:
    candidates: list[tuple[tuple[int, int, int, int], int, DocumentPart]] = []
    supporting: list[tuple[int, DocumentPart]] = []
    annexes: list[tuple[int, DocumentPart]] = []
    incomplete_named_bills: list[tuple[int, DocumentPart]] = []
    decisions: dict[int, tuple[PolicyOutcome, ArchiveMemberPolicyReason]] = {}
    version_choice: ArchiveVersionChoice | None = None
    conflict_slots: tuple[int, ...] = ()

    def note(index: int, outcome: PolicyOutcome, reason: ArchiveMemberPolicyReason) -> None:
        if decision_sink is not None:
            decisions[index] = (outcome, reason)

    def finish(result: ExtractedDocument) -> ExtractedDocument:
        if decision_sink is not None and len(decisions) == len(parts):
            try:
                for index, part in enumerate(parts):
                    outcome, reason = decisions[index]
                    decision_sink(
                        ArchivePolicyDecision(
                            index,
                            outcome,
                            reason,
                            part.sha256,
                            part.text_sha256,
                            version_choice if reason == "selected_main" else None,
                            compared_in_conflict=index in conflict_slots,
                        )
                    )
            except Exception:
                pass
        return result

    for index, part in enumerate(parts):
        name = part.members[-1]
        base = name.rsplit("/", 1)[-1]
        kind = document_kind(part.text) if part.text.strip() else "unknown"
        if kind != "bill" and _normative_annex(base, part.text):
            annexes.append((index, part.model_copy(update={"role": "normative_annex"})))
            continue
        if (
            kind in ("unknown", "letter")
            and part.has_pdf_page_failure
            and _member_role(base) == "bill"
        ):
            incomplete_named_bills.append((index, part))
        if kind in _NOT_THE_BILL or not part.text.strip():
            rejected.append(f"{part.members}: {kind}")
            if part.text.strip():
                note(index, "non_bill", "non_bill_body")
            else:
                note(index, "unclassified", "empty_text")
            continue
        role = _ROLE_OF_KIND.get(kind) or (_member_role(base) if kind == "unknown" else None)
        if role in ("justification", "osr"):
            if regulation_support(part.text, name=base):
                rejected.append(f"{part.members}: supporting draft regulation")
                note(index, "policy_excluded", "regulation_support")
                continue
            supporting.append((index, part.model_copy(update={"role": role})))
            continue
        if role != "bill":
            note(index, "unclassified", "unknown_role")
            continue
        if kind == "unknown" and max_part_chars and len(part.text) > max_part_chars:
            rejected.append(f"{part.members}: unrecognised long document")
            note(index, "policy_excluded", "long_unknown_text")
            continue
        rank = (
            0 if kind == "bill" else 1,
            len(part.members),
            *text_rank(base, base.rsplit(".", 1)[-1].lower()),
        )
        candidates.append((rank, index, part.model_copy(update={"role": "bill"})))
        note(index, "policy_excluded", "rank_preference")
    if not candidates and (incomplete_named_bills or unreadable_drafts):
        for index, _ in incomplete_named_bills:
            note(index, "unclassified", "archive_pdf_page_failure")
        for index, _ in (*annexes, *supporting):
            note(index, "unclassified", "no_bill_candidate")
        return finish(
            ExtractedDocument(
                archive_unpack_blocked=bool(unreadable_drafts),
                selection=DocumentSelection(
                    observed_parts=tuple(parts),
                    state="waiting",
                    parts=tuple(part for _, part in incomplete_named_bills),
                    rejected=(
                        *rejected,
                        *(
                            ("PDF page extraction failed in an archive draft",)
                            if incomplete_named_bills
                            else ()
                        ),
                    ),
                ),
            )
        )
    if not candidates:
        for index, _ in annexes:
            note(index, "policy_excluded", "no_bill_candidate")
        for index, _ in supporting:
            note(index, "selected", "selected_support")
        return finish(
            ExtractedDocument(
                text=PAGE_BREAK.join(p.text for _, p in supporting),
                selection=DocumentSelection(
                    observed_parts=tuple(parts),
                    state="unsupported",
                    parts=tuple(p for _, p in supporting),
                    rejected=tuple(rejected),
                ),
            )
        )
    candidates.sort(key=lambda item: (item[0], item[2].members))
    rank, main_index, main = candidates[0]
    peers = [(index, p) for r, index, p in candidates if r[0] == rank[0]]
    peer_set = CandidateSet.from_parts([p for _, p in peers])
    confirmed = [peers[index] for index in peer_set.bill_indices()]
    comparison_peers = confirmed or [(main_index, main)]

    def archive_parent(
        members: tuple[str, ...], ordinals: tuple[int, ...]
    ) -> tuple[tuple[int, ...], tuple[str, ...]]:
        return ordinals[:-1] if part_archive_ordinals else (), members[:-1]

    def comparison_family(
        members: tuple[str, ...], ordinals: tuple[int, ...]
    ) -> tuple[tuple[int, ...], tuple[str, ...], str]:
        return (*archive_parent(members, ordinals), members[-1].rpartition("/")[0])

    def part_parent(index: int, part: DocumentPart) -> tuple[tuple[int, ...], tuple[str, ...]]:
        return archive_parent(
            part.members, part_archive_ordinals[index] if part_archive_ordinals else ()
        )

    def part_family(index: int, part: DocumentPart) -> tuple[tuple[int, ...], tuple[str, ...], str]:
        return (*part_parent(index, part), part.members[-1].rpartition("/")[0])

    comparison_families = {part_family(index, part) for index, part in comparison_peers}
    incomplete_peers = tuple(
        (index, part)
        for index, part in incomplete_named_bills
        if part_family(index, part) in comparison_families
    )
    unreadable_peer = any(
        comparison_family(failure.members, failure.ordinals) in comparison_families
        for failure in unreadable_drafts
    )
    pdf_failure = (
        main.has_pdf_page_failure
        or bool(incomplete_peers)
        or (len(confirmed) > 1 and any(part.has_pdf_page_failure for _, part in confirmed))
    )
    if pdf_failure or unreadable_peer:
        incomplete_indices = {index for index, _ in incomplete_peers}
        waiting_parts = (
            *((index, part) for index, part in comparison_peers if index not in incomplete_indices),
            *incomplete_peers,
        )
        failures = {index for index, part in waiting_parts if part.has_pdf_page_failure}
        for _, index, _ in candidates:
            note(
                index,
                "unclassified",
                "archive_pdf_page_failure" if index in failures else "archive_comparison_deferred",
            )
        for index, _ in incomplete_peers:
            note(index, "unclassified", "archive_pdf_page_failure")
        for index, _ in (*annexes, *supporting):
            note(index, "unclassified", "archive_comparison_deferred")
        return finish(
            ExtractedDocument(
                archive_unpack_blocked=unreadable_peer,
                selection=DocumentSelection(
                    observed_parts=tuple(parts),
                    state="waiting",
                    parts=tuple(part for _, part in waiting_parts),
                    rejected=(
                        *rejected,
                        *(
                            ("PDF page extraction failed during archive version comparison",)
                            if pdf_failure
                            else ()
                        ),
                    ),
                ),
            )
        )
    if len(confirmed) > 1:
        comparison = compare_version_decision([p.text for _, p in confirmed])
        indices = comparison.matching_indices
        compared_parents = {part_parent(*confirmed[index]) for index in indices}
        annex_identities = {
            tuple(
                sorted(
                    normative_annex_key(part.text)
                    for index, part in annexes
                    if part_parent(index, part) == parent
                )
            )
            for parent in compared_parents
        }
        annex_conflict = len(annex_identities) > 1
        if not indices or annex_conflict:
            if not indices and decision_sink is not None and len(confirmed) == 2:
                conflict_slots = (confirmed[0][0], confirmed[1][0])
            for index, _ in (*annexes, *supporting):
                note(index, "policy_excluded", "version_conflict")
            for index, _ in confirmed:
                note(index, "policy_excluded", "version_conflict")
            return finish(
                ExtractedDocument(
                    selection=DocumentSelection(
                        observed_parts=tuple(parts),
                        state="ambiguous",
                        limitations=("draft_version_conflict",) if annex_conflict else (),
                        parts=(
                            *(part for _, part in confirmed),
                            *(
                                part
                                for index, part in annexes
                                if annex_conflict and part_parent(index, part) in compared_parents
                            ),
                        ),
                        rejected=tuple(rejected),
                    )
                )
            )
        for index, _ in confirmed:
            note(index, "policy_excluded", "version_preference")
        winner = indices[0]
        main_index, main = confirmed[winner]
        if decision_sink is not None and len(confirmed) == 2:
            dates = comparison.date_filter
            version_choice = ArchiveVersionChoice(
                peer_index=confirmed[1 - winner][0],
                selected_first=winner == 0,
                date_filter=(dates[0], dates[1]) if dates is not None else None,
            )
    family = part_parent(main_index, main)
    for index, _ in (*annexes, *supporting):
        note(index, "policy_excluded", "family_exclusion")
    kept = [main]
    note(main_index, "selected", "selected_main")
    kept_annexes = sorted(
        ((index, p) for index, p in annexes if part_parent(index, p) == family),
        key=lambda item: normative_annex_key(item[1].text),
    )
    for index, p in kept_annexes:
        kept.append(p)
        note(index, "selected", "selected_annex")
    for role in ("justification", "osr"):
        matches = [
            (index, p)
            for index, p in supporting
            if p.role == role and part_parent(index, p) == family
        ]
        for index, _ in matches:
            note(index, "policy_excluded", "rank_preference")
        if matches:
            index, p = min(
                matches,
                key=lambda item: (
                    text_rank(item[1].members[-1], item[1].members[-1].rsplit(".", 1)[-1].lower()),
                    item[1].members,
                ),
            )
            kept.append(p)
            note(index, "selected", "selected_support")
    losses = tuple(rejected)
    return finish(
        ExtractedDocument(
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
