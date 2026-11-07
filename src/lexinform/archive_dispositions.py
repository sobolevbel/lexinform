"""Build bounded archive metadata without changing ordinary candidate selection traces."""

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Literal

from lexinform.models import (
    ArchiveMemberDisposition,
    ArchiveMemberDispositions,
    CandidateDispositionTrace,
)
from lexinform.models.candidate_trace import (
    _PACKED_OUTCOMES,
    _PACKED_PHASES,
    _PACKED_REASONS,
    _PACKED_STAGES,
    MAX_ARCHIVE_MEMBER_ROWS,
    MAX_CANDIDATE_TRACE_BYTES,
    MAX_PACKED_ARCHIVE_MEMBER_ROWS,
    ArchiveMemberRowChecker,
    PackedArchiveMemberDispositions,
    archive_member_rows_sha256,
    validate_archive_member_parent,
)

MAX_ARCHIVE_VALIDATION_BYTES = 128 * 1024


@dataclass(frozen=True)
class _ObservedRows:
    count: int
    sha256: str
    prefix: tuple[ArchiveMemberDisposition, ...]


def _observe(
    trace: CandidateDispositionTrace, rows: Iterable[ArchiveMemberDisposition]
) -> _ObservedRows | None:
    checker = ArchiveMemberRowChecker()
    prefix: list[ArchiveMemberDisposition] = []
    count, size = 0, 2
    limited = False

    def observed() -> Iterator[ArchiveMemberDisposition]:
        nonlocal count, size, limited
        for value in rows:
            row = ArchiveMemberDisposition.model_validate(value)
            added = len(row.model_dump_json(exclude_defaults=True).encode()) + bool(count)
            if size + added > MAX_ARCHIVE_VALIDATION_BYTES:
                limited = True
                return
            row = checker.add(row)
            validate_archive_member_parent(trace, row)
            count += 1
            size += added
            if len(prefix) < MAX_ARCHIVE_MEMBER_ROWS:
                prefix.append(row)
            yield row

    digest = archive_member_rows_sha256(observed())
    return None if limited else _ObservedRows(count, digest, tuple(prefix))


def _attach(
    trace: CandidateDispositionTrace, section: ArchiveMemberDispositions
) -> CandidateDispositionTrace:
    candidate = trace.model_copy(update={"archive_members": section})
    if (
        not section.rows
        and trace.coverage == "complete"
        and len(candidate.model_dump_json(exclude_defaults=True).encode())
        > MAX_CANDIDATE_TRACE_BYTES
    ):
        candidate = candidate.model_copy(
            update={
                "coverage": "omitted_over_limit",
                "requested_document": None,
                "omitted_dispositions": len(trace.dispositions),
                "dispositions": (),
            }
        )
    return CandidateDispositionTrace.model_validate(candidate.model_dump())


def enrich_archive_trace(
    original: CandidateDispositionTrace,
    rows: Iterable[ArchiveMemberDisposition] | None,
    *,
    walk: Literal["finished", "interrupted", "unknown"],
    unvisited: Literal["none_known", "known", "unknown"],
) -> CandidateDispositionTrace:
    """Optional metadata failures preserve the ordinary trace; cancellation propagates."""
    if original.archive_members is not None:
        return original
    try:
        trace = CandidateDispositionTrace.model_validate_json(
            original.model_dump_json(exclude_defaults=True)
        )
        observed = _observe(trace, rows) if rows is not None else None
        if observed is None:
            return _attach(
                trace,
                ArchiveMemberDispositions(
                    walk="unknown",
                    retention="unknown",
                    observed_rows=None,
                    rows_sha256=None,
                    omitted_rows=None,
                    unvisited="unknown",
                ),
            )
        retained = len(observed.prefix) if trace.coverage == "complete" else 0
        for length in range(retained, -1, -1):
            try:
                section = ArchiveMemberDispositions(
                    walk=walk,
                    retention="complete"
                    if length == observed.count
                    else "prefix"
                    if length
                    else "omitted",
                    observed_rows=observed.count,
                    rows_sha256=observed.sha256,
                    omitted_rows=observed.count - length,
                    unvisited=unvisited,
                    rows=observed.prefix[:length],
                )
                return _attach(trace, section)
            except ValueError:
                continue
    except Exception:
        return original
    return original


def pack_archive_trace(
    trace: CandidateDispositionTrace, rows: Sequence[ArchiveMemberDisposition]
) -> CandidateDispositionTrace:
    legacy = trace.archive_members
    if (
        trace.coverage != "complete"
        or not isinstance(legacy, ArchiveMemberDispositions)
        or legacy.retention not in ("prefix", "omitted")
        or legacy.rows_sha256 is None
        or len(rows) != legacy.observed_rows
        or len(rows) > MAX_PACKED_ARCHIVE_MEMBER_ROWS
    ):
        return trace
    hashes: dict[str, int] = {}

    def position(sha256: str | None) -> int | None:
        return hashes.setdefault(sha256, len(hashes)) if sha256 is not None else None

    try:
        packed = PackedArchiveMemberDispositions(
            schema_version=2,
            walk=legacy.walk,
            retention="complete",
            observed_rows=len(rows),
            rows_sha256=legacy.rows_sha256,
            omitted_rows=0,
            unvisited=legacy.unvisited,
            packed_rows=tuple(
                (
                    _PACKED_STAGES.index(row.stage),
                    _PACKED_PHASES.index(row.phase),
                    row.index,
                    position(row.root_sha256),
                    row.ordinals,
                    row.physical_part_index,
                    _PACKED_OUTCOMES.index(row.outcome),
                    _PACKED_REASONS.index(row.reason),
                    position(row.leaf_sha256),
                    position(row.text_sha256),
                )
                for row in rows
            ),
            hashes=tuple(hashes),
        )
        return CandidateDispositionTrace.model_validate(
            {**trace.model_dump(), "archive_members": packed}
        )
    except ValueError:
        return trace
