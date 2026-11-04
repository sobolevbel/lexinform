"""Extend only frozen replay ordinal verification with independently checked Deflate64 payloads."""

import argparse
import inspect
import io
import json
import struct
import zipfile
import zlib
from collections import Counter
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from inflate64 import Inflater

from tools import replay_archive_dispositions as frozen
from tools.corpus_artifacts import digest, write_json

VERSION = "archive-ordinal-deflate64-v1"
DRIVER_SHA256 = "4eea367f98858d8add00a1ea043f5eed4707b0ed83c42b5f02ae9f4f64dc6319"
ORIGINAL_LEAF_SHA256 = "461dd233a8037b335cb3d1c1f8730d1e9669fcf53cec6332c3547e1b287b1875"


def _deflate64(data: bytes, info: zipfile.ZipInfo, limit: int) -> bytes:
    offset = info.header_offset
    if offset < 0 or offset + 30 > len(data):
        raise zipfile.BadZipFile("Deflate64 local header is outside the actual archive")
    signature, _, flags, method, _, _, _, _, _, name_size, extra_size = struct.unpack_from(
        "<4s5H3I2H", data, offset
    )
    if signature != b"PK\x03\x04" or method != 9 or flags != info.flag_bits:
        raise zipfile.BadZipFile("Deflate64 local and central records disagree")
    start = offset + 30 + name_size + extra_size
    end = start + info.compress_size
    if start > len(data) or end > len(data) or info.compress_size < 0:
        raise zipfile.BadZipFile("Deflate64 compressed span is outside the actual archive")
    name = data[offset + 30 : offset + 30 + name_size]
    if b"\x00" in name or name.decode("utf-8" if flags & 0x800 else "cp437") != info.filename:
        raise zipfile.BadZipFile("Deflate64 local filename differs from its actual central entry")
    inflater = Inflater()
    result = inflater.inflate(data[start:end], min(limit, info.file_size) + 1)
    if not inflater.eof:
        raise zipfile.BadZipFile("Deflate64 stream is incomplete or exceeds the declared bound")
    return result


def read_ordinal_path(
    data: bytes,
    ordinals: tuple[int, ...],
    *,
    max_member_bytes: int,
    methods: Counter[int] | None = None,
) -> bytes:
    """Resolve actual central-directory ordinals and check each complete returned payload."""
    if len(ordinals) > 4 or max_member_bytes < 0:
        raise ValueError("Ordinal verification requires a bounded path and byte limit")
    for ordinal in ordinals:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if type(ordinal) is not int or ordinal < 0 or ordinal >= len(entries):
                raise ValueError("Ordinal is outside its actual central-directory universe")
            info = entries[ordinal]
            if info.flag_bits & 1 or info.file_size > max_member_bytes:
                raise ValueError("Encrypted or oversized member cannot be independently verified")
            if info.compress_type == 9:
                result = _deflate64(data, info, max_member_bytes)
            else:
                with archive.open(info) as stream:
                    result = stream.read(max_member_bytes + 1)
            if len(result) != info.file_size or len(result) > max_member_bytes:
                raise zipfile.BadZipFile("Independent ordinal payload length differs")
            if zlib.crc32(result) != info.CRC:
                raise zipfile.BadZipFile("Independent ordinal payload CRC differs")
            if methods is not None:
                methods[info.compress_type] += 1
            data = result
    return data


def run(corpus: Path, cohort: Path, output: Path, *, baseline: Path) -> dict[str, Any]:
    output = output.resolve()
    if output.exists() or output.is_relative_to(corpus.resolve()):
        raise ValueError("Output must be new and outside the corpus")
    actual_driver = digest(Path(frozen.__file__).read_bytes())
    actual_leaf = digest(inspect.getsource(frozen.archive_leaf).encode())
    if actual_driver != DRIVER_SHA256 or actual_leaf != ORIGINAL_LEAF_SHA256:
        raise ValueError("Frozen replay or original ordinal-verification boundary changed")
    methods: Counter[int] = Counter()

    def ordinal_leaf(data: bytes, ordinals: tuple[int, ...]) -> bytes:
        return read_ordinal_path(
            data, ordinals, max_member_bytes=frozen.OPTIONS["max_member_bytes"], methods=methods
        )

    with (
        TemporaryDirectory(prefix="lexinform-ordinal-replay-") as scratch,
        patch.object(frozen, "archive_leaf", ordinal_leaf),
    ):
        result = frozen.run(corpus, cohort, Path(scratch) / "report.json", baseline=baseline)
    result["verification_extension"] = {
        "version": VERSION,
        "frozen_driver_sha256": actual_driver,
        "original_archive_leaf_sha256": actual_leaf,
        "extension_source_sha256": digest(Path(__file__).read_bytes()),
        "inflate64_version": version("inflate64"),
        "successful_member_decodes_by_method": dict(sorted(methods.items())),
        "scope": "Independent raw ordinal payload checks only; no production selection helpers.",
    }
    write_json(output, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.corpus, args.cohort, args.output, baseline=args.baseline)
    print(
        json.dumps(
            {
                "comparison": result["comparison"],
                "verification_extension": result["verification_extension"],
            }
        )
    )


if __name__ == "__main__":
    main()
