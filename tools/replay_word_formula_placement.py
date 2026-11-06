"""Verify retained text anchors around isolated Word formula paragraphs offline."""

import hashlib
from dataclasses import dataclass

from lexinform.models.documents import WordTextExtraction
from lexinform.sections import PAGE_BREAK
from lexinform.text_projection import TextProjection
from lexinform.word_formulas import WordFormulaScan
from lexinform.word_projection import legacy_word_text


@dataclass(frozen=True)
class FormulaPlacement:
    paragraph_path: tuple[int, ...]
    formula_path: tuple[int, ...]
    formula_sha256: str
    source_span: tuple[int, int]
    emitted_span: tuple[int, int]
    source_insert_at: int
    emitted_insert_at: int


def retained_formula_placements(
    body: WordTextExtraction, scan: WordFormulaScan, projection: TextProjection
) -> tuple[FormulaPlacement, ...] | None:
    """Require both neighboring paragraphs and their exact gap to survive one source copy."""
    blocks = {block.block_id: block for block in body.blocks}
    walk = tuple(blocks[identity] for identity in body.legacy_blocks)
    indices = {block.path: index for index, block in enumerate(walk)}
    source = body.legacy_text
    if (
        body.body_state != "read"
        or body.xml_sha256 != scan.document_xml_sha256
        or scan.coverage != "complete_body_walk"
        or projection.source_length != len(source)
        or projection.source_sha256 != hashlib.sha256(source.encode()).hexdigest()
    ):
        return None
    placements = []
    for formula in scan.formulas:
        path = formula.paragraph_path
        if (
            path is None
            or len(path) != 2
            or formula.structure is None
            or formula.xml_sha256 is None
        ):
            return None
        index = indices.get(path)
        if index is None or not 0 < index < len(walk) - 1:
            return None
        left, middle, right = walk[index - 1 : index + 2]
        triplet = "\n".join((left.text, middle.text, right.text))
        if (
            left.path != (path[0], path[1] - 1)
            or right.path != (path[0], path[1] + 1)
            or any(block.kind != "paragraph" for block in (left, middle, right))
            or middle.text
            or not left.text.strip()
            or not right.text.strip()
            or PAGE_BREAK in triplet
        ):
            return None
        start = len(legacy_word_text(block.text for block in walk[:index])) - len(left.text)
        end = start + len(triplet)
        if start < 0 or source[start:end] != triplet:
            return None
        matches = []
        for segment in projection.segments:
            origin = segment.source_start
            if origin is not None and origin <= start < end <= origin + segment.end - segment.start:
                emitted_start = segment.start + start - origin
                emitted_end = emitted_start + len(triplet)
                if projection.text[emitted_start:emitted_end] == triplet:
                    matches.append((emitted_start, emitted_end))
        if len(matches) != 1:
            return None
        (emitted_span,) = matches
        before_gap = len(left.text) + 1
        placements.append(
            FormulaPlacement(
                path,
                formula.path,
                formula.xml_sha256,
                (start, end),
                emitted_span,
                start + before_gap,
                emitted_span[0] + before_gap,
            )
        )
    return tuple(placements)
