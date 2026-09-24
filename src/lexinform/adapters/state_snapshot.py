"""The SQL state and its derived polling manifest share a Git commit."""

from collections.abc import Sequence
from pathlib import Path

from lexinform.models import LlmBatch, PendingBatch, PendingBatches

MANIFEST_NAME = "pending-batches.json"


def write_state_snapshot(path: Path, dump: str, batches: Sequence[LlmBatch]) -> None:
    manifest = PendingBatches(
        version=1, batches=[PendingBatch(batch_id=b.batch_id, provider=b.provider) for b in batches]
    )
    payloads = {path: dump, path.with_name(MANIFEST_NAME): manifest.model_dump_json() + "\n"}
    path.parent.mkdir(parents=True, exist_ok=True)
    for target, payload in payloads.items():
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(payload, encoding="utf-8")
    for target in payloads:
        target.with_suffix(target.suffix + ".tmp").replace(target)
