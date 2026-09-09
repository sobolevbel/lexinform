"""Text of legacy Word documents (.doc, the Word 97-2003 binary format).

RCL still publishes about a tenth of its files this way. The format ([MS-DOC]) keeps the text in
the `WordDocument` stream of an OLE container, but not in order: the *piece table* in the table
stream lists runs of characters ("pieces") with their file positions, each either 8-bit
Windows-1252 or UTF-16LE. This module reads exactly that, which is what every text extractor for
the format does (Apache POI, wvWare, antiword); formatting, pictures and fields are not needed.

Anything the parser does not understand (Word 6/95, encrypted files, damaged streams) yields an
empty string with a warning, so such a document is analysed from its metadata, as before.
"""

import io
import logging
import struct

import olefile

from lexinform.sections import PAGE_BREAK

log = logging.getLogger(__name__)

_WORD97_NFIB = 0x00C1  # the first version whose FIB layout this parser knows
_F_ENCRYPTED = 0x0100
_F_WHICH_TBL_STM = 0x0200
_FC_COMPRESSED = 0x40000000
_FC_MASK = 0x3FFFFFFF
_FIB_CCP_TEXT = 0x4C  # FibRgLw97.ccpText: characters of the main document
_FIB_CCP_FTN = 0x50  # FibRgLw97.ccpFtn: footnotes follow the main text in the CP space
_FIB_FC_CLX = 0x1A2  # FibRgFcLcb97.fcClx / lcbClx: where the piece table lives

_FIELD_BEGIN, _FIELD_SEPARATOR, _FIELD_END = "\x13", "\x14", "\x15"
_CONTROL = {
    "\r": "\n",  # paragraph mark
    "\x0b": "\n",  # line break
    "\x07": "\t",  # end of a table cell or row
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
    """Text of a .doc file: paragraphs on their own lines, table cells tab-separated, `\\f` at
    page breaks, field results kept and field codes (HYPERLINK, PAGE, TOC) dropped."""

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
    return word_text(word, table)


def table_stream_name(word_document: bytes) -> str:
    """Which of the two table streams the FIB says holds the piece table."""
    if len(word_document) < 0x0C:
        raise DocFormatError("WordDocument stream too short for a FIB")
    flags = struct.unpack_from("<H", word_document, 0x0A)[0]
    return "1Table" if flags & _F_WHICH_TBL_STM else "0Table"


def word_text(word_document: bytes, table: bytes) -> str:
    """The text of a Word 97-2003 file from its two streams: main document and footnotes, in
    reading order, cleaned of field codes and Word's control characters. Raises
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
    ccp_text, ccp_ftn = struct.unpack_from("<II", word, _FIB_CCP_TEXT)
    fc_clx, lcb_clx = struct.unpack_from("<II", word, _FIB_FC_CLX)
    if lcb_clx == 0 or fc_clx + lcb_clx > len(table):
        raise DocFormatError("piece table out of range")
    pieces = _piece_table(table[fc_clx : fc_clx + lcb_clx])
    text = _read_pieces(word, pieces, limit=ccp_text + ccp_ftn)
    return _clean(text)


def _piece_table(clx: bytes) -> list[tuple[int, int, int, bool]]:
    """(cp_start, cp_end, file offset, compressed) per piece, from the Clx structure: a run of
    Prc entries (property modifiers, skipped) followed by the Pcdt with the PlcPcd."""
    pos = 0
    while pos < len(clx) and clx[pos] == 0x01:
        (cb,) = struct.unpack_from("<h", clx, pos + 1)
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


def _read_pieces(word: bytes, pieces: list[tuple[int, int, int, bool]], *, limit: int) -> str:
    parts: list[str] = []
    for cp_start, cp_end, offset, compressed in pieces:
        if cp_start >= limit:
            break
        length = min(cp_end, limit) - cp_start
        if compressed:
            chunk = word[offset : offset + length]
            parts.append(chunk.decode("cp1252", errors="replace"))
        else:
            chunk = word[offset : offset + 2 * length]
            parts.append(chunk.decode("utf-16-le", errors="replace"))
    return "".join(parts)


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
