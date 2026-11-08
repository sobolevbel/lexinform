"""Verify physical source parts and replay trimming against a retained request."""

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
from lexinform.sections import PAGE_BREAK, trim_print
from lexinform.text_projection import TextProjection
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


@dataclass(frozen=True)
class PinnedSource:
    path: Path
    sha256: str


@dataclass(frozen=True)
class QuotePin:
    start: int
    end: int
    sha256: str


def _identity(part: DocumentPart) -> tuple[object, ...]:
    return part.role, part.members, part.sha256, part.text_sha256, part.text_start, part.text_end


def _read_source(archive: Path, archive_sha256: str) -> bytes:
    if archive.name == ".env" or archive.resolve().name == ".env":
        raise ValueError("A credential file is not a replay archive")
    if archive.stat().st_size > _MAX_ARCHIVE_BYTES:
        raise ValueError("Archive exceeds the replay byte limit")
    with archive.open("rb") as stream:
        raw = stream.read(_MAX_ARCHIVE_BYTES + 1)
    if len(raw) > _MAX_ARCHIVE_BYTES or digest(raw) != archive_sha256:
        raise ValueError("Archive differs from its bounded SHA-256 pin")
    return raw


def _extract_source_text(
    selected: tuple[DocumentPart, ...], bodies: list[bytes], *, archive: bool
) -> tuple[str, tuple[str, ...]]:
    reader = DocumentTextExtractor(PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor())
    if archive:
        extracted = reader.extract_document(bodies[0])
        if tuple(map(_identity, extracted.selection.parts)) != tuple(map(_identity, selected)):
            raise ValueError("Archive replay differs from the recorded source parts")
        return extracted.text, tuple(part.text for part in extracted.selection.parts)
    texts = []
    for expected, raw in zip(selected, bodies, strict=True):
        extracted = reader.extract_document(raw)
        if tuple(map(_identity, extracted.selection.parts)) != (_identity(expected),):
            raise ValueError("File replay differs from its recorded source part")
        texts.append(extracted.text)
    return PAGE_BREAK.join(texts), tuple(texts)


def _quote_source(
    pin: QuotePin,
    projection: TextProjection,
    assembled: str,
    parts: tuple[DocumentPart, ...],
    part_texts: tuple[str, ...],
) -> dict[str, object]:
    quote = projection.text[pin.start : pin.end]
    if (
        not 0 <= pin.start < pin.end <= len(projection.text)
        or len(quote) > 1200
        or digest(quote.encode()) != pin.sha256
    ):
        raise ValueError("Submitted quote differs from its bounded span or SHA-256 pin")
    if assembled != PAGE_BREAK.join(part_texts):
        raise ValueError("Assembled text has no exact ordered part map")
    for segment in projection.segments:
        if segment.source_start is not None and segment.start <= pin.start < pin.end <= segment.end:
            start = segment.source_start + pin.start - segment.start
            end = start + len(quote)
            break
    else:
        raise ValueError("Quote crosses a generated or non-contiguous text boundary")
    if assembled[start:end] != quote:
        raise ValueError("Projected quote differs from its source text")
    offset = 0
    for index, (part, text) in enumerate(zip(parts, part_texts, strict=True)):
        if offset <= start < end <= offset + len(text):
            local = start - offset
            if text[local : local + len(quote)] != quote:
                raise ValueError("Quoted part differs from its assembled text")
            return {
                "quote_sha256": pin.sha256,
                "submitted_span": [pin.start, pin.end],
                "assembled_span": [start, end],
                "part_index": index,
                "part_role": part.role,
                "part_sha256": part.sha256,
                "part_members": part.members,
                "part_span": [local, local + len(quote)],
                "part_coordinates": "selected_extracted_text",
            }
        offset += len(text) + len(PAGE_BREAK)
    raise ValueError("Quote crosses an extracted part boundary")


