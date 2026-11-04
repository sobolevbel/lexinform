"""Declared DOC note CP links keep numbering, sentinels and story coordinates distinct."""

import struct

import pytest

from lexinform.adapters.doc_notes import DocNoteFormatError, read_doc_note_links

FIB_PAIRS = 0x9A
NEW_FIB = FIB_PAIRS + 183 * 8


def note_streams(
    *,
    refs: tuple[int, ...] = (2, 7),
    starts: tuple[int, ...] = (0, 4, 9),
    flags: tuple[int, ...] = (1, 0),
    endnotes: bool = False,
    sentinel: int = 0xFFFFFFFF,
) -> tuple[bytes, bytes]:
    word = bytearray(2048)
    struct.pack_into("<HH", word, 0, 0xA5EC, 0xC1)
    struct.pack_into("<H", word, 10, 0x200)
    struct.pack_into("<H", word, 32, 14)
    struct.pack_into("<H", word, 62, 22)
    note_count = starts[-1] + 1
    counts = (20, 0 if endnotes else note_count, 5, 0, 3, note_count if endnotes else 0, 0, 0)
    struct.pack_into("<8i", word, 0x4C, *counts)
    struct.pack_into("<H", word, 0x98, 183)
    struct.pack_into("<HH4H", word, NEW_FIB, 5, 0x112, 0, 0, 0, 0)
    ref = struct.pack(f"<{len(refs) + 1}I", *refs, sentinel)
    ref += struct.pack(f"<{len(flags)}H", *flags)
    txt = struct.pack(f"<{len(starts) + 1}I", *starts, sentinel)
    pair = 46 if endnotes else 2
    struct.pack_into("<IIII", word, FIB_PAIRS + pair * 8, 0, len(ref), len(ref), len(txt))
    return bytes(word), ref + txt


def replace_word(word: bytes, offset: int, fmt: str, value: int) -> bytes:
    changed = bytearray(word)
    struct.pack_into(fmt, changed, offset, value)
    return bytes(changed)


def test_declared_links_preserve_numbering_flags_and_relative_cp_ranges() -> None:
    word, table = note_streams(flags=(60000, 0))

    result = read_doc_note_links(word, table)

    assert result.validation == "declared_cp_only"
    assert result.table_name == "1Table"
    assert result.main_characters == 20
    assert result.footnote_characters == 10
    assert [(link.kind, link.reference_cp, link.numbering_flag) for link in result.links] == [
        ("footnote", 2, 60000),
        ("footnote", 7, 0),
    ]
    assert [
        (link.note_start_cp, link.note_end_cp, link.story_start_cp) for link in result.links
    ] == [(0, 4, 20), (4, 9, 20)]


def test_endnote_base_counts_headers_and_comments_without_reading_them() -> None:
    word, table = note_streams(endnotes=True)

    result = read_doc_note_links(word, table)

    assert result.endnote_characters == 10
    assert all(link.kind == "endnote" and link.story_start_cp == 28 for link in result.links)


@pytest.mark.parametrize("sentinel", [0, 1, 0x7FFFFFFF, 0xFFFFFFFF])
def test_undefined_final_cps_are_not_link_positions(sentinel: int) -> None:
    word, table = note_streams(sentinel=sentinel)

    assert len(read_doc_note_links(word, table).links) == 2


@pytest.mark.parametrize("length", [0, 31, 33, 63, 151, 152, NEW_FIB, NEW_FIB + 10])
def test_truncated_counted_fib_is_rejected(length: int) -> None:
    word, table = note_streams()

    with pytest.raises(DocNoteFormatError):
        read_doc_note_links(word[:length], table)


@pytest.mark.parametrize(
    ("offset", "fmt", "value"),
    [
        (0, "<H", 0),
        (2, "<H", 0x112),
        (10, "<H", 0x100),
        (32, "<H", 13),
        (62, "<H", 21),
        (0x98, "<H", 93),
        (NEW_FIB, "<H", 2),
        (NEW_FIB + 2, "<H", 0x10C),
        (0x4C, "<i", -1),
        (0x50, "<i", -1),
        (0x58, "<i", 1),
        (0x68, "<i", -1),
        (0x4C, "<i", 0x7FFFFFFF),
    ],
)
def test_unsupported_or_damaged_headers_do_not_produce_links(
    offset: int, fmt: str, value: int
) -> None:
    word, table = note_streams()

    with pytest.raises(DocNoteFormatError):
        read_doc_note_links(replace_word(word, offset, fmt, value), table)


