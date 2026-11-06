"""Bound the literal normative core eligible for a second-format source read."""

import re

from lexinform.sections import document_kind, law_body

MAX_REPRESENTATION_PDF_BYTES = 2_097_152
MAX_REPRESENTATION_PDF_PAGES = 30
MAX_REPRESENTATION_CORE_CHARS = 2000
MAX_REPRESENTATION_DOCX_BYTES = 262_144

_ARTICLE_ONE = re.compile(r"\bArt\. 1\.")
_COMMENCEMENT = re.compile(
    r"\bArt\. [1-9][0-9]*\. Ustawa wchodzi w życie po upływie [1-9][0-9]* dni od dnia ogłoszenia\.$"
)
_ANNEX = re.compile(r"\bzałączni\w*", re.IGNORECASE)
_ANNEX_HEADING = re.compile(r"(?:^|[\n\f])[^\S\n\f]*załączni", re.IGNORECASE)


def exact_core(text: str, *, pdf: bool) -> str:
    normalized = " ".join(text.split())
    core = " ".join((law_body(text, preserve_numbers=True) or "").split())
    first = _ARTICLE_ONE.search(normalized)
    if (
        first is None
        or not _ARTICLE_ONE.match(core)
        or not normalized.startswith(core, first.start())
        or _COMMENCEMENT.search(core) is None
        or len(core) < 200
        or _ANNEX.search(core) is not None
        or _ANNEX_HEADING.search(text) is not None
        or document_kind(text) != "bill"
    ):
        raise ValueError("Expected one literal normative core through final commencement")
    tail = normalized[first.start() + len(core) :].lstrip()
    if (pdf and not tail.startswith("UZASADNIENIE ")) or (not pdf and tail):
        raise ValueError("Expected PDF justification boundary and exact draft-only Word end")
    return core
