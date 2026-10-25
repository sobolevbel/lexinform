"""Build the bounded maintenance extractor with explicit, recorded options."""

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor

MAX_BYTES = 32_000_000
OPTIONS = {"max_member_bytes": MAX_BYTES, "max_part_chars": 400_000}


def bounded_reader() -> DocumentTextExtractor:
    return DocumentTextExtractor(
        PypdfTextExtractor(),
        DocxTextExtractor(),
        DocTextExtractor(),
        max_member_bytes=OPTIONS["max_member_bytes"],
        max_part_chars=OPTIONS["max_part_chars"],
    )
