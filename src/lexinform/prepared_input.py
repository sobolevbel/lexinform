"""Bind emitted text to selected part strings without retaining bodies or guessing origins."""

import hashlib

from lexinform.models.document_history import DocumentSelectionRecord
from lexinform.models.documents import DocumentSelection
from lexinform.models.prepared_input import (
    PreparedTextInput,
    PreparedTextPart,
    PreparedTextSegment,
)
from lexinform.sections import PAGE_BREAK
from lexinform.text_projection import SourcePiece, TextProjection, project_pieces


def prepared_text_input(
    selection: DocumentSelection, text: str, *, projection: TextProjection | None
) -> PreparedTextInput:
    if selection.scan_input is not None or any(
        span.stage == "analysis" for span in selection.coverage
    ):
        raise ValueError("Prepared text requires the source decision before input fitting")
    source = PAGE_BREAK.join(part.text for part in selection.parts)
    parts: list[PreparedTextPart] = []
    regions: list[tuple[int, int, int | None]] = []
    offset = 0
    for index, part in enumerate(selection.parts):
        identity = hashlib.sha256(part.text.encode()).hexdigest()
        if part.text_sha256 and part.text_sha256 != identity:
            raise ValueError("Prepared source text differs from its declared part hash")
        parts.append(PreparedTextPart(text_sha256=identity, chars=len(part.text), role=part.role))
        if part.text:
            regions.append((offset, offset + len(part.text), index))
        offset += len(part.text)
        if index < len(selection.parts) - 1:
            regions.append((offset, offset + len(PAGE_BREAK), None))
            offset += len(PAGE_BREAK)
    segments: list[PreparedTextSegment] = []
    if projection is None:
        if text:
            segments.append(PreparedTextSegment(start=0, end=len(text), origin="unknown"))
    else:
        if projection.text != text:
            raise ValueError("Prepared text differs from the projection output")
        validated = projection.compose(project_pieces(source, (SourcePiece(source, 0),)))
        for segment in validated.segments:
            if segment.source_start is None:
                segments.append(
                    PreparedTextSegment(
                        start=segment.start,
                        end=segment.end,
                        origin="generated",
                        system_origin="input_transform",
                    )
                )
                continue
            end = segment.source_start + segment.end - segment.start
            for begin, stop, part_index in regions:
                left, right = max(begin, segment.source_start), min(stop, end)
                if left >= right:
                    continue
                start = segment.start + left - segment.source_start
                segments.append(
                    PreparedTextSegment(
                        start=start,
                        end=start + right - left,
                        origin="source" if part_index is not None else "generated",
                        part_index=part_index,
                        source_start=left - begin if part_index is not None else None,
                        source_end=right - begin if part_index is not None else None,
                        system_origin=None if part_index is not None else "part_separator",
                    )
                )
    return PreparedTextInput(
        source_selection_id=DocumentSelectionRecord.from_selection(selection).selection_id,
        source_text_sha256=hashlib.sha256(source.encode()).hexdigest(),
        source_chars=len(source),
        separator=PAGE_BREAK,
        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        text_chars=len(text),
        parts=tuple(parts),
        segments=tuple(segments),
    )
