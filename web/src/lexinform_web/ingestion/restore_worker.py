import hashlib
import resource
import sys
from pathlib import Path

from lexinform_web.ingestion.process import MAX_DATABASE_BYTES, RestoreReceipt
from lexinform_web.ingestion.restore import MAX_DUMP_BYTES, restore_snapshot


def limit_resources() -> None:
    limits = [
        (resource.RLIMIT_CPU, 10),
        (resource.RLIMIT_FSIZE, MAX_DATABASE_BYTES),
        (resource.RLIMIT_NOFILE, 32),
        (resource.RLIMIT_CORE, 0),
    ]
    if sys.platform == "linux":
        limits.append((resource.RLIMIT_AS, 512 * 1024 * 1024))
    for kind, maximum in limits:
        _, hard = resource.getrlimit(kind)
        bound = maximum if hard == resource.RLIM_INFINITY else min(maximum, hard)
        resource.setrlimit(kind, (bound, bound))


def main() -> None:
    limit_resources()
    raw = sys.stdin.buffer.read(MAX_DUMP_BYTES + 1)
    with restore_snapshot(raw) as snapshot:
        database = snapshot.connection.serialize()
        receipt = RestoreReceipt(
            source_schema=snapshot.source_schema,
            normalized_schema=snapshot.normalized_schema,
            dump_sha256=snapshot.dump_sha256,
            database_sha256=hashlib.sha256(database).hexdigest(),
            database_bytes=len(database),
        )
        Path("normalized.sqlite").write_bytes(database)
        Path("receipt.json").write_text(receipt.model_dump_json(), encoding="utf-8")


if __name__ == "__main__":
    main()
