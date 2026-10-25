"""Trace the prepared main text and separately labelled self-amendments to their selected parts."""

import hashlib

from lexinform.models.documents import DocumentPart, DocumentSelection
from lexinform.sections import PAGE_BREAK
from lexinform.text_projection import SourcePiece, TextProjection, project_pieces

_AMENDMENTS = (
    "\n\nAUTOPOPRAWKI: odrębne propozycje zmian do powyższego projektu; nie tekst jednolity.\n"
)


def append_self_amendments(text: str, amendments: tuple[DocumentPart, ...]) -> str:
    if not amendments:
        return text
    return text + _AMENDMENTS + "\n\n".join(_label(part) + part.text for part in amendments)


def amended_input_projection(
    selection: DocumentSelection, trimmed: TextProjection, prepared: str
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


def _label(part: DocumentPart) -> str:
    return f"Źródło: {part.url}\n"
