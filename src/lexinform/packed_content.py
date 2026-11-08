"""Bounded storage for exact editorial JSON."""

import base64
import binascii
import hashlib
import zlib

MAX_PACKED_CONTENT_BYTES = 16_384
MAX_UNPACKED_CONTENT_BYTES = 524_288
MAX_PACKED_BASE64_CHARS = ((MAX_PACKED_CONTENT_BYTES + 2) // 3) * 4


def unpack_content(packed: str, sha256: str) -> bytes:
    if len(packed) > MAX_PACKED_BASE64_CHARS:
        raise ValueError("Packed content exceeds its encoded limit")
    try:
        compressed = base64.b64decode(packed, validate=True)
        if len(compressed) > MAX_PACKED_CONTENT_BYTES or (
            base64.b64encode(compressed).decode("ascii") != packed
        ):
            raise ValueError("Packed content differs from its canonical encoding")
        stream = zlib.decompressobj()
        raw = stream.decompress(compressed, MAX_UNPACKED_CONTENT_BYTES + 1)
    except (binascii.Error, zlib.error) as exc:
        raise ValueError("Invalid packed content") from exc
    if (
        len(raw) > MAX_UNPACKED_CONTENT_BYTES
        or not stream.eof
        or stream.unconsumed_tail
        or stream.unused_data
        or hashlib.sha256(raw).hexdigest() != sha256
    ):
        raise ValueError("Packed content differs from its recorded input")
    return raw
