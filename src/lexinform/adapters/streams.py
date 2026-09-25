from collections.abc import Iterable

from lexinform.errors import AttachmentTooLargeError


def read_bounded(chunks: Iterable[bytes], url: str, max_bytes: int | None) -> bytes:
    parts: list[bytes] = []
    received = 0
    for chunk in chunks:
        received += len(chunk)
        if max_bytes is not None and received > max_bytes:
            raise AttachmentTooLargeError(url, max_bytes)
        parts.append(chunk)
    return b"".join(parts)
