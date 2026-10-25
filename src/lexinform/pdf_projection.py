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