def test_unencrypted_obfuscation_bit_is_ignored() -> None:
    word, table = note_streams()

    assert len(read_doc_note_links(replace_word(word, 10, "<H", 0x8200), table).links) == 2


@pytest.mark.parametrize("refs", [(2, 2), (7, 2), (2, 20), (2, 0x7FFFFFFF)])
def test_reference_positions_are_unique_ordered_and_inside_main_story(
    refs: tuple[int, ...],
) -> None:
    word, table = note_streams(refs=refs)

    with pytest.raises(DocNoteFormatError):
        read_doc_note_links(word, table)


@pytest.mark.parametrize("starts", [(1, 4, 9), (0, 0, 9), (0, 9, 4), (0, 0x7FFFFFFF, 9)])
def test_note_positions_are_ordered_and_begin_at_story_start(starts: tuple[int, ...]) -> None:
    word, table = note_streams(starts=starts)

    with pytest.raises(DocNoteFormatError):
        read_doc_note_links(word, table)


def test_note_count_and_reference_count_must_match() -> None:
    word, table = note_streams(refs=(2,), flags=(1,))

    with pytest.raises(DocNoteFormatError):
        read_doc_note_links(word, table)


def test_note_boundary_matches_declared_story_length() -> None:
    word, table = note_streams()

    with pytest.raises(DocNoteFormatError):
        read_doc_note_links(replace_word(word, 0x50, "<i", 11), table)


@pytest.mark.parametrize(("pair", "component", "value"), [(2, 0, 999), (2, 4, 15), (3, 4, 15)])
def test_plcf_bounds_and_cardinality_are_checked(pair: int, component: int, value: int) -> None:
    word, table = note_streams()

    with pytest.raises(DocNoteFormatError):
        read_doc_note_links(
            replace_word(word, FIB_PAIRS + pair * 8 + component, "<I", value), table
        )


def test_empty_stories_ignore_unused_fc_values() -> None:
    word, _ = note_streams()
    word = replace_word(word, 0x50, "<i", 0)
    for pair in (2, 3, 46, 47):
        word = replace_word(word, FIB_PAIRS + pair * 8, "<I", 0xFFFFFFFF)
        word = replace_word(word, FIB_PAIRS + pair * 8 + 4, "<I", 0)

    assert read_doc_note_links(word, b"").links == ()


@pytest.mark.parametrize("bound", [0, 1])
def test_bounded_reader_never_silently_truncates(bound: int) -> None:
    word, table = note_streams()

    with pytest.raises(DocNoteFormatError):
        read_doc_note_links(word, table, max_notes=bound)


def test_exact_capacity_preserves_every_declared_link() -> None:
    word, table = note_streams()

    assert len(read_doc_note_links(word, table, max_notes=2).links) == 2


def test_negative_limit_is_an_invalid_reader_option() -> None:
    word, table = note_streams()

    with pytest.raises(ValueError, match="max_notes"):
        read_doc_note_links(word, table, max_notes=-1)


def test_declared_cp_positions_are_preserved_without_decoding_text() -> None:
    word, table = note_streams(refs=(3, 8))

    result = read_doc_note_links(word, table)

    assert [link.reference_cp for link in result.links] == [3, 8]
    assert [link.note_end_cp for link in result.links] == [4, 9]


def test_zero_reference_endnote_story_keeps_its_terminal_boundary() -> None:
    word, table = note_streams(refs=(), starts=(12,), flags=(), endnotes=True)

    result = read_doc_note_links(word, table)

    assert result.endnote_characters == 13
    assert result.links == ()


def test_both_note_kinds_share_one_total_capacity() -> None:
    word, table = note_streams()
    end_word, end_table = note_streams(endnotes=True)
    word = replace_word(word, 0x60, "<i", 10)
    offset = FIB_PAIRS + 46 * 8
    copied = bytearray(word)
    values = struct.unpack_from("<4I", end_word, offset)
    struct.pack_into(
        "<4I", copied, offset, values[0] + len(table), values[1], values[2] + len(table), values[3]
    )
    combined = table + end_table

    result = read_doc_note_links(bytes(copied), combined, max_notes=4)

    assert [link.kind for link in result.links] == ["footnote", "footnote", "endnote", "endnote"]
    assert [link.story_start_cp for link in result.links] == [20, 20, 38, 38]
    with pytest.raises(DocNoteFormatError):
        read_doc_note_links(bytes(copied), combined, max_notes=3)
