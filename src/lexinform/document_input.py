"""Trace the prepared main text and separately labelled self-amendments to their selected parts."""

import hashlib

from lexinform.document_selection import ARCHIVE_EDITION_DIRECTORY_POLICY_VERSION
from lexinform.document_versions import normative_annex_key, normative_text_pieces
from lexinform.models.documents import DocumentPart, DocumentSelection
from lexinform.models.sejm import TextDocument
from lexinform.sections import PAGE_BREAK
from lexinform.text_projection import SourcePiece, TextProjection, project_pieces

_AMENDMENTS = (
    "\n\nAUTOPOPRAWKI: odrębne propozycje zmian do powyższego projektu; nie tekst jednolity.\n"
)


def append_self_amendments(text: str, amendments: tuple[DocumentPart, ...]) -> str:
    if not amendments:
        return text
    return text + _AMENDMENTS + "\n\n".join(_label(part) + part.text for part in amendments)


def self_amendment_header(first: DocumentPart) -> str:
    return _AMENDMENTS + _label(first)


def amended_input_projection(
    selection: DocumentSelection,
    trimmed: TextProjection,
    prepared: str,
    *,
    assembly: TextProjection | None = None,
) -> TextProjection | None:
    source = PAGE_BREAK.join(part.text for part in selection.parts)
    main: list[SourcePiece] = []
    amendments: list[SourcePiece] = []
    offset = 0
    for part in selection.parts:
        if part.role == "self_amendment":
            if amendments:
                amendments.append(SourcePiece("\n\n"))
            amendments.extend((SourcePiece(_label(part)), SourcePiece(part.text, offset)))
        else:
            main.append(SourcePiece(part.text, offset))
        offset += len(part.text) + len(PAGE_BREAK)
    if not main or not amendments:
        return None
    if assembly is None:
        assembly = project_pieces(source, main, separator=PAGE_BREAK)
    if (
        trimmed.source_length != len(assembly.text)
        or trimmed.source_sha256 != hashlib.sha256(assembly.text.encode()).hexdigest()
    ):
        return None
    baseline = trimmed.compose(assembly)
    pieces = [
        SourcePiece(baseline.text[segment.start : segment.end], segment.source_start)
        for segment in baseline.segments
    ]
    result = project_pieces(source, (*pieces, SourcePiece(_AMENDMENTS), *amendments))
    return result if result.text == prepared else None


def loose_normative_input_projection(
    document: TextDocument,
    selection: DocumentSelection,
    text: str,
    *,
    with_amendments: bool = False,
) -> TextProjection | None:
    parts = selection.parts
    annex_count = len(document.normative_urls)
    if (
        document.kind != "rcl"
        or not annex_count
        or document.required_urls
        or bool(document.amendment_urls) != with_amendments
        or len(parts) <= annex_count
        or any(part.members or not part.text for part in parts)
        or len({part.url for part in parts}) != len(parts)
    ):
        return None
    main = parts[0]
    annex_end = 1 + annex_count
    amendment_end = annex_end + len(document.amendment_urls)
    annexes = parts[1:annex_end]
    amendments = parts[annex_end:amendment_end]
    supporting = parts[amendment_end:]
    supporting_urls = {part.url for part in supporting}
    if (
        main.url != document.url
        or main.role not in ("bill", "unknown")
        or any(part.role != "normative_annex" for part in annexes)
        or annexes != tuple(sorted(annexes, key=lambda part: normative_annex_key(part.text)))
        or {part.url for part in annexes} != set(document.normative_urls)
        or tuple((part.role, part.url) for part in amendments)
        != tuple(("self_amendment", url) for url in document.amendment_urls)
        or tuple(part.url for part in supporting)
        != tuple(url for url in document.extra_urls if url in supporting_urls)
    ):
        return None

    return _normative_input_projection(parts, annex_count, text)


def archive_normative_input_projection(
    selection: DocumentSelection, text: str
) -> TextProjection | None:
    parts = selection.parts
    if (
        selection.policy_version != ARCHIVE_EDITION_DIRECTORY_POLICY_VERSION
        or not parts
        or not parts[0].members
        or parts[0].role != "bill"
    ):
        return None
    annex_end = next(
        (index for index, part in enumerate(parts[1:], start=1) if part.role != "normative_annex"),
        len(parts),
    )
    if annex_end == 1 or any(part.role == "normative_annex" for part in parts[annex_end:]):
        return None
    return _normative_input_projection(parts, annex_end - 1, text)


def _normative_input_projection(
    parts: tuple[DocumentPart, ...], annex_count: int, text: str
) -> TextProjection | None:
    annex_end = 1 + annex_count
    pieces = normative_text_pieces(parts[0].text, [part.text for part in parts[1:annex_end]])
    offset = sum(len(part.text) + len(PAGE_BREAK) for part in parts[:annex_end])
    for part in parts[annex_end:]:
        if part.role != "self_amendment":
            pieces.extend((SourcePiece(PAGE_BREAK), SourcePiece(part.text, offset)))
        offset += len(part.text) + len(PAGE_BREAK)
    source = PAGE_BREAK.join(part.text for part in parts)
    projection = project_pieces(source, pieces)
    return projection if projection.text == text else None


def _label(part: DocumentPart) -> str:
    return f"Źródło: {part.url}\n"
