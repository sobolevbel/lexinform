"""Read explicit CFSpec assignments for source character positions in the counted DOC profile."""

import struct
from bisect import bisect_right
from dataclasses import dataclass
from itertools import pairwise
from typing import Literal

from lexinform.adapters.doc_notes import DocNoteFormatError, _read_fib
from lexinform.models.doc_notes import CfSpecSource

_MAX_INDEX_ENTRIES = 65_536
_MAX_REFERENCE_CPS = 256
_MAX_CLX_BYTES = 4 * 1024 * 1024
_CF_SPEC = 0x0855
_PRM0_CODES = frozenset(
    bytes.fromhex(
        "00 04 05 07 08 09 0c 0d 0e 0f 18 19 1d 25 2c 32 33 35 36 37 38 39 3a "
        "41 42 43 47 4b 4d 4e 4f 50 51 53 55 56 57 58 59 5a 5b 5c 5e 62 68 "
        "73 74 75 76 78 7b 7c 7e"
    )
)

type CfSpecState = Literal["on", "off", "absent", "unknown"]


@dataclass(frozen=True)
class CfSpecObservation:
    cp: int
    state: CfSpecState
    source: CfSpecSource | None = None
    reason: str | None = None


class _UnresolvedError(ValueError):
    pass


@dataclass(frozen=True)
class _Piece:
    fc: int
    width: int
    prm: int


@dataclass(frozen=True)
class _CharacterPage:
    data: memoryview[int]
    boundaries: tuple[int, ...]


def read_doc_cfspec(
    word: bytes, table: bytes, reference_cps: tuple[int, ...]
) -> tuple[CfSpecObservation, ...]:
    """Read direct CFSpec observations in input order for up to 256 main-story CPs."""
    if len(reference_cps) > _MAX_REFERENCE_CPS:
        raise ValueError("CFSpec accepts at most 256 reference CPs")
    if not reference_cps:
        return ()
    try:
        fib = _read_fib(word)
        word_view = memoryview(word)
        table_view = memoryview(table)
        meaningful = struct.unpack_from("<I", word, 0x40)[0]
        if not 1630 <= meaningful <= len(word):
            raise _UnresolvedError("invalid_word_length")
        clx = _table_range(table_view, fib.pairs, 33, _MAX_CLX_BYTES)
        piece_cps, pieces, prcs = _read_clx(clx, meaningful)
        plc = _table_range(table_view, fib.pairs, 12, 4 + 8 * _MAX_INDEX_ENTRIES)
        if len(plc) < 12 or (len(plc) - 4) % 8:
            raise _UnresolvedError("invalid_chpx_plc_size")
        page_count = (len(plc) - 4) // 8
        page_fcs = _boundaries(plc, page_count + 1)
    except DocNoteFormatError:
        return tuple(
            CfSpecObservation(cp, "unknown", reason="unsupported_fib") for cp in reference_cps
        )
    except _UnresolvedError as exc:
        return tuple(CfSpecObservation(cp, "unknown", reason=str(exc)) for cp in reference_cps)

    pages: dict[int, _CharacterPage] = {}
    observations = []
    for cp in reference_cps:
        try:
            if not 0 <= cp < fib.counts[0]:
                raise _UnresolvedError("cp_outside_main_story")
            piece_index = _covering_index(piece_cps, cp)
            piece = pieces[piece_index]
            fc = piece.fc + piece.width * (cp - piece_cps[piece_index])
            page_index = _covering_index(page_fcs, fc)
            pn_offset = 4 * (page_count + 1 + page_index)
            pn = struct.unpack_from("<I", plc, pn_offset)[0] & 0x3FFFFF
            if pn not in pages:
                pages[pn] = _read_page(word_view, pn, meaningful)
            value = _chpx_value(pages[pn], fc)
            source: CfSpecSource = "chpx"
            override = _prm_value(piece.prm, prcs)
            if override is not None:
                value = override
                source = "prm1" if piece.prm & 1 else "prm0"
            observations.append(_observation(cp, value, source))
        except _UnresolvedError as exc:
            observations.append(CfSpecObservation(cp, "unknown", reason=str(exc)))
    return tuple(observations)


def _table_range(table: memoryview[int], pairs: bytes, index: int, limit: int) -> memoryview[int]:
    offset, size = struct.unpack_from("<II", pairs, index * 8)
    if not 0 < offset < len(table) or not 0 < size <= min(limit, len(table) - offset):
        raise _UnresolvedError("table_range_missing_or_outside_profile")
    return table[offset : offset + size]


def _boundaries(data: memoryview[int], count: int) -> tuple[int, ...]:
    boundaries = struct.unpack_from(f"<{count}I", data)
    if any(first >= last for first, last in pairwise(boundaries)):
        raise _UnresolvedError("unordered_boundaries")
    return boundaries


def _covering_index(boundaries: tuple[int, ...], position: int) -> int:
    index = bisect_right(boundaries, position) - 1
    if not 0 <= index < len(boundaries) - 1:
        raise _UnresolvedError("no_covering_interval")
    return index


