import hashlib
import json
import re

from lexinform.document_bundle import inspect_bundle
from lexinform.document_facts import span_fact
from lexinform.document_versions import (
    draft_version_evidence,
    draft_version_span,
    join_normative_parts,
)
from lexinform.models.documents import (
    DocumentCoverage,
    DocumentFact,
    DocumentLimitation,
    DocumentPart,
    DocumentSelection,
)
from lexinform.sections import PAGE_BREAK, document_kind, trim_print
from lexinform.text_projection import SourcePiece, TextProjection, project_pieces

DOCUMENT_SELECTION_POLICY_VERSION = "document-selection-v1"


def bill_selection(
    selection: DocumentSelection, *, source_basis: tuple[str, ...]
) -> DocumentSelection:
    parts = tuple(_part_role(part) for part in selection.parts)
    bundle = inspect_bundle(parts, fact_offset=0)
    facts: list[DocumentFact] = list(bundle.facts)
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
            facts.append(span_fact("bill_structure", "bill", index, part, 0, article.end()))
        version = draft_version_span(part.text)
        if version:
            value, start, end = version
            facts.append(span_fact("draft_date", value.isoformat(), index, part, start, end))
        commencement = re.search(r"ustawa\s+wchodzi\s+w\s+życie", part.text, re.I)
        if commencement and not any(p.losses for p in parts) and bundle.annexes_complete:
            facts.append(
                span_fact(
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
    limitations: list[DocumentLimitation] = list(bundle.limitations)
    if not confirmed:
        limitations.append("missing_evidence")
    for index, part in enumerate(parts):
        if part.role != "bill":
            continue
        dated = draft_version_evidence(part.text)
        if len({date for date, _, _ in dated if date is not None}) > 1:
            limitations.append("draft_version_conflict")
            facts.extend(
                span_fact("version_conflict", str(date), index, part, start, end)
                for date, start, end in dated
            )
    core_dates = [
        (index, version)
        for index, part in enumerate(parts)
        if part.role in ("bill", "normative_continuation")
        and document_kind(part.text) == "bill"
        and (version := draft_version_span(part.text)) is not None
    ]
    if len({version[0] for _, version in core_dates}) > 1:
        limitations.append("draft_version_conflict")
        facts.extend(
            span_fact(
                "version_conflict",
                version[0].isoformat(),
                index,
                parts[index],
                version[1],
                version[2],
            )
            for index, version in core_dates
        )
    state = "selected" if confirmed else "unverified"
    if "missing_own_annex" in limitations:
        state = "waiting"
    if "draft_version_conflict" in limitations or any(
        link.status == "conflicting" for link in bundle.links
    ):
        state = "ambiguous"
    normative = [
        trim_print(part.text).text if part.role in ("bill", "normative_continuation") else part.text
        for part in parts
        if part.role in ("bill", "normative_annex", "normative_continuation")
    ]
    digest = hashlib.sha256(json.dumps(normative, ensure_ascii=False).encode()).hexdigest()
    return DocumentSelection.model_validate(
        {
            **selection.model_dump(),
            "parts": parts,
            "facts": facts,
            "state": state,
            "links": bundle.links,
            "coverage": tuple(
                DocumentCoverage(
                    part_index=index,
                    start=part.text_start,
                    end=part.text_end or len(part.text),
                    page_start=part.page_start,
                    page_end=part.page_end,
                    losses=part.losses,
                )
                for index, part in enumerate(parts)
            ),
            "normative_sha256": digest,
            "rejected": (
                *selection.rejected,
                *(
                    reason
                    for reason in limitations
                    if reason
                    in (
                        "missing_own_annex",
                        "support_conflict",
                        "foreign_annex",
                        "draft_version_conflict",
                    )
                ),
            ),
            "limitations": limitations,
        }
    )


def _part_role(part: DocumentPart) -> DocumentPart:
    kind = document_kind(part.text)
    if part.role == "unknown" and kind in ("bill", "justification", "osr"):
        return part.model_copy(update={"role": kind})
    return part


def selection_for_input(
    selection: DocumentSelection, text: str, *, projection: TextProjection | None = None
) -> DocumentSelection:
    coverage = [span for span in selection.coverage if span.stage != "analysis"]
    if projection is None:
        projection = _input_projection(selection, text)
    else:
        source = PAGE_BREAK.join(part.text for part in selection.parts)
        if (
            projection.text != text
            or projection.source_length != len(source)
            or projection.source_sha256 != hashlib.sha256(source.encode()).hexdigest()
        ):
            raise ValueError("Input projection differs from the selected text assembly")
        for segment in projection.segments:
            if segment.source_start is not None and (
                text[segment.start : segment.end]
                != source[segment.source_start : segment.source_start + segment.end - segment.start]
            ):
                raise ValueError("Input projection copy differs from the selected text assembly")
    missing = False
    offset = 0
    for index, part in enumerate(selection.parts):
        start = offset
        offset += len(part.text) + len(PAGE_BREAK)
        if part.role not in ("bill", "normative_annex", "normative_continuation"):
            continue
        expected = (
            trim_print(part.text).text
            if part.role in ("bill", "normative_continuation")
            else part.text
        )
        present = bool(expected.strip()) and expected.strip() in text
        missing = missing or not present
        losses: tuple[str, ...] = () if present else ("normative part omitted from prepared input",)
        spans = _part_input_ranges(projection, start, len(part.text))
        if projection is None:
            losses += ("input coverage unknown",)
        for begin, end in spans or [(0, 0)]:
            coverage.append(
                DocumentCoverage(
                    part_index=index,
                    stage="analysis",
                    start=begin,
                    end=end,
                    losses=losses,
                )
            )
    limitations: tuple[DocumentLimitation, ...] = tuple(
        reason for reason in selection.limitations if reason != "normative_text_omitted"
    )
    if missing:
        limitations += ("normative_text_omitted",)
    return DocumentSelection.model_validate(
        {
            **selection.model_dump(),
            "coverage": coverage,
            "limitations": limitations,
            "state": "unverified" if missing and selection.state == "selected" else selection.state,
        }
    )


def _input_projection(selection: DocumentSelection, text: str) -> TextProjection | None:
    if not selection.parts:
        return None
    source = PAGE_BREAK.join(part.text for part in selection.parts)
    if source == text:
        return project_pieces(source, (SourcePiece(source, 0),))
    trimmed = trim_print(source)
    return trimmed.projection if trimmed.text == text else None


def _part_input_ranges(
    projection: TextProjection | None, start: int, length: int
) -> list[tuple[int, int]]:
    if projection is None:
        return []
    result: list[tuple[int, int]] = []
    for segment in projection.segments:
        if segment.source_start is None:
            continue
        begin = max(start, segment.source_start)
        end = min(start + length, segment.source_start + segment.end - segment.start)
        if begin < end:
            result.append((begin - start, end - start))
    return result


def deduplicate_bundle(
    text: str, parts: tuple[DocumentPart, ...]
) -> tuple[str, tuple[DocumentPart, ...]]:
    unique: list[DocumentPart] = []
    seen: set[tuple[str, str]] = set()
    for part in parts:
        key = (part.sha256, part.role)
        if not part.sha256 or key not in seen:
            unique.append(part)
        if part.sha256:
            seen.add(key)
    if len(unique) == len(parts):
        return text, parts
    main = PAGE_BREAK.join(
        part.text for part in unique if part.role in ("bill", "unknown", "normative_continuation")
    )
    main = join_normative_parts(
        main, [part.text for part in unique if part.role == "normative_annex"]
    )
    support = [part.text for part in unique if part.role in ("justification", "osr")]
    return PAGE_BREAK.join([main, *support]), tuple(unique)
