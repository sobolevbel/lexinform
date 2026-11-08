"""Verify archived source parts and replay trimming against a retained card request."""

import argparse
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from lexinform.adapters.computation_history import read_computation_result
from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_source_rows import DocumentSourceRows
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.models import DocumentPart
from lexinform.sections import trim_print
from tools.audit_saved_primary_claims import (
    ReviewInputCheckpoint,
    _primary_batch_meta,
    saved_claim_review,
)
from tools.corpus_artifacts import digest, write_json
from tools.primary_delivery_cycle import audit
from tools.sqlite_snapshot import open_snapshot, snapshot_files

_MAX_ARCHIVE_BYTES = 32_000_000


@dataclass(frozen=True)
class SubmittedText:
    database_sha256: str
    result_id: str
    text_sha256: str
    chars: int


def _identity(part: DocumentPart) -> tuple[object, ...]:
    return part.role, part.members, part.sha256, part.text_sha256, part.text_start, part.text_end


def _read_archive(archive: Path, archive_sha256: str) -> bytes:
    if archive.name == ".env" or archive.resolve().name == ".env":
        raise ValueError("A credential file is not a replay archive")
    if archive.stat().st_size > _MAX_ARCHIVE_BYTES:
        raise ValueError("Archive exceeds the replay byte limit")
    with archive.open("rb") as stream:
        raw = stream.read(_MAX_ARCHIVE_BYTES + 1)
    if len(raw) > _MAX_ARCHIVE_BYTES or digest(raw) != archive_sha256:
        raise ValueError("Archive differs from its bounded SHA-256 pin")
    return raw


def _replay_source(
    database: Path,
    checkpoint: ReviewInputCheckpoint,
    observation_id: str,
    archive: Path,
    archive_sha256: str,
    text: SubmittedText,
) -> dict[str, object]:
    raw = _read_archive(archive, archive_sha256)
    with open_snapshot(database) as snapshot:
        db = snapshot.connection
        if snapshot.sha256 != text.database_sha256:
            raise ValueError("Database changed after the historical card audit")
        row = db.execute(
            "SELECT * FROM computation_results WHERE result_id=?", (text.result_id,)
        ).fetchone()
        paid = read_computation_result(row)
        meta = _primary_batch_meta(db, paid)
        reader = DocumentSourceRows(db)
        observation = reader.get_source_observation(observation_id)
        submitted = db.execute(
            "SELECT submitted_at FROM llm_batches WHERE batch_id=? AND provider=?",
            (paid.batch_id, paid.provider),
        ).fetchone()
        submitted_at = (
            datetime.fromisoformat(submitted["submitted_at"])
            if submitted is not None and isinstance(submitted["submitted_at"], str)
            else None
        )
        if (
            observation is None
            or submitted_at is None
            or submitted_at.utcoffset() is None
            or observation.recorded_at > submitted_at
            or observation.purpose != "analysis"
            or (observation.term, observation.number) != (paid.term, paid.number)
            or observation.document.kind != "rcl"
            or (observation.document.kind, observation.document.url)
            != (meta.source_kind, meta.source_url)
        ):
            raise ValueError("Observation differs from the historical request owner or source")
        selection = reader.get_document_selection(observation.selection_id)
        if selection is None:
            raise ValueError("Historical source selection is unavailable")
        selected = selection.selection.parts
        if (
            not selected
            or any(not 1 <= len(part.members) <= 2 for part in selected)
            or tuple(map(_identity, selected)) != tuple(map(_identity, meta.selection.parts))
        ):
            raise ValueError("Recorded selection differs from the historical request parts")
        references = db.execute(
            "SELECT part_index,artifact_sha256 FROM selection_artifacts"
            " WHERE selection_id=? AND part_scope='parts' ORDER BY part_index",
            (selection.selection_id,),
        )
        if list(map(tuple, references)) != list(enumerate([archive_sha256] * len(selected))):
            raise ValueError("Selected parts do not share the pinned archive")
        extracted = DocumentTextExtractor(
            PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor()
        ).extract_document(raw)
        if tuple(map(_identity, extracted.selection.parts)) != tuple(map(_identity, selected)):
            raise ValueError("Archive replay differs from the recorded source parts")
        trimmed = trim_print(extracted.text)
        if len(trimmed.text) != text.chars or digest(trimmed.text.encode()) != text.text_sha256:
            raise ValueError("Trimmed archive does not reproduce the submitted text")
        projection = trimmed.projection
        if projection is None:
            raise ValueError("Archive replay has no exact trimming coordinates")
        if (
            not snapshot.unchanged()
            or not checkpoint.unchanged()
            or _read_archive(archive, archive_sha256) != raw
        ):
            raise ValueError("A pinned source changed during archive replay")
    return {
        "observation_id": observation_id,
        "selection_id": selection.selection_id,
        "archive_sha256": archive_sha256,
        "archive_bytes": len(raw),
        "selected_parts": [
            {
                "role": part.role,
                "members": part.members,
                "sha256": part.sha256,
                "text_sha256": part.text_sha256,
            }
            for part in selected
        ],
        "extracted_text_sha256": digest(extracted.text.encode()),
        "dropped_sections": [asdict(section) for section in trimmed.dropped],
        "trimmed_text_segments": [asdict(segment) for segment in projection.segments],
    }


