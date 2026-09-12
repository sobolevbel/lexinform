"""The Word 97-2003 parser on synthetic streams built to the [MS-DOC] layout: piece table,
encodings, field codes, control characters, the boundaries between the subdocuments, the
paragraph properties that tell a table row from a cell, and what it refuses."""

import struct
from collections.abc import Callable

import pytest

from lexinform.adapters.doc_text import (
    DocFormatError,
    DocTextExtractor,
    table_stream_name,
    word_text,
)
from lexinform.sections import PAGE_BREAK

FIB_SIZE = 0x200  # the fixed part of the FIB the parser reads sits within the first 0x1AA bytes
FKP_SIZE = 512
WORD97 = 0x00C1


def word_streams(
    pieces: list[tuple[str, bool]],
    *,
    ccp_text: int | None = None,
    ccp_ftn: int = 0,
    ccp_hdd: int = 0,
    ccp_atn: int = 0,
    ccp_edn: int = 0,
    nfib: int = WORD97,
    encrypted: bool = False,
    which_table: int = 1,
    prc: bytes = b"",
    cps: list[int] | None = None,
) -> tuple[bytes, bytes]:
    """A `WordDocument` stream (FIB + the text of every piece) and its table stream (Clx).

    `pieces` are (text, compressed) in CP order; compressed pieces are stored as Windows-1252,
    the others as UTF-16LE. `ccp_text` defaults to every character of every piece, and `cps`
    replaces the character positions the pieces are stored under (a damaged file).
    """
    body = bytearray()
    pcds: list[tuple[int, bytes]] = []  # (cp count, Pcd)
    for text, compressed in pieces:
        fc = FIB_SIZE + len(body)
        if compressed:
            body += text.encode("cp1252")
            fc_field = (fc * 2) | 0x40000000
        else:
            body += text.encode("utf-16-le")
            fc_field = fc
        pcds.append((len(text), struct.pack("<HIH", 0, fc_field, 0)))
    if cps is None:
        cps = [0]
        for count, _ in pcds:
            cps.append(cps[-1] + count)
    plc = b"".join(struct.pack("<I", cp) for cp in cps) + b"".join(pcd for _, pcd in pcds)
    clx = prc + b"\x02" + struct.pack("<I", len(plc)) + plc
    table = b"\0" * 16 + clx  # the piece table need not start the stream

    fib = bytearray(FIB_SIZE)
    struct.pack_into("<HH", fib, 0, 0xA5EC, nfib)
    flags = (0x0200 if which_table else 0) | (0x0100 if encrypted else 0)
    struct.pack_into("<H", fib, 0x0A, flags)
    total = cps[-1] if ccp_text is None else ccp_text
    struct.pack_into("<6I", fib, 0x4C, total, ccp_ftn, ccp_hdd, 0, ccp_atn, ccp_edn)
    struct.pack_into("<II", fib, 0x1A2, 16, len(clx))
    return bytes(fib) + bytes(body), table


