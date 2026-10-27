"""Read recorded complete-body metadata without implying byte availability."""

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass

from lexinform.models import ArtifactMetadata


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
