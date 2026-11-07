"""Compatible PDF string view over physical page records."""

import re
from collections.abc import Iterable

from lexinform.sections import PAGE_BREAK

_MULTI_BLANK_RE = re.compile(r"\n{3,}")
_TRAILING_SPACES_RE = re.compile(r"[ \t]+\n")


def legacy_pdf_text(pages: Iterable[str]) -> str:
    text = PAGE_BREAK.join(pages).replace("\r\n", "\n").replace("\xa0", " ")
    text = _TRAILING_SPACES_RE.sub("\n", text)
    return _MULTI_BLANK_RE.sub("\n\n", text).strip()


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