def _replay_source(
    database: Path,
    checkpoint: ReviewInputCheckpoint,
    observation_id: str,
    sources: tuple[PinnedSource, ...],
    text: SubmittedText,
    *,
    archive: bool,
    quote: QuotePin | None = None,
) -> dict[str, object]:
    if not sources or len(sources) > 6 or (archive and len(sources) != 1):
        raise ValueError("Replay requires one archive or one to six individual parts")
    bodies = [_read_source(source.path, source.sha256) for source in sources]
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
            or (
                any(not 1 <= len(part.members) <= 2 for part in selected)
                if archive
                else len(selected) != len(sources) or any(part.members for part in selected)
            )
            or tuple(map(_identity, selected)) != tuple(map(_identity, meta.selection.parts))
        ):
            raise ValueError("Recorded selection differs from the historical request parts")
        references = db.execute(
            "SELECT part_index,artifact_sha256 FROM selection_artifacts"
            " WHERE selection_id=? AND part_scope='parts' ORDER BY part_index",
            (selection.selection_id,),
        )
        expected_hashes = (
            [sources[0].sha256] * len(selected)
            if archive
            else [source.sha256 for source in sources]
        )
        if list(map(tuple, references)) != list(enumerate(expected_hashes)):
            raise ValueError(
                "Selected parts do not share the pinned archive"
                if archive
                else "Selected parts differ from the ordered file pins"
            )
        extracted, part_texts = _extract_source_text(selected, bodies, archive=archive)
        trimmed = trim_print(extracted)
        if len(trimmed.text) != text.chars or digest(trimmed.text.encode()) != text.text_sha256:
            raise ValueError("Trimmed archive does not reproduce the submitted text")
        projection = trimmed.projection
        if projection is None:
            raise ValueError("Archive replay has no exact trimming coordinates")
        if (
            not snapshot.unchanged()
            or not checkpoint.unchanged()
            or any(
                _read_source(source.path, source.sha256) != raw
                for source, raw in zip(sources, bodies, strict=True)
            )
        ):
            raise ValueError("A pinned source changed during archive replay")
    literal_quote = (
        _quote_source(quote, projection, extracted, selected, part_texts)
        if quote is not None
        else None
    )
    return {
        "observation_id": observation_id,
        "selection_id": selection.selection_id,
        **(
            {"archive_sha256": sources[0].sha256, "archive_bytes": len(bodies[0])}
            if archive
            else {"file_sha256s": expected_hashes, "file_bytes": [len(raw) for raw in bodies]}
        ),
        "selected_parts": [
            {
                "role": part.role,
                "members": part.members,
                "sha256": part.sha256,
                "text_sha256": part.text_sha256,
            }
            for part in selected
        ],
        "extracted_text_sha256": digest(extracted.encode()),
        "dropped_sections": [asdict(section) for section in trimmed.dropped],
        "trimmed_text_segments": [asdict(segment) for segment in projection.segments],
        **({"literal_quote": literal_quote} if literal_quote is not None else {}),
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
            database,
            checkpoint,
            observation_id,
            (PinnedSource(archive, archive_sha256),),
            submitted,
            archive=True,
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
    return replay_result_sources(
        database,
        database_sha256,
        result_id,
        checkpoint,
        observation_id,
        (PinnedSource(archive, archive_sha256),),
        archive=True,
    )


def replay_result_sources(
    database: Path,
    database_sha256: str,
    result_id: str,
    checkpoint: ReviewInputCheckpoint,
    observation_id: str,
    sources: tuple[PinnedSource, ...],
    *,
    archive: bool = False,
    quote: QuotePin | None = None,
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
            database, checkpoint, observation_id, sources, submitted, archive=archive, quote=quote
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
    physical = parser.add_mutually_exclusive_group(required=True)
    physical.add_argument("--archive", type=Path)
    physical.add_argument("--part", nargs=2, action="append", metavar=("PATH", "SHA256"))
    parser.add_argument("--archive-sha256")
    parser.add_argument("--quote", nargs=3, metavar=("START", "END", "SHA256"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    checkpoint = ReviewInputCheckpoint(Path(args.checkpoint[0]), args.checkpoint[1])
    protected = snapshot_files(args.database) | snapshot_files(checkpoint.database)
    try:
        quote = (
            QuotePin(int(args.quote[0]), int(args.quote[1]), args.quote[2]) if args.quote else None
        )
        if quote is not None and args.result_id is None:
            raise ValueError("Quote replay requires a result ID")
        sources: tuple[PinnedSource, ...]
        if args.archive is not None:
            if args.archive_sha256 is None:
                raise ValueError("Archive replay requires its SHA-256 pin")
            sources = (PinnedSource(args.archive, args.archive_sha256),)
        else:
            if args.archive_sha256 is not None or args.result_id is None:
                raise ValueError("Individual parts require result replay and one pin per part")
            sources = tuple(PinnedSource(Path(path), sha) for path, sha in args.part)
        if args.output.resolve() in protected | {source.path.resolve() for source in sources}:
            raise ValueError("Output must be separate from all replay inputs")
        if args.result_id is not None:
            if args.database_sha256 is None:
                raise ValueError("Result replay requires a database SHA-256 pin")
            report = replay_result_sources(
                args.database,
                args.database_sha256,
                args.result_id,
                checkpoint,
                args.observation_id,
                sources,
                archive=args.archive is not None,
                quote=quote,
            )
        else:
            if args.database_sha256 is not None:
                raise ValueError("Database SHA-256 is only used for result replay")
            report = replay(
                args.database,
                args.publication_id,
                checkpoint,
                args.observation_id,
                sources[0].path,
                sources[0].sha256,
            )
        write_json(args.output, report)
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