def replay(
    database: Path,
    publication_id: int,
    checkpoint: ReviewInputCheckpoint,
    observation_id: str,
    archive: Path,
    archive_sha256: str,
) -> dict[str, object]:
    delivery = audit(database, publication_id, legacy_text_checkpoint=checkpoint)
    text = delivery.get("legacy_submitted_text")
    if not isinstance(text, dict) or text["status"] != "recorded":
        raise ValueError("Replay requires a verified historical card request")
    database_sha256 = delivery["database_sha256"]
    assert isinstance(database_sha256, str), "the delivery audit pins the current database"
    submitted = SubmittedText(
        database_sha256, text["result_id"], text["text_sha256"], text["text_chars"]
    )
    return {
        "publication_id": publication_id,
        "database_sha256": database_sha256,
        "request": text,
        "delivery_cycle": delivery["status"],
        "physical_source_replay": _replay_source(
            database, checkpoint, observation_id, archive, archive_sha256, submitted
        ),
    }


def replay_result(
    database: Path,
    database_sha256: str,
    result_id: str,
    checkpoint: ReviewInputCheckpoint,
    observation_id: str,
    archive: Path,
    archive_sha256: str,
) -> dict[str, object]:
    saved = saved_claim_review(
        database, database_sha256=database_sha256, result_id=result_id, input_checkpoint=checkpoint
    )
    submitted = SubmittedText(database_sha256, result_id, saved.text_sha256, len(saved.text))
    return {
        "database_sha256": database_sha256,
        "request": {
            "result_id": result_id,
            "request_sha256": saved.request_sha256,
            "text_sha256": saved.text_sha256,
            "text_chars": len(saved.text),
            "input_checkpoint_sha256": checkpoint.sha256,
        },
        "physical_source_replay": _replay_source(
            database, checkpoint, observation_id, archive, archive_sha256, submitted
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--publication-id", type=int)
    target.add_argument("--result-id")
    parser.add_argument("--database-sha256")
    parser.add_argument("--checkpoint", nargs=2, metavar=("PATH", "SHA256"), required=True)
    parser.add_argument("--observation-id", required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    checkpoint = ReviewInputCheckpoint(Path(args.checkpoint[0]), args.checkpoint[1])
    protected = snapshot_files(args.database) | snapshot_files(checkpoint.database)
    try:
        if args.output.resolve() in protected | {args.archive.resolve()}:
            raise ValueError("Output must be separate from all replay inputs")
        common = (checkpoint, args.observation_id, args.archive, args.archive_sha256)
        if args.result_id is not None:
            if args.database_sha256 is None:
                raise ValueError("Result replay requires a database SHA-256 pin")
            report = replay_result(args.database, args.database_sha256, args.result_id, *common)
        else:
            if args.database_sha256 is not None:
                raise ValueError("Database SHA-256 is only used for result replay")
            report = replay(args.database, args.publication_id, *common)
        write_json(args.output, report)
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
