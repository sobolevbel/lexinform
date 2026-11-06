"""Bounded table-declared note links in the source CP space of Word 2007 binary files."""

import struct
from dataclasses import dataclass
from typing import Literal

from lexinform.models.doc_notes import DocNoteProjection

_MAX_CP = 0x7FFFFFFF


class DocNoteFormatError(ValueError):
    """Note declarations do not satisfy the supported counted-FIB profile."""


@dataclass(frozen=True)
class DocNoteLink:
    kind: Literal["footnote", "endnote"]
    reference_cp: int
    note_start_cp: int
    note_end_cp: int
    story_start_cp: int
    numbering_flag: int


@dataclass(frozen=True)
class DocNoteLinks:
    table_name: str
    links: tuple[DocNoteLink, ...]
    main_characters: int
    footnote_characters: int
    endnote_characters: int
    validation: Literal["declared_cp_only"] = "declared_cp_only"


@dataclass(frozen=True)
class _Fib:
    table_name: str
    counts: tuple[int, ...]
    pairs: bytes


def read_doc_note_links(word: bytes, table: bytes, *, max_notes: int = 256) -> DocNoteLinks:
    """Read declared CP links without validating text characters, pieces or CFSpec properties."""
    if type(max_notes) is not int or max_notes < 0:
        raise ValueError("max_notes must be a nonnegative integer")
    fib = _read_fib(word)
    main, footnotes, headers, _, comments, endnotes, _, _ = fib.counts
    foot_links = _links(
        table,
        fib.pairs,
        kind="footnote",
        pair=2,
        main=main,
        story_start=main,
        characters=footnotes,
        remaining=max_notes,
    )
    end_links = _links(
        table,
        fib.pairs,
        kind="endnote",
        pair=46,
        main=main,
        story_start=main + footnotes + headers + comments,
        characters=endnotes,
        remaining=max_notes - len(foot_links),
    )
    return DocNoteLinks(fib.table_name, tuple(foot_links + end_links), main, footnotes, endnotes)


def _read_fib(word: bytes) -> _Fib:
    if len(word) < 34:
        raise DocNoteFormatError("truncated FIB base")
    signature, base_version = struct.unpack_from("<HH", word)
    flags = struct.unpack_from("<H", word, 10)[0]
    if signature != 0xA5EC or base_version != 0x00C1:
        raise DocNoteFormatError("unsupported FIB base")
    if flags & 0x0100:
        raise DocNoteFormatError("encrypted WordDocument")
    position = 32
    _, position = _section(word, position, count=14, width=2)
    long_words, position = _section(word, position, count=22, width=4)
    pairs, position = _section(word, position, count=183, width=8)
    new_words, _ = _section(word, position, count=5, width=2)
    if struct.unpack_from("<H", new_words)[0] != 0x0112:
        raise DocNoteFormatError("unsupported effective FIB version")
    counts = struct.unpack_from("<8i", long_words, 12)
    if any(count < 0 for count in counts) or counts[3] != 0:
        raise DocNoteFormatError("invalid signed story counts")
    if sum(counts) >= _MAX_CP:
        raise DocNoteFormatError("story CP space exceeds the supported range")
    return _Fib("1Table" if flags & 0x0200 else "0Table", counts, pairs)


def _section(word: bytes, position: int, *, count: int, width: int) -> tuple[bytes, int]:
    if position + 2 > len(word):
        raise DocNoteFormatError("truncated FIB section count")
    actual = struct.unpack_from("<H", word, position)[0]
    if actual != count:
        raise DocNoteFormatError("unsupported FIB section count")
    start = position + 2
    end = start + count * width
    if end > len(word):
        raise DocNoteFormatError("truncated counted FIB section")
    return word[start:end], end


def _links(
    table: bytes,
    pairs: bytes,
    *,
    kind: Literal["footnote", "endnote"],
    pair: int,
    main: int,
    story_start: int,
    characters: int,
    remaining: int,
) -> list[DocNoteLink]:
    ref_offset, ref_size, txt_offset, txt_size = struct.unpack_from("<4I", pairs, pair * 8)
    if ref_size and (ref_size < 4 or (ref_size - 4) % 6):
        raise DocNoteFormatError(f"invalid {kind} reference PLC size")
    count = (ref_size - 4) // 6 if ref_size else 0
    if count > remaining:
        raise DocNoteFormatError("note declarations exceed max_notes")
    if not characters:
        if count or txt_size:
            raise DocNoteFormatError(f"{kind} declarations without a note story")
        _check_table_range(table, ref_offset, ref_size)
        return []
    if txt_size != 4 * (count + 2):
        raise DocNoteFormatError(f"{kind} reference/text cardinality mismatch")
    _check_table_range(table, ref_offset, ref_size)
    _check_table_range(table, txt_offset, txt_size)
    refs = struct.unpack_from(f"<{count}I", table, ref_offset) if count else ()
    starts = struct.unpack_from(f"<{count + 1}I", table, txt_offset)
    if any(cp >= main or cp >= _MAX_CP for cp in refs) or not _ascending(refs):
        raise DocNoteFormatError(f"invalid {kind} reference CPs")
    if any(cp >= characters for cp in starts) or not _ascending(starts):
        raise DocNoteFormatError(f"invalid {kind} text CPs")
    if starts[-1] != characters - 1 or (count and starts[0] != 0):
        raise DocNoteFormatError(f"invalid {kind} story boundaries")
    flags_at = ref_offset + (count + 1) * 4
    numbering = struct.unpack_from(f"<{count}H", table, flags_at) if count else ()
    return [
        DocNoteLink(kind, refs[index], starts[index], starts[index + 1], story_start, flag)
        for index, flag in enumerate(numbering)
    ]


def _check_table_range(table: bytes, offset: int, size: int) -> None:
    if size and (offset > len(table) or size > len(table) - offset):
        raise DocNoteFormatError("note PLC points outside the Table stream")


def _ascending(cps: tuple[int, ...]) -> bool:
    return all(first < last for first, last in zip(cps, cps[1:], strict=False))


@dataclass(frozen=True)
class DocNoteText:
    legacy_text: str
    links: DocNoteLinks | None
    unavailable_reason: str | None
    validation: Literal["source_characters_checked_cf_spec_unknown"] | None


@dataclass(frozen=True)
class DocNoteExtraction:
    input_sha256: str
    result: DocNoteText
    projections: tuple[DocNoteProjection, ...] = ()
