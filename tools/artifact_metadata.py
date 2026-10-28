"""Read recorded complete-body metadata without implying byte availability."""

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass

from lexinform.models import ArtifactMetadata, ArtifactReadReceipt


@dataclass(frozen=True)
class ArtifactMetadataIssue:
    ref: str


def recorded_artifacts(
    connection: sqlite3.Connection,
) -> Iterator[ArtifactMetadata | ArtifactMetadataIssue]:
    for row in connection.execute("SELECT rowid AS audit_id, * FROM artifacts ORDER BY rowid"):
        try:
            yield ArtifactMetadata.model_validate(
                {name: row[name] for name in ("sha256", "byte_size", "recorded_at")}
            )
        except ValueError, TypeError:
            yield ArtifactMetadataIssue(f"artifacts/{row['audit_id']}")


@dataclass(frozen=True)
class ArtifactReadIssue:
    ref: str
    code: str


def recorded_artifact_reads(
    connection: sqlite3.Connection,
) -> Iterator[ArtifactReadReceipt | ArtifactReadIssue]:
    for row in connection.execute("SELECT rowid AS audit_id, * FROM artifact_reads ORDER BY rowid"):
        ref = f"artifact_reads/{row['audit_id']}"
        try:
            receipt = ArtifactReadReceipt.model_validate(
                {
                    "sha256": row["artifact_sha256"],
                    "byte_size": row["byte_size"],
                    "requested_url": row["requested_url"],
                    "recorded_at": row["recorded_at"],
                }
            )
        except ValueError, TypeError, KeyError, IndexError:
            yield ArtifactReadIssue(ref, "invalid_artifact_read")
            continue
        if receipt.read_id != row["read_id"]:
            yield ArtifactReadIssue(ref, "artifact_read_identity_mismatch")
        else:
            yield receipt
