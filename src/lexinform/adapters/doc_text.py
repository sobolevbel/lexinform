"""Text of legacy Word documents (.doc, the Word 97-2003 binary format).

RCL still publishes about a tenth of its files this way. The format ([MS-DOC]) keeps the text in
the `WordDocument` stream of an OLE container, but not in order: the *piece table* in the table
stream lists runs of characters ("pieces") with their file positions, each either 8-bit
Windows-1252 or UTF-16LE. This module reads exactly that, which is what every text extractor for
the format does (Apache POI, wvWare, antiword); formatting, pictures and fields are not needed.

The one property it does read is the table row mark, because without it a table is unreadable:
Word writes the end of a cell and the end of a row as the same character, so a bill's tabela
zgodności arrived as one line of tabs tens of thousands of characters long.

Anything the parser does not understand (Word 6/95, encrypted files, damaged streams) yields an
empty string with a warning, so such a document is analysed from its metadata, as before.
"""

import io
import logging
import struct
from collections.abc import Iterator, Sequence

import olefile

from lexinform.sections import PAGE_BREAK

log = logging.getLogger(__name__)

_WORD97_NFIB = 0x00C1  # the first version whose FIB layout this parser knows
_F_ENCRYPTED = 0x0100
_F_WHICH_TBL_STM = 0x0200
_FC_COMPRESSED = 0x40000000
_FC_MASK = 0x3FFFFFFF
_FIB_CCP = 0x4C  # FibRgLw97: ccpText, ccpFtn, ccpHdd, reserved, ccpAtn, ccpEdn, …
_FIB_FC_PLCF_BTE_PAPX = 0x102  # FibRgFcLcb97.fcPlcfBtePapx / lcbPlcfBtePapx: paragraph properties
_FIB_FC_CLX = 0x1A2  # FibRgFcLcb97.fcClx / lcbClx: where the piece table lives
# The subdocuments the CP space holds, in its own order. A bill's numbered notes ("Zmiany tekstu
# jednolitego …", the EU-implementation note) are footnotes in some files and endnotes in others,
# so both are text; headers are page numbers and the footnote separators, comments are a
# reviewer's remarks beside the text, and a text box is a diagram's label.
_SUBDOCUMENTS = ("text", "footnotes", "headers", "reserved", "comments", "endnotes")
_READ = ("text", "footnotes", "endnotes")

_FKP_SIZE = 512  # a PapxFkp is one 512-byte page of the WordDocument stream
_BX_PAP_SIZE = 13  # BXPap: the offset of a PapxInFkp, then the paragraph height
_PN_MASK = 0x003FFFFF  # PnFkpPapx.pn: the page number, in the low 22 bits
_SPRM_P_F_TTP = 0x2417  # "this paragraph mark ends a table row"
_SPRM_P_HUGE_PAPX = 0x6646  # "the properties did not fit; they are in the Data stream"
_SPRM_T_DEF_TABLE = 0xD608  # the row's column definitions: the one two-byte length
_OPERAND_SIZES = {0: 1, 1: 1, 2: 2, 3: 4, 4: 2, 5: 2, 7: 3}  # by Sprm.spra; 6 is variable

_FIELD_BEGIN, _FIELD_SEPARATOR, _FIELD_END = "\x13", "\x14", "\x15"
_ROW_END = "\r"  # a row mark is a paragraph mark once it is told from a cell mark
_CONTROL = {
    "\r": "\n",  # paragraph mark
    "\x0b": "\n",  # line break
    "\x07": "\t",  # end of a table cell
    "\x0c": PAGE_BREAK,  # page or section break
    "\x1e": "-",  # non-breaking hyphen
    "\x1f": "",  # optional hyphen
    "\xa0": " ",
    "\x01": "",  # picture or OLE anchor
    "\x02": "",  # footnote reference mark
    "\x08": "",  # drawn object anchor
    "\x05": "",  # annotation reference
}


class DocFormatError(ValueError):
    """The file is not a Word document this parser can read."""


