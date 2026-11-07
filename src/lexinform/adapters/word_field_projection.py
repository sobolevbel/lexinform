"""Project verified paragraph-local field fragments into the unchanged legacy Word body."""

import hashlib
from bisect import bisect_left
from dataclasses import dataclass

from lexinform.adapters.word_field_ranges import WordFieldExtraction, WordFieldRange, XmlPath
from lexinform.models.documents import WordTextBlock, WordTextExtraction
from lexinform.word_projection import _TIDY_PAGE_BREAK


@dataclass(frozen=True)
class WordBodyFieldRange:
    source: WordFieldRange
    start: int
    end: int


def project_word_field_ranges(
    extraction: WordFieldExtraction,
) -> tuple[WordBodyFieldRange, ...] | None:
    """Return exact legacy body ranges, or None when any required projection is unsupported."""
    legacy = extraction.legacy
    if extraction.state != "checked" or legacy.body_state != "read":
        return None
    if not extraction.ranges:
        return ()
    layout = _paragraph_layout(legacy)
    if layout is None:
        return None
    raw, offsets = layout
    removed: list[int] = []
    for match in _TIDY_PAGE_BREAK.finditer(raw):
        removed.extend(index for index in range(*match.span()) if raw[index] == "\n")
    text = legacy.legacy_text
    if len(raw) - len(removed) != len(text):
        return None
    paragraphs = {block.path: block for block in legacy.blocks if block.kind == "paragraph"}
    ranges = []
    for event in extraction.ranges:
        paragraph = paragraphs.get(event.paragraph_path)
        base = offsets.get(event.paragraph_path)
        if (
            paragraph is None
            or base is None
            or not 0 <= event.start < event.end <= len(paragraph.text)
            or event.paragraph_sha256 != paragraph.text_sha256
            or event.path[: len(event.paragraph_path)] != event.paragraph_path
            or len(event.path) <= len(event.paragraph_path)
        ):
            return None
        start, end = base + event.start, base + event.end
        removed_before = bisect_left(removed, start)
        if bisect_left(removed, end) != removed_before:
            return None
        start -= removed_before
        end -= removed_before
        slice_text = paragraph.text[event.start : event.end]
        if (
            text[start:end] != slice_text
            or hashlib.sha256(slice_text.encode()).hexdigest() != event.text_sha256
        ):
            return None
        ranges.append(WordBodyFieldRange(event, start, end))
    return tuple(ranges)


def _paragraph_layout(legacy: WordTextExtraction) -> tuple[str, dict[XmlPath, int]] | None:
    roots: list[WordTextBlock] = []
    ancestors: list[WordTextBlock] = []
    children: dict[XmlPath, list[WordTextBlock]] = {}
    for block in legacy.blocks:
        while ancestors and block.path[: len(ancestors[-1].path)] != ancestors[-1].path:
            ancestors.pop()
        if ancestors:
            children.setdefault(ancestors[-1].path, []).append(block)
        else:
            roots.append(block)
        ancestors.append(block)

    if tuple(block.block_id for block in roots) != legacy.legacy_blocks:
        return None
    if any(block.kind == "table_cell" for block in roots):
        return None
    paragraphs: dict[XmlPath, int] = {}
    pending = _placed(roots, 0)
    while pending:
        block, start = pending.pop()
        nested = children.get(block.path, [])
        if block.kind == "paragraph":
            if nested:
                return None
            paragraphs[block.path] = start
            continue
        separator = "\t" if block.kind == "table_row" else " "
        if any((child.kind == "table_cell") != (block.kind == "table_row") for child in nested):
            return None
        if separator.join(child.text for child in nested) != block.text:
            return None
        pending.extend(_placed(nested, start))

    return "\n".join(block.text for block in roots), paragraphs


def _placed(blocks: list[WordTextBlock], start: int) -> list[tuple[WordTextBlock, int]]:
    result = []
    for block in blocks:
        result.append((block, start))
        start += len(block.text) + 1
    return result
