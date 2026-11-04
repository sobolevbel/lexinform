import io
import struct
import zipfile
from collections import Counter
from pathlib import Path

import pytest
from tools import replay_archive_dispositions as frozen
from tools.replay_archive_dispositions import zipped
from tools.replay_archive_priority import read_ordinal_path, run


def deflate64_member(data: bytes) -> bytes:
    raw = bytearray(zipped((("member", data),)))
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        info = archive.infolist()[0]
    start = info.header_offset + 30 + len("member")
    compressed = b"\x01" + struct.pack("<HH", len(data), 65535 - len(data)) + data
    central = raw.index(b"PK\x01\x02")
    raw[start:central] = compressed
    central = start + len(compressed)
    struct.pack_into("<H", raw, 8, 9)
    struct.pack_into("<I", raw, 18, len(compressed))
    struct.pack_into("<H", raw, central + 10, 9)
    struct.pack_into("<I", raw, central + 20, len(compressed))
    end = raw.index(b"PK\x05\x06", central)
    struct.pack_into("<I", raw, end + 16, central)
    return bytes(raw)


def test_deflate64_ordinal_payload_is_decoded_independently_with_exact_crc_and_length() -> None:
    body = b"actual independently encoded stored Deflate64 block"
    methods: Counter[int] = Counter()
    assert (
        read_ordinal_path(deflate64_member(body), (0,), max_member_bytes=1000, methods=methods)
        == body
    )
    assert methods == {9: 1}


@pytest.mark.parametrize("fault", ["crc", "size", "span", "method", "truncated", "encrypted"])
def test_deflate64_tamper_and_truncation_cannot_be_accepted_as_complete(fault: str) -> None:
    raw = bytearray(deflate64_member(b"payload"))
    central = raw.index(b"PK\x01\x02")
    if fault == "crc":
        struct.pack_into("<I", raw, central + 16, 0)
    elif fault == "size":
        struct.pack_into("<I", raw, central + 24, 100)
    elif fault == "span":
        struct.pack_into("<I", raw, central + 20, 100000)
    elif fault == "method":
        struct.pack_into("<H", raw, 8, 0)
    elif fault == "truncated":
        raw = raw[:-30]
    else:
        struct.pack_into("<H", raw, 6, 1)
        struct.pack_into("<H", raw, central + 8, 1)
    with pytest.raises((ValueError, zipfile.BadZipFile)):
        read_ordinal_path(bytes(raw), (0,), max_member_bytes=1000)


def test_duplicate_names_and_nested_ordinals_keep_occurrence_identity() -> None:
    data = zipped((("same", b"first"), ("same", deflate64_member(b"second"))))
    assert read_ordinal_path(data, (0,), max_member_bytes=1000) == b"first"
    assert read_ordinal_path(data, (1, 0), max_member_bytes=1000) == b"second"
    with pytest.raises(ValueError):
        read_ordinal_path(data, (1,), max_member_bytes=1)
    with pytest.raises(ValueError):
        read_ordinal_path(data, (2,), max_member_bytes=1000)


def test_frozen_boundary_is_restored_when_qualification_is_cancelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = frozen.archive_leaf
    error = KeyboardInterrupt("cancelled qualification")

    def fail(*args: object, **kwargs: object) -> dict[str, object]:
        raise error

    monkeypatch.setattr(frozen, "run", fail)
    with pytest.raises(KeyboardInterrupt) as caught:
        run(
            tmp_path,
            tmp_path / "cohort",
            tmp_path.parent / "new.json",
            baseline=tmp_path / "baseline",
        )
    assert caught.value is error and frozen.archive_leaf is original


def test_changed_original_boundary_is_rejected_before_qualification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def different(data: bytes, ordinals: tuple[int, ...]) -> bytes:
        return data

    monkeypatch.setattr(frozen, "archive_leaf", different)
    with pytest.raises(ValueError, match="boundary changed"):
        run(
            tmp_path,
            tmp_path / "cohort",
            tmp_path.parent / "new.json",
            baseline=tmp_path / "baseline",
        )


def test_empty_parent_prefix_preserves_the_actual_root_for_frozen_walk_verification() -> None:
    raw = deflate64_member(b"payload")
    assert read_ordinal_path(raw, (), max_member_bytes=1000) is raw
