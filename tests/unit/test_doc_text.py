"""The Word 97-2003 parser on synthetic streams built to the [MS-DOC] layout: piece table,
encodings, field codes, control characters, the footnote/header boundary, and what it refuses."""

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
WORD97 = 0x00C1


def word_streams(
    pieces: list[tuple[str, bool]],
    *,
    ccp_text: int | None = None,
    ccp_ftn: int = 0,
    nfib: int = WORD97,
    encrypted: bool = False,
    which_table: int = 1,
    prc: bytes = b"",
) -> tuple[bytes, bytes]:
    """A `WordDocument` stream (FIB + the text of every piece) and its table stream (Clx).

    `pieces` are (text, compressed) in CP order; compressed pieces are stored as Windows-1252,
    the others as UTF-16LE. `ccp_text` defaults to every character of every piece.
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
    struct.pack_into("<II", fib, 0x4C, total, ccp_ftn)
    struct.pack_into("<II", fib, 0x1A2, 16, len(clx))
    return bytes(fib) + bytes(body), table


# --------------------------------------------------------------------------- pieces


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


# --------------------------------------------------------------------------- cleaning


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


# --------------------------------------------------------------------------- what is read


def test_main_text_and_footnotes_are_read_headers_and_comments_are_not() -> None:
    main, footnote, header = "Ustawa.\r", "1) Dz. U. poz. 1.\r", "Nagłówek strony\r"
    word, table = word_streams(
        [(main, False), (footnote, False), (header, False)],
        ccp_text=len(main),
        ccp_ftn=len(footnote),
    )

    assert word_text(word, table) == "Ustawa.\n1) Dz. U. poz. 1."


def test_the_fib_names_the_table_stream() -> None:
    word_1, _ = word_streams([("x", True)], which_table=1)
    word_0, _ = word_streams([("x", True)], which_table=0)

    assert table_stream_name(word_1) == "1Table"
    assert table_stream_name(word_0) == "0Table"


# --------------------------------------------------------------------------- refusals


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


def test_a_piece_pointing_past_the_stream_is_refused_instead_of_read_short() -> None:
    word, table = word_streams([("Art. 1. Cudzoziemiec składa wniosek.", False)])

    with pytest.raises(DocFormatError, match="past the end"):
        word_text(word[:-10], table)  # the WordDocument stream lost its tail


def test_the_extractor_turns_refusals_into_empty_text() -> None:
    assert DocTextExtractor().extract(b"not an OLE file at all") == ""
