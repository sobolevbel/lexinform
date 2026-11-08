import base64
import hashlib
import zlib

import pytest

from lexinform.models.digest import Digest, week_bounds
from lexinform.models.digest_content import (
    MAX_UNPACKED_DIGEST_BYTES,
    DigestRenderedPartRef,
    DigestRenderedPlan,
)


@pytest.mark.parametrize("damage", ["expanded", "trailing", "checksum", "week", "encoding"])
def test_retained_digest_rejects_unbounded_or_foreign_payload(damage: str) -> None:
    week_ref = "2026-W37"
    since, until = week_bounds(week_ref)
    upstream_week = "2026-W38" if damage == "week" else week_ref
    start, end = week_bounds(upstream_week)
    raw = Digest(ref=upstream_week, since=start, until=end).model_dump_json().encode()
    if damage == "expanded":
        raw = b"x" * (MAX_UNPACKED_DIGEST_BYTES + 1)
    compressed = zlib.compress(raw)
    if damage == "trailing":
        compressed += zlib.compress(b"second stream")
    packed = base64.b64encode(compressed).decode("ascii")
    plan = DigestRenderedPlan(
        profile="digest-rendered-semantic-v1",
        channel_id="fixture",
        delivery_ref=week_ref,
        week_ref=week_ref,
        since=since,
        until=until,
        upstream_semantics="retained",
        upstream_digest_sha256="0" * 64
        if damage == "checksum"
        else hashlib.sha256(raw).hexdigest(),
        upstream_digest_zlib=packed + "!" if damage == "encoding" else packed,
        parts=(
            DigestRenderedPartRef(publication_id=1, snapshot_id="content-snapshot-v1:" + "a" * 64),
        ),
    )

    with pytest.raises(ValueError, match="digest compressed semantics"):
        plan.retained_digest()
