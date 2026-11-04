"""Source note checks preserve text and distinguish UTF-16 CPs from Python characters."""

import builtins
import struct
from typing import Never

import pytest

from lexinform.adapters import doc_text
from lexinform.adapters.doc_text import word_text, word_text_with_notes
from tests.unit.test_doc_notes import note_streams, replace_word


def source_notes(
    *,
    main: str = "A\x02B\r",
    note: str = "\x02note\r\r",
    reference_cp: int = 1,
    automatic: bool = True,
    endnotes: bool = False,
    pieces: tuple[tuple[str, bool], ...] | None = None,
) -> tuple[bytes, bytes]:
    note_units = len(note.encode("utf-16-le")) // 2
    word, table = note_streams(
        refs=(reference_cp,),
        starts=(0, note_units - 1),
        flags=(int(automatic),),
        endnotes=endnotes,
    )
    header = bytearray(word)
    main_units = len(main.encode("utf-16-le")) // 2
    struct.pack_into(
        "<8i",
        header,
        0x4C,
        main_units,
        0 if endnotes else note_units,
        0,
        0,
        0,
        note_units if endnotes else 0,
        0,
        0,
    )
    body = bytearray()
    cps = [0]
    descriptors = bytearray()
    for text, compressed in pieces or ((main + note, False),):
        offset = len(header) + len(body)
        encoded = text.encode("cp1252" if compressed else "utf-16-le")
        body.extend(encoded)
        cps.append(cps[-1] + (len(encoded) if compressed else len(encoded) // 2))
        raw = offset * 2 | 0x40000000 if compressed else offset
        descriptors.extend(struct.pack("<HIH", 0, raw, 0))
    plc = struct.pack(f"<{len(cps)}I", *cps) + descriptors
    clx = b"\x02" + struct.pack("<I", len(plc)) + plc
    struct.pack_into("<II", header, 0x1A2, len(table), len(clx))
    struct.pack_into("<I", header, 0x40, len(header) + len(body))
    return bytes(header + body), table + clx


@pytest.mark.parametrize("endnotes", [False, True])
def test_source_symbols_and_terminal_cr_are_checked_without_changing_text(endnotes: bool) -> None:
    word, table = source_notes(endnotes=endnotes)

    result = word_text_with_notes(word, table)

    assert result.legacy_text == word_text(word, table)
    assert result.validation == "source_characters_checked_cf_spec_unknown"
    assert result.unavailable_reason is None
    assert result.links is not None, "The fixture has valid source markers and note endings"
    assert result.links.links[0].kind == ("endnote" if endnotes else "footnote")


def test_non_bmp_before_reference_and_inside_note_uses_source_units() -> None:
    word, table = source_notes(main="😀\x02B\r", note="\x02😀note\r\r", reference_cp=2)

    result = word_text_with_notes(word, table)

    assert result.legacy_text == word_text(word, table)
    assert result.links is not None, "Valid source offsets account for the surrogate pair"
    assert result.links.links[0].reference_cp == 2
    assert result.links.links[0].story_start_cp == 5
    assert result.links.links[0].note_end_cp == 8


def test_mixed_pieces_and_a_note_crossing_piece_boundaries() -> None:
    word, table = source_notes(pieces=(("A\x02", True), ("B\r\x02no", False), ("te\r\r", True)))

    result = word_text_with_notes(word, table)

    assert result.validation is not None
    assert result.legacy_text == word_text(word, table)


@pytest.mark.parametrize(("main", "note"), [("AxB\r", "\x02note\r\r"), ("A\x02B\r", "\x02note!\r")])
def test_wrong_source_characters_leave_metadata_unknown_and_legacy_intact(
    main: str, note: str
) -> None:
    word, table = source_notes(main=main, note=note)

    result = word_text_with_notes(word, table)

    assert result.legacy_text == word_text(word, table)
    assert result.links is None and result.validation is None
    assert result.unavailable_reason is not None


def test_custom_reference_is_not_required_to_be_an_automatic_marker() -> None:
    word, table = source_notes(main="A*B\r", automatic=False)

    result = word_text_with_notes(word, table)

    assert result.links is not None, "A custom reference is declared by its zero numbering flag"
    assert result.links.links[0].numbering_flag == 0


def test_meaningful_stream_boundary_is_inclusive_at_the_last_byte() -> None:
    word, table = source_notes()

    assert word_text_with_notes(word, table).links is not None
    padding_only = replace_word(word, 0x40, "<I", len(word) - 1)
    result = word_text_with_notes(padding_only, table)
    assert result.legacy_text == word_text(padding_only, table)
    assert result.links is None and result.validation is None


def test_physical_piece_reuse_does_not_mean_cp_overlap() -> None:
    word, table = source_notes(
        main="A\x02B\r", note="A\x02B\r\r", pieces=(("A\x02B\r", False), ("A\x02B\r\r", False))
    )
    changed = bytearray(table)
    clx_offset = struct.unpack_from("<I", word, 0x1A2)[0]
    second_pointer = clx_offset + 5 + 3 * 4 + 8 + 2
    struct.pack_into("<I", changed, second_pointer, 2048)
    altered = bytes(changed)

    result = word_text_with_notes(word, altered)

    assert result.legacy_text == word_text(word, altered)
    assert result.links is not None, "Distinct CP intervals can refer to reused physical text"


def test_partial_plc_record_is_unknown_even_if_legacy_parser_floors_the_count() -> None:
    word, table = source_notes()
    clx_offset, size = struct.unpack_from("<II", word, 0x1A2)
    changed = bytearray(table + b"\0")
    declared = struct.unpack_from("<I", changed, clx_offset + 1)[0]
    struct.pack_into("<I", changed, clx_offset + 1, declared + 1)
    altered_word = replace_word(word, 0x1A6, "<I", size + 1)

    result = word_text_with_notes(altered_word, bytes(changed))

    assert result.legacy_text == word_text(altered_word, bytes(changed))
    assert result.links is None and result.validation is None


def test_unsupported_note_profile_does_not_erase_existing_text() -> None:
    word, table = source_notes()
    altered = replace_word(word, 0x98, "<H", 93)

    result = word_text_with_notes(altered, table)

    assert result.legacy_text == word_text(altered, table)
    assert result.links is None


@pytest.mark.parametrize("error", [ValueError, OSError, RuntimeError, KeyboardInterrupt])
def test_unexpected_optional_failures_propagate(
    monkeypatch: pytest.MonkeyPatch, error: type[BaseException]
) -> None:
    word, table = source_notes()

    def fail(*args: object, **kwargs: object) -> Never:
        raise error("optional capture interrupted")

    monkeypatch.setattr(doc_text, "read_doc_note_links", fail)

    with pytest.raises(error):
        word_text_with_notes(word, table)


def test_nonzero_initial_piece_cp_is_unavailable_without_changing_text() -> None:
    word, table = source_notes()
    changed = bytearray(table)
    offset = struct.unpack_from("<I", word, 0x1A2)[0]
    struct.pack_into("<I", changed, offset + 5, 1)

    result = word_text_with_notes(word, bytes(changed))

    assert result.legacy_text == word_text(word, bytes(changed))
    assert result.links is None and result.validation is None


@pytest.mark.parametrize("with_notes", [False, True])
def test_in_memory_document_bytes_never_probe_a_local_filename(
    monkeypatch: pytest.MonkeyPatch, with_notes: bool
) -> None:
    def refuse_file_open(*args: object, **kwargs: object) -> Never:
        raise AssertionError("In-memory document bytes must not become a filesystem path")

    monkeypatch.setattr(builtins, "open", refuse_file_open)
    reader = doc_text.DocTextExtractor()

    if with_notes:
        result = reader.extract_with_notes(b"not-a-path")
        assert result.result.legacy_text == ""
        assert result.result.links is None
    else:
        assert reader.extract(b"not-a-path") == ""
