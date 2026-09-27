import hashlib
import math
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from lexinform.adapters.sqlite_repo import SCHEMA_VERSION
from lexinform_web.ingestion.contract import ImportDocumentV1
from lexinform_web.ingestion.restore import (
    MAX_DATABASE_PAGES,
    MAX_DUMP_BYTES,
    RestoredSnapshot,
    SnapshotError,
)

MAX_DATABASE_BYTES = MAX_DATABASE_PAGES * 4096
MAX_RECEIPT_BYTES = 1024


class RestoreReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    source_schema: int = Field(ge=1, le=SCHEMA_VERSION)
    normalized_schema: int = Field(ge=SCHEMA_VERSION, le=SCHEMA_VERSION)
    dump_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    database_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    database_bytes: int = Field(gt=0, le=MAX_DATABASE_BYTES)


def verify_result(directory: Path, expected_hash: str) -> RestoreReceipt:
    receipt_path = directory / "receipt.json"
    database_path = directory / "normalized.sqlite"
    try:
        with receipt_path.open("rb") as receipt_file:
            raw_receipt = receipt_file.read(MAX_RECEIPT_BYTES + 1)
        if len(raw_receipt) > MAX_RECEIPT_BYTES:
            raise SnapshotError("restore receipt exceeds its size limit")
        receipt = RestoreReceipt.model_validate_json(raw_receipt)
        if receipt.dump_sha256 != expected_hash:
            raise SnapshotError("restore result belongs to a different snapshot")
        if database_path.stat().st_size != receipt.database_bytes:
            raise SnapshotError("restore result size mismatch")
        with database_path.open("rb") as database_file:
            digest = hashlib.file_digest(database_file, "sha256").hexdigest()
        if digest != receipt.database_sha256:
            raise SnapshotError("restore result hash mismatch")
    except (OSError, ValidationError) as error:
        raise SnapshotError("restore worker produced an invalid result") from error
    return receipt


@contextmanager
def worker_result(raw: bytes, timeout: float, arguments: tuple[str, ...] = ()) -> Iterator[Path]:
    if not raw or len(raw) > MAX_DUMP_BYTES:
        raise SnapshotError("snapshot size outside allowed bounds")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("restore timeout must be finite and positive")
    with TemporaryDirectory(prefix="lexinform-restore-") as temporary:
        directory = Path(temporary)
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-m",
                    "lexinform_web.ingestion.restore_worker",
                    *arguments,
                ],
                input=raw,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=directory,
                env={},
                close_fds=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise SnapshotError("restore worker exceeded its deadline") from None
        except OSError:
            raise SnapshotError("restore worker could not start") from None
        if result.returncode != 0:
            raise SnapshotError("restore worker failed validation or exceeded its resource limits")
        yield directory


@contextmanager
def restore_in_process(raw: bytes, *, timeout: float = 30) -> Iterator[RestoredSnapshot]:
    """Bound restoration in a child process; deployment must also isolate filesystem and network."""
    with worker_result(raw, timeout) as directory:
        receipt = verify_result(directory, hashlib.sha256(raw).hexdigest())
        uri = (directory / "normalized.sqlite").as_uri() + "?mode=ro&immutable=1"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            connection.execute("PRAGMA query_only = ON")
            yield RestoredSnapshot(
                connection=connection,
                source_schema=receipt.source_schema,
                normalized_schema=receipt.normalized_schema,
                dump_sha256=receipt.dump_sha256,
            )


def project_in_process(
    raw: bytes, *, source_commit: str, public_channel: str | None = None, timeout: float = 30
) -> ImportDocumentV1:
    with worker_result(raw, timeout, (source_commit, public_channel or "")) as directory:
        try:
            with (directory / "document.json").open("rb") as output:
                payload = output.read(MAX_DUMP_BYTES + 1)
            if len(payload) > MAX_DUMP_BYTES:
                raise SnapshotError("import document exceeds its size limit")
            document = ImportDocumentV1.model_validate_json(payload)
        except OSError, ValidationError:
            raise SnapshotError("projection worker produced an invalid document") from None
        if (
            document.origin.source_commit != source_commit
            or document.origin.dump_sha256 != hashlib.sha256(raw).hexdigest()
        ):
            raise SnapshotError("import document belongs to a different snapshot")
        return document