class DocTextExtractor:
    """Text of a .doc file: paragraphs on their own lines, table rows on theirs with the cells
    tab-separated, `\\f` at page breaks, field results kept and field codes (HYPERLINK, PAGE,
    TOC) dropped."""

    def pages(self, data: bytes) -> int:
        return 0

    def select_pages(self, data: bytes, pages: Sequence[int]) -> bytes:
        return data

    def extract(self, data: bytes) -> str:
        try:
            return _extract(data)
        except (DocFormatError, OSError, struct.error, ValueError, IndexError) as exc:
            log.warning(".doc not read (%s: %s); no text", type(exc).__name__, exc)
            return ""


def _extract(data: bytes) -> str:
    if not olefile.isOleFile(data):
        raise DocFormatError("not an OLE container")
    with olefile.OleFileIO(io.BytesIO(data)) as ole:
        if not ole.exists("WordDocument"):
            raise DocFormatError("no WordDocument stream")
        word = ole.openstream("WordDocument").read()
        table_name = table_stream_name(word)
        if not ole.exists(table_name):
            raise DocFormatError(f"no {table_name} stream")
        table = ole.openstream(table_name).read()
        extra = ole.openstream("Data").read() if ole.exists("Data") else b""
    return word_text(word, table, extra)


def table_stream_name(word_document: bytes) -> str:
    """Which of the two table streams the FIB says holds the piece table."""
    if len(word_document) < 0x0C:
        raise DocFormatError("WordDocument stream too short for a FIB")
    flags = struct.unpack_from("<H", word_document, 0x0A)[0]
    return "1Table" if flags & _F_WHICH_TBL_STM else "0Table"


def word_text(word_document: bytes, table: bytes, data: bytes = b"") -> str:
    """The text of a Word 97-2003 file from its streams: the document, its footnotes and its
    endnotes, in reading order, cleaned of field codes and Word's control characters. Raises
    `DocFormatError` for what the parser cannot read (older versions, encryption, damage)."""
    word = word_document
    if len(word) < _FIB_FC_CLX + 8:
        raise DocFormatError("WordDocument stream too short for a FIB")
    if struct.unpack_from("<H", word, 0)[0] != 0xA5EC:
        raise DocFormatError("not a Word binary file")
    nfib = struct.unpack_from("<H", word, 2)[0]
    if nfib < _WORD97_NFIB:
        raise DocFormatError(f"Word 6/95 file (nFib 0x{nfib:04X}) is not supported")
    flags = struct.unpack_from("<H", word, 0x0A)[0]
    if flags & _F_ENCRYPTED:
        raise DocFormatError("encrypted document")
    fc_clx, lcb_clx = struct.unpack_from("<II", word, _FIB_FC_CLX)
    if lcb_clx == 0 or fc_clx + lcb_clx > len(table):
        raise DocFormatError("piece table out of range")
    pieces = _piece_table(table[fc_clx : fc_clx + lcb_clx])
    rows = _row_end_marks(word, table, data)
    return _clean(_read_pieces(word, pieces, spans=_spans(word), row_ends=rows))


def _spans(word: bytes) -> list[tuple[int, int]]:
    """The character positions of the subdocuments worth reading. They follow one another in the
    order `_SUBDOCUMENTS` names, each as long as its own count in the FIB."""
    counts = struct.unpack_from(f"<{len(_SUBDOCUMENTS)}I", word, _FIB_CCP)
    spans, start = [], 0
    for name, count in zip(_SUBDOCUMENTS, counts, strict=True):
        if name in _READ and count:
            spans.append((start, start + count))
        start += count
    return spans