def _read_clx(
    clx: memoryview[int], meaningful: int
) -> tuple[tuple[int, ...], tuple[_Piece, ...], tuple[memoryview[int], ...]]:
    prcs: list[memoryview[int]] = []
    cursor = 0
    while cursor < len(clx) and clx[cursor] == 1:
        if cursor + 3 > len(clx) or len(prcs) >= 32_768:
            raise _UnresolvedError("invalid_prc_header")
        size = struct.unpack_from("<h", clx, cursor + 1)[0]
        start = cursor + 3
        if not 0 <= size <= min(0x3FA2, len(clx) - start):
            raise _UnresolvedError("invalid_prc_size")
        cursor = start + size
        prcs.append(clx[start:cursor])
    if cursor + 5 > len(clx) or clx[cursor] != 2:
        raise _UnresolvedError("missing_pcdt")
    size = struct.unpack_from("<I", clx, cursor + 1)[0]
    count = (size - 4) // 12
    if (size - 4) % 12 or not 1 <= count <= _MAX_INDEX_ENTRIES or cursor + 5 + size != len(clx):
        raise _UnresolvedError("invalid_piece_table_size")
    plc = clx[cursor + 5 :]
    cps = _boundaries(plc, count + 1)
    if cps[0] != 0 or cps[-1] >= 0x7FFFFFFF:
        raise _UnresolvedError("invalid_piece_cp_range")
    descriptors = 4 * (count + 1)
    pieces = []
    for index in range(count):
        raw, prm = struct.unpack_from("<IH", plc, descriptors + 8 * index + 2)
        compressed = bool(raw & 0x40000000)
        if raw & 0x80000000 or (compressed and raw & 1):
            raise _UnresolvedError("invalid_piece_fc_flags")
        width = 1 if compressed else 2
        fc = (raw & 0x3FFFFFFF) // (2 if compressed else 1)
        if fc < 1630 or fc + width * (cps[index + 1] - cps[index]) > meaningful:
            raise _UnresolvedError("piece_outside_word_stream")
        pieces.append(_Piece(fc, width, prm))
    return cps, tuple(pieces), tuple(prcs)


def _read_page(word: memoryview[int], pn: int, meaningful: int) -> _CharacterPage:
    start = 512 * pn
    if start + 512 > meaningful:
        raise _UnresolvedError("chpx_page_outside_word_stream")
    data = word[start : start + 512]
    runs = data[511]
    if not 1 <= runs <= 101:
        raise _UnresolvedError("invalid_chpx_run_count")
    return _CharacterPage(data, _boundaries(data, runs + 1))


def _chpx_value(page: _CharacterPage, fc: int) -> int | None:
    run = _covering_index(page.boundaries, fc)
    offsets_start = 4 * len(page.boundaries)
    offset = 2 * page.data[offsets_start + run]
    if not offset:
        return None
    if not offsets_start + len(page.boundaries) - 1 <= offset < 511:
        raise _UnresolvedError("invalid_chpx_offset")
    end = offset + 1 + page.data[offset]
    if end > 511:
        raise _UnresolvedError("truncated_chpx")
    return _last_cfspec(page.data[offset + 1 : end])


def _prm_value(prm: int, prcs: tuple[memoryview[int], ...]) -> int | None:
    if prm & 1:
        index = prm >> 1
        if index >= len(prcs):
            raise _UnresolvedError("invalid_prm1_index")
        return _last_cfspec(prcs[index])
    opcode = (prm >> 1) & 0x7F
    if opcode not in _PRM0_CODES:
        raise _UnresolvedError("unsupported_prm0_code")
    return _toggle(prm >> 8) if opcode == 0x75 else None


def _last_cfspec(data: memoryview[int]) -> int | None:
    cursor = 0
    value = None
    while cursor < len(data):
        if cursor + 2 > len(data):
            raise _UnresolvedError("truncated_sprm")
        opcode = struct.unpack_from("<H", data, cursor)[0]
        cursor += 2
        if not 1 <= (opcode >> 10) & 7 <= 5:
            raise _UnresolvedError("unsupported_sprm_kind")
        spra = opcode >> 13
        width = (1, 1, 2, 4, 2, 2, 0, 3)[spra]
        if spra == 6:
            if opcode in (0xC615, 0xD608):
                raise _UnresolvedError("unsupported_special_sprm_length")
            if cursor == len(data):
                raise _UnresolvedError("truncated_sprm_length")
            width = 1 + data[cursor]
        if cursor + width > len(data):
            raise _UnresolvedError("truncated_sprm_operand")
        if opcode == _CF_SPEC:
            value = _toggle(data[cursor])
        cursor += width
    return value


def _toggle(value: int) -> int:
    if value not in (0, 1, 0x80, 0x81):
        raise _UnresolvedError("invalid_cfspec_toggle")
    return value


def _observation(cp: int, value: int | None, source: CfSpecSource) -> CfSpecObservation:
    if value is None:
        return CfSpecObservation(cp, "absent")
    if value >= 0x80:
        return CfSpecObservation(cp, "unknown", source, "style_relative_toggle")
    return CfSpecObservation(cp, "on" if value else "off", source)
