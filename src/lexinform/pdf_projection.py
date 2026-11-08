"""Compatible PDF string view over physical page records."""

import re
from collections.abc import Iterable

from lexinform.sections import PAGE_BREAK

_MULTI_BLANK_RE = re.compile(r"\n{3,}")
_TRAILING_SPACES_RE = re.compile(r"[ \t]+\n")


def _normalize_pdf_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\xa0", " ")
    text = _TRAILING_SPACES_RE.sub("\n", text)
    return _MULTI_BLANK_RE.sub("\n\n", text)


def legacy_pdf_text(pages: Iterable[str]) -> str:
    return _normalize_pdf_whitespace(PAGE_BREAK.join(pages)).strip()


def normalized_pdf_page_offsets(pages: tuple[str, ...]) -> tuple[int | None, ...]:
    """Locate each nonempty normalized page in the legacy PDF string."""
    chunks = _normalize_pdf_whitespace(PAGE_BREAK.join(pages)).split(PAGE_BREAK)
    if len(chunks) != len(pages):
        return ()
    trim = next(
        (
            sum(len(chunk) + 1 for chunk in chunks[:i]) + len(chunk) - len(chunk.lstrip())
            for i, chunk in enumerate(chunks)
            if chunk.strip()
        ),
        0,
    )
    offset = 0
    starts: list[int | None] = []
    for chunk in chunks:
        leading = len(chunk) - len(chunk.lstrip())
        starts.append(offset + leading - trim if chunk.strip() else None)
        offset += len(chunk) + 1
    return tuple(starts)


def raw_pdf_page_span(page: str, start: int, end: int) -> tuple[int, int] | None:
    """Trace a normalized literal to an unambiguous raw extractor substring."""
    if not 0 <= start < end <= len(page) <= 1_000_000 or PAGE_BREAK in page:
        return None
    text = page.replace("\r\n", "\n").replace("\xa0", " ")
    origins = [match.span() for match in re.finditer(r"\r\n|[\s\S]", page)]
    for pattern, replacement in ((_TRAILING_SPACES_RE, "\n"), (_MULTI_BLANK_RE, "\n\n")):
        mapped: list[tuple[int, int]] = []
        cursor = 0
        for match in pattern.finditer(text):
            left, right = match.span()
            mapped.extend(origins[cursor:left])
            mapped.extend([(origins[left][0], origins[right - 1][1])] * len(replacement))
            cursor = right
        mapped.extend(origins[cursor:])
        text = pattern.sub(replacement, text)
        origins = mapped
    normalized = text.strip()
    if end > len(normalized) or normalized != legacy_pdf_text((page,)):
        return None
    offset = len(text) - len(text.lstrip())
    left, right = offset + start, offset + end
    raw_start, raw_end = origins[left][0], origins[right - 1][1]
    if (
        left > 0
        and origins[left - 1][1] > raw_start
        or right < len(origins)
        and origins[right][0] < raw_end
        or legacy_pdf_text((page[raw_start:raw_end],)) != normalized[start:end]
    ):
        return None
    return raw_start, raw_end


def pdf_copy_spans(pages: tuple[str, ...]) -> tuple[tuple[int, int, int, int, int], ...]:
    """Map exact unchanged runs through the legacy PDF whitespace projection."""
    if sum(map(len, pages)) > 1_000_000 or len(pages) > 2048:
        return ()
    raw = PAGE_BREAK.join(pages)
    origins: list[tuple[int, int] | None] = []
    for index, page in enumerate(pages):
        if index:
            origins.extend([None] * len(PAGE_BREAK))
        origins.extend((index, offset) for offset in range(len(page)))
    text = raw
    for pattern, replacement in (
        (re.compile(r"\r\n"), "\n"),
        (re.compile("\xa0"), " "),
        (_TRAILING_SPACES_RE, "\n"),
        (_MULTI_BLANK_RE, "\n\n"),
    ):
        mapped: list[tuple[int, int] | None] = []
        cursor = 0
        for match in pattern.finditer(text):
            mapped.extend(origins[cursor : match.start()])
            mapped.extend([None] * len(replacement))
            cursor = match.end()
        mapped.extend(origins[cursor:])
        text = pattern.sub(replacement, text)
        origins = mapped
    left = len(text) - len(text.lstrip())
    right = len(text.rstrip())
    text = text[left:right]
    origins = origins[left:right]
    if text != legacy_pdf_text(pages):
        return ()
    rows: list[tuple[int, int, int, int, int]] = []
    start = 0
    while start < len(text) and len(rows) < 32:
        origin = origins[start]
        if origin is None:
            start += 1
            continue
        page_index, offset = origin
        end = start + 1
        while end < len(text) and origins[end] == (page_index, offset + end - start):
            end += 1
        block_end = offset + end - start
        if text[start:end] != pages[page_index][offset:block_end]:
            return ()
        rows.append((start, end, page_index, offset, block_end))
        start = end
    return tuple(rows)