def _piece_table(clx: bytes) -> list[tuple[int, int, int, bool]]:
    """(cp_start, cp_end, file offset, compressed) per piece, from the Clx structure: a run of
    Prc entries (property modifiers, skipped) followed by the Pcdt with the PlcPcd."""
    pos = 0
    while pos < len(clx) and clx[pos] == 0x01:
        (cb,) = struct.unpack_from("<h", clx, pos + 1)
        if cb < 0:  # a damaged file; without the check the loop would never end
            raise DocFormatError("negative property modifier length")
        pos += 3 + cb
    if pos >= len(clx) or clx[pos] != 0x02:
        raise DocFormatError("piece table (Pcdt) not found")
    (lcb,) = struct.unpack_from("<I", clx, pos + 1)
    plc = clx[pos + 5 : pos + 5 + lcb]
    count = (lcb - 4) // 12  # n+1 CPs of 4 bytes, then n Pcd of 8 bytes
    if count <= 0 or len(plc) < lcb:
        raise DocFormatError("empty or truncated piece table")
    cps = struct.unpack_from(f"<{count + 1}I", plc, 0)
    pieces = []
    for i in range(count):
        fc_raw = struct.unpack_from("<I", plc, (count + 1) * 4 + i * 8 + 2)[0]
        compressed = bool(fc_raw & _FC_COMPRESSED)
        fc = fc_raw & _FC_MASK
        pieces.append((cps[i], cps[i + 1], fc // 2 if compressed else fc, compressed))
    return pieces


def _read_pieces(
    word: bytes,
    pieces: list[tuple[int, int, int, bool]],
    *,
    spans: list[tuple[int, int]],
    row_ends: frozenset[int],
) -> str:
    parts: list[str] = []
    for cp_start, cp_end, offset, compressed in pieces:
        if cp_end < cp_start:
            raise DocFormatError("piece spans backwards in the character positions")
        width = 1 if compressed else 2
        for span_start, span_end in spans:
            first, last = max(cp_start, span_start), min(cp_end, span_end)
            if first >= last:
                continue
            fc = offset + width * (first - cp_start)
            end = fc + width * (last - first)
            if end > len(word):  # a silent short read would hand the model a truncated text
                raise DocFormatError("piece points past the end of the WordDocument stream")
            chunk = word[fc:end].decode("cp1252" if compressed else "utf-16-le", errors="replace")
            parts.append(_mark_rows(chunk, fc, width, row_ends) if row_ends else chunk)
    return "".join(parts)


def _mark_rows(chunk: str, fc: int, width: int, row_ends: frozenset[int]) -> str:
    """Turn the cell marks that end a table row into paragraph marks, so a row becomes a line."""
    parts: list[str] = []
    cut, at = 0, chunk.find("\x07")
    while at >= 0:
        if fc + width * (at + 1) in row_ends:
            parts.append(chunk[cut:at])
            parts.append(_ROW_END)
            cut = at + 1
        at = chunk.find("\x07", at + 1)
    if not parts:
        return chunk
    parts.append(chunk[cut:])
    return "".join(parts)


def _row_end_marks(word: bytes, table: bytes, data: bytes) -> frozenset[int]:
    """The file position just past every cell mark that ends a table row.

    Word writes the end of a cell and the end of a row as the same character (0x07) and only the
    paragraph properties tell them apart, by `sprmPFTtp`. They live in the PAPX bin table:
    `PlcBtePapx` names the 512-byte `PapxFkp` pages of the WordDocument stream, and each page
    holds the file positions of the paragraphs that end in it together with their properties.

    Properties are decoration, so nothing here refuses a document: a bin table that is missing or
    does not parse leaves the marks unknown and a table reads as it did before, tabs only.
    """
    fc, lcb = struct.unpack_from("<II", word, _FIB_FC_PLCF_BTE_PAPX)
    if lcb < 12 or fc + lcb > len(table):
        return frozenset()
    plc = table[fc : fc + lcb]
    count = (lcb - 4) // 8  # n+1 file positions of 4 bytes, then n page numbers of 4 bytes
    ends: set[int] = set()
    for i in range(count):
        page_number = struct.unpack_from("<I", plc, (count + 1) * 4 + i * 4)[0] & _PN_MASK
        page = word[page_number * _FKP_SIZE : (page_number + 1) * _FKP_SIZE]
        if len(page) < _FKP_SIZE:
            continue
        paragraphs = page[_FKP_SIZE - 1]
        rgbx = 4 * (paragraphs + 1)
        if paragraphs == 0 or rgbx + _BX_PAP_SIZE * paragraphs > _FKP_SIZE - 1:
            continue
        positions = struct.unpack_from(f"<{paragraphs + 1}I", page, 0)
        for p in range(paragraphs):
            offset = page[rgbx + _BX_PAP_SIZE * p]
            if offset == 0 or 2 * offset + 2 > _FKP_SIZE:  # 0: the paragraph has no properties
                continue
            if _ends_a_row(_paragraph_properties(page[2 * offset :]), data, istd=True):
                ends.add(positions[p + 1])
    return frozenset(ends)


def _paragraph_properties(papx: bytes) -> bytes:
    """The properties of one `PapxInFkp`, whose length is given in 2-byte units in one of two
    shapes: a non-zero first byte counts them itself, a zero one hands that over to the second."""
    length = papx[0]
    return papx[1 : 2 * length] if length else papx[2 : 2 + 2 * papx[1]]


def _ends_a_row(properties: bytes, data: bytes, *, istd: bool) -> bool:
    """Whether `sprmPFTtp` is set, following the properties into the Data stream when they were
    too long for their page. What waits there is a bare `grpprl`, with no style index in front of
    it (verified on RCL's dokument68498, whose 300 rows are all stored that way)."""
    for sprm, operand in _properties(properties, start=2 if istd else 0):
        if sprm == _SPRM_P_F_TTP and operand[0]:
            return True
        if sprm == _SPRM_P_HUGE_PAPX and len(operand) == 4:
            at = struct.unpack_from("<I", operand, 0)[0]
            if at + 2 > len(data):
                continue
            (length,) = struct.unpack_from("<H", data, at)
            huge = data[at + 2 : at + 2 + length]
            if len(huge) == length and _ends_a_row(huge, b"", istd=False):
                return True
    return False


def _properties(grpprl: bytes, *, start: int) -> Iterator[tuple[int, bytes]]:
    """Every property of a `grpprl`: a two-byte sprm and the operand its `spra` field sizes. A
    length that does not fit ends the walk, because from there on the positions are guesses."""
    at = start
    while at + 2 <= len(grpprl):
        (sprm,) = struct.unpack_from("<H", grpprl, at)
        at += 2
        size = _operand_size(sprm, grpprl, at)
        if size is None or size <= 0 or at + size > len(grpprl):
            return
        yield sprm, grpprl[at : at + size]
        at += size


def _operand_size(sprm: int, grpprl: bytes, at: int) -> int | None:
    fixed = _OPERAND_SIZES.get(sprm >> 13)
    if fixed is not None:
        return fixed
    if at >= len(grpprl):
        return None
    if sprm == _SPRM_T_DEF_TABLE:
        return struct.unpack_from("<H", grpprl, at)[0] if at + 2 <= len(grpprl) else None
    return 1 + grpprl[at]


def _clean(raw: str) -> str:
    """Drop field codes, map Word's control characters, tidy whitespace around page breaks."""
    out: list[str] = []
    # One entry per open field, True while in its code part (between 0x13 and 0x14): the code is
    # dropped, the result (between 0x14 and 0x15) kept, a field without a result vanishes.
    in_code: list[bool] = []
    for ch in raw:
        if ch == _FIELD_BEGIN:
            in_code.append(True)
        elif ch == _FIELD_SEPARATOR:
            if in_code:
                in_code[-1] = False
        elif ch == _FIELD_END:
            if in_code:
                in_code.pop()
        elif any(in_code):
            continue
        else:
            out.append(_CONTROL.get(ch, ch))
    lines = [line.rstrip() for line in "".join(out).split("\n")]
    text = "\n".join(lines)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return (
        text.replace(f"\n{PAGE_BREAK}", PAGE_BREAK).replace(f"{PAGE_BREAK}\n", PAGE_BREAK).strip()
    )
