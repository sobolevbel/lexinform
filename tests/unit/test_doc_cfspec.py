import struct

import pytest

from lexinform.adapters.doc_cfspec import CfSpecObservation, read_doc_cfspec
from tests.unit.test_doc_note_extraction import source_notes

ON = bytes.fromhex("55 08 01")
OFF = bytes.fromhex("55 08 00")


def streams(
    payload: bytes,
    *,
    prm: int = 0,
    prc: bytes | None = None,
    runs: int = 1,
    rgb: int = 10,
    malformed_clx: bool = False,
    compressed: bool = False,
) -> tuple[bytearray, bytearray]:
    pieces = (("A\x02B\r\x02note\r\r", True),) if compressed else None
    word_bytes, table_bytes = source_notes(pieces=pieces)
    word = bytearray(word_bytes)
    table = bytearray(table_bytes)
    clx_offset, _ = struct.unpack_from("<II", word, 0x1A2)
    struct.pack_into("<H", table, len(table) - 2, prm)
    if prc is not None:
        table[clx_offset:clx_offset] = b"\x01" + struct.pack("<h", len(prc)) + prc
    if malformed_clx:
        table.append(0)
    struct.pack_into("<I", word, 0x1A6, len(table) - clx_offset)
    struct.pack_into("<II", word, 0xFA, len(table), 12)
    fc = 2048 + (1 if compressed else 2)
    table.extend(struct.pack("<III", fc, fc + (1 if compressed else 2), 5 | 0x80000000))
    word.extend(bytes(3072 - len(word)))
    struct.pack_into("<I", word, 0x40, len(word))
    struct.pack_into("<II", word, 2560, fc, fc + (1 if compressed else 2))
    word[2568] = rgb
    word[3071] = runs
    if rgb:
        start = 2560 + 2 * rgb
        word[start] = len(payload)
        word[start + 1 : start + 1 + len(payload)] = payload
    return word, table


@pytest.mark.parametrize("compressed", [False, True])
def test_cf_spec_uses_source_piece_and_returns_cps_in_input_order(compressed: bool) -> None:
    word, table = streams(ON, compressed=compressed)

    result = read_doc_cfspec(bytes(word), bytes(table), (2, 1, -1, 1))

    assert result == (
        CfSpecObservation(2, "unknown", reason="no_covering_interval"),
        CfSpecObservation(1, "on", "chpx"),
        CfSpecObservation(-1, "unknown", reason="cp_outside_main_story"),
        CfSpecObservation(1, "on", "chpx"),
    )


@pytest.mark.parametrize(
    ("payload", "prm", "prc", "expected"),
    [
        (ON + OFF, 0, None, CfSpecObservation(1, "off", "chpx")),
        (ON, 0x75 << 1, None, CfSpecObservation(1, "off", "prm0")),
        (OFF, (1 << 8) | (0x75 << 1), None, CfSpecObservation(1, "on", "prm0")),
        (ON, (1 << 8) | (0x55 << 1), None, CfSpecObservation(1, "on", "chpx")),
        (ON, 1, OFF, CfSpecObservation(1, "off", "prm1")),
        (ON, 1, None, CfSpecObservation(1, "unknown", reason="invalid_prm1_index")),
        (
            bytes.fromhex("55 08 80"),
            0,
            None,
            CfSpecObservation(1, "unknown", "chpx", "style_relative_toggle"),
        ),
        (bytes.fromhex("01 68") + ON + b"\0", 0, None, CfSpecObservation(1, "absent")),
        (ON + b"\x55", 0, None, CfSpecObservation(1, "unknown", reason="truncated_sprm")),
        (ON[:-1], 0, None, CfSpecObservation(1, "unknown", reason="truncated_sprm_operand")),
        (
            bytes.fromhex("55 08 02"),
            0,
            None,
            CfSpecObservation(1, "unknown", reason="invalid_cfspec_toggle"),
        ),
        (
            bytes.fromhex("08 d6 00") + ON,
            0,
            None,
            CfSpecObservation(1, "unknown", reason="unsupported_special_sprm_length"),
        ),
        (
            bytes.fromhex("15 c6 00") + ON,
            0,
            None,
            CfSpecObservation(1, "unknown", reason="unsupported_special_sprm_length"),
        ),
    ],
)
def test_cf_spec_assignment_precedence_and_operand_framing(
    payload: bytes, prm: int, prc: bytes | None, expected: CfSpecObservation
) -> None:
    word, table = streams(payload, prm=prm, prc=prc)

    assert read_doc_cfspec(bytes(word), bytes(table), (1,)) == (expected,)


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("fib", "unsupported_fib"),
        ("plc", "invalid_chpx_plc_size"),
        ("plc_order", "unordered_boundaries"),
        ("plc_limit", "table_range_missing_or_outside_profile"),
        ("fkp", "chpx_page_outside_word_stream"),
        ("crun", "invalid_chpx_run_count"),
        ("rgb", "invalid_chpx_offset"),
        ("clx", "invalid_piece_table_size"),
        ("piece_fc", "invalid_piece_fc_flags"),
        ("piece_order", "unordered_boundaries"),
    ],
)
def test_cf_spec_structural_damage_is_unknown(field: str, reason: str) -> None:
    word, table = streams(
        ON,
        runs=0 if field == "crun" else 1,
        rgb=4 if field == "rgb" else 10,
        malformed_clx=field == "clx",
    )
    if field == "fib":
        struct.pack_into("<H", word, 32, 13)
    if field == "plc":
        struct.pack_into("<I", word, 0xFE, 11)
    if field == "plc_order":
        struct.pack_into("<I", table, len(table) - 8, 0)
    if field == "plc_limit":
        table.extend(bytes(8 * 65_536))
        struct.pack_into("<I", word, 0xFE, 12 + 8 * 65_536)
    if field == "piece_fc":
        struct.pack_into("<I", table, len(table) - 18, 0x80000000)
    if field == "piece_order":
        struct.pack_into("<I", table, len(table) - 24, 0)
    if field == "fkp":
        struct.pack_into("<I", table, len(table) - 4, 6)

    assert read_doc_cfspec(bytes(word), bytes(table), (1,)) == (
        CfSpecObservation(1, "unknown", reason=reason),
    )


def test_empty_chpx_run_is_observed_absent() -> None:
    word, table = streams(ON, rgb=0)

    assert read_doc_cfspec(bytes(word), bytes(table), (1,)) == (CfSpecObservation(1, "absent"),)