def with_row_ends(
    word: bytes, table: bytes, marks: list[int], *, in_data_stream: bool = False
) -> tuple[bytes, bytes, bytes]:
    """The same streams plus a PAPX bin table saying that the characters at `marks` end rows.

    One `PapxFkp` page holding one paragraph per mark, each ending just past its own mark, and
    one `PlcBtePapx` naming that page. The properties are a bare `sprmPFTtp`, written either into
    the page or, when the page is said to be too small for them, into the Data stream behind
    `sprmPHugePapx`. `marks` are character positions of the single uncompressed piece.
    """
    ttp = struct.pack("<HB", 0x2417, 1)
    data = b""
    if in_data_stream:
        data = b"\0" * 8 + struct.pack("<H", len(ttp)) + ttp
        grpprl = b"\0\0" + struct.pack("<HI", 0x6646, 8)  # istd, then the offset in the stream
        papx = bytes([0, len(grpprl) // 2]) + grpprl
    else:
        grpprl = b"\0\0" + ttp
        papx = bytes([(len(grpprl) + 1) // 2]) + grpprl

    page = bytearray(FKP_SIZE)
    at = (FKP_SIZE - len(papx) - 1) & ~1
    page[at : at + len(papx)] = papx
    positions = [FIB_SIZE, *(FIB_SIZE + 2 * (mark + 1) for mark in marks)]
    struct.pack_into(f"<{len(positions)}I", page, 0, *positions)
    for i in range(len(marks)):
        page[4 * len(positions) + 13 * i] = at // 2
    page[FKP_SIZE - 1] = len(marks)

    padded = word + b"\0" * (-len(word) % FKP_SIZE)
    plc = struct.pack("<III", 0, 0x7FFFFFFF, len(padded) // FKP_SIZE)
    grown = bytearray(padded + bytes(page))
    struct.pack_into("<II", grown, 0x102, len(table), len(plc))
    return bytes(grown), table + plc, data


def test_pieces_are_concatenated_in_cp_order_whatever_their_encoding() -> None:
    word, table = word_streams(
        [("Art. 1. ", True), ("Cudzoziemiec składa wniosek", False), (" o zezwolenie.", True)]
    )

    assert word_text(word, table) == "Art. 1. Cudzoziemiec składa wniosek o zezwolenie."


def test_compressed_pieces_use_windows_1252_including_typographic_quotes() -> None:
    word, table = word_streams(
        [("\x93ustawa\x94 \x96 2026".encode("latin-1").decode("cp1252"), True)]
    )

    assert word_text(word, table) == "“ustawa” – 2026"


def test_property_modifiers_before_the_piece_table_are_skipped() -> None:
    grpprl = b"\x11\x22\x33"
    prc = b"\x01" + struct.pack("<h", len(grpprl)) + grpprl
    word, table = word_streams([("tekst", True)], prc=prc * 2)

    assert word_text(word, table) == "tekst"


def test_control_characters_become_paragraphs_tabs_page_breaks_and_hyphens() -> None:
    raw = "Tytuł\rLp.\x07Podmiot\x07\rlinia\x0bdruga\x0cStrona 2\rnie\x1ełamany\x1fdziel"
    word, table = word_streams([(raw, False)])

    # The row-end mark leaves a trailing tab, which the line clean-up removes.
    assert word_text(word, table) == (
        f"Tytuł\nLp.\tPodmiot\nlinia\ndruga{PAGE_BREAK}Strona 2\nnie-łamanydziel"
    )


def test_field_codes_are_dropped_and_field_results_kept() -> None:
    # A link with a result, an empty result, a field without a result, a field nested in the
    # result of another one.
    raw = (
        'zob. \x13 HYPERLINK "https://x" \x14art. 5\x15 oraz \x13 PAGE \x14\x15\x13 TOC \x15'
        "\x13 REF a \x14rozdział \x13 SEQ \x142\x15\x15 koniec"
    )
    word, table = word_streams([(raw, False)])

    assert word_text(word, table) == "zob. art. 5 oraz rozdział 2 koniec"


def test_anchors_and_reference_marks_vanish_and_blank_runs_shrink() -> None:
    raw = "obraz\x01 przypis\x02 rysunek\x08\r\r\r\rdalej   \r"
    word, table = word_streams([(raw, False)])

    assert word_text(word, table) == "obraz przypis rysunek\n\ndalej"


def test_the_document_its_footnotes_and_its_endnotes_are_read_and_nothing_else() -> None:
    # The subdocuments follow one another in the order the FIB counts them, and a bill's
    # "Zmiany tekstu jednolitego …" notes are endnotes as often as footnotes.
    main, footnote = "Ustawa.\r", "1) Dz. U. poz. 1.\r"
    header, comment, endnote = "Nagłówek strony\r", "Uwaga recenzenta\r", "2) Dz. U. poz. 2.\r"
    word, table = word_streams(
        [(part, False) for part in (main, footnote, header, comment, endnote)],
        ccp_text=len(main),
        ccp_ftn=len(footnote),
        ccp_hdd=len(header),
        ccp_atn=len(comment),
        ccp_edn=len(endnote),
    )

    assert word_text(word, table) == "Ustawa.\n1) Dz. U. poz. 1.\n2) Dz. U. poz. 2."


def test_a_cell_mark_that_ends_a_row_breaks_the_line_and_the_others_do_not() -> None:
    raw = "Lp.\x07Podmiot\x07\x071.\x07UdSC\x07\x07"
    word, table = word_streams([(raw, False)])
    marked, marked_table, data = with_row_ends(word, table, [12, len(raw) - 1])

    assert word_text(word, table) == "Lp.\tPodmiot\t\t1.\tUdSC"  # every mark a cell mark
    assert word_text(marked, marked_table, data) == "Lp.\tPodmiot\n1.\tUdSC"


def test_row_properties_too_long_for_their_page_are_read_from_the_data_stream() -> None:
    # A row with many columns carries its whole column layout, which does not fit in the 512-byte
    # page; Word then leaves an offset into the Data stream behind (RCL's dokument68498).
    raw = "Lp.\x07Podmiot\x07\x071.\x07UdSC\x07\x07"
    word, table, data = with_row_ends(
        *word_streams([(raw, False)]), [12, len(raw) - 1], in_data_stream=True
    )

    assert word_text(word, table, data) == "Lp.\tPodmiot\n1.\tUdSC"


def test_properties_that_cannot_be_read_leave_a_table_as_tabs_instead_of_refusing_the_file() -> (
    None
):
    raw = "Lp.\x07Podmiot\x07\x07"
    word, table, data = with_row_ends(*word_streams([(raw, False)]), [len(raw) - 1])

    assert word_text(word[:-FKP_SIZE], table, data) == "Lp.\tPodmiot"  # the page is gone


def test_the_fib_names_the_table_stream() -> None:
    word_1, _ = word_streams([("x", True)], which_table=1)
    word_0, _ = word_streams([("x", True)], which_table=0)

    assert table_stream_name(word_1) == "1Table"
    assert table_stream_name(word_0) == "0Table"


@pytest.mark.parametrize(
    ("build", "reason"),
    [
        (lambda: word_streams([("x", True)], nfib=0x0065), "Word 6/95"),
        (lambda: word_streams([("x", True)], encrypted=True), "encrypted"),
    ],
)
def test_older_and_encrypted_files_are_refused(
    build: Callable[[], tuple[bytes, bytes]], reason: str
) -> None:
    word, table = build()

    with pytest.raises(DocFormatError, match=reason):
        word_text(word, table)


def test_damaged_structures_are_refused_not_misread() -> None:
    word, table = word_streams([("tekst", True)])
    not_word = b"\x00\x00" + word[2:]

    with pytest.raises(DocFormatError, match="not a Word"):
        word_text(not_word, table)
    with pytest.raises(DocFormatError, match="out of range"):
        word_text(word, table[:20])  # the Clx is cut off
    with pytest.raises(DocFormatError, match="too short"):
        word_text(word[:100], table)
    with pytest.raises(DocFormatError, match="Pcdt"):
        word_text(word, table[:16] + b"\x07" + table[17:])  # the Pcdt marker is gone


def test_a_negative_property_modifier_length_is_refused_instead_of_looping_forever() -> None:
    # A Prc whose length is -3 would leave the parser at the same position for ever.
    word, table = word_streams([("tekst", True)], prc=b"\x01" + struct.pack("<h", -3))

    with pytest.raises(DocFormatError, match="negative"):
        word_text(word, table)


def test_a_piece_whose_span_runs_backwards_is_refused_instead_of_read_short() -> None:
    # The second piece spans -2 characters, and a plain slice of it is empty.
    word, table = word_streams(
        [("Art. 1. ", True), ("Cudzoziemiec.", True)], cps=[0, 5, 3], ccp_text=10
    )

    with pytest.raises(DocFormatError, match="backwards"):
        word_text(word, table)


def test_a_piece_pointing_past_the_stream_is_refused_instead_of_read_short() -> None:
    word, table = word_streams([("Art. 1. Cudzoziemiec składa wniosek.", False)])

    with pytest.raises(DocFormatError, match="past the end"):
        word_text(word[:-10], table)  # the WordDocument stream lost its tail


def test_the_extractor_turns_refusals_into_empty_text() -> None:
    assert DocTextExtractor().extract(b"not an OLE file at all") == ""
