"""Inventory saved primary claim slots without exporting text or assessing support."""

import argparse
import sqlite3
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import TypedDict

from lexinform.adapters.computation_history import ComputationRowError, read_computation_result
from lexinform.adapters.input_receipts import (
    capture_frozen_request_manifest,
    intent_metadata,
    parse_batch_request,
    validate_input_snapshots,
)
from lexinform.adapters.llm_prompts import build_user_prompt
from lexinform.json_storage import read_json
from lexinform.models import AnalysisRecord, BatchItemMeta, ComputationResult
from lexinform.models.batch import AnalysisQuestion, batch_item_meta
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.sqlite_snapshot import live_journal, open_snapshot, snapshot_files


def _claim_slots(record: AnalysisRecord) -> Iterator[tuple[str, int | None, str]]:
    answer = record.analysis
    for name, values in (
        ("key_changes", answer.key_changes),
        ("affected_groups", answer.affected_groups),
    ):
        for index, value in enumerate(values):
            yield name, index, value
    if answer.effective_date is not None:
        yield "effective_date", None, answer.effective_date
    for index, value in enumerate(answer.changes_since_previous):
        yield "changes_since_previous", index, value


class ClaimAnswer(TypedDict):
    result_id: str
    purpose: str
    claims: list[dict[str, object]]


def audit(
    database: Path,
    *,
    review_checkpoints: tuple[ReviewInputCheckpoint, ...] | None = None,
) -> dict[str, object]:
    rows: Counter[str] = Counter(validated_primary=0, failed_primary=0, other_purposes=0, invalid=0)
    claims: Counter[str] = Counter(
        key_changes=0, affected_groups=0, effective_date=0, changes_since_previous=0
    )
    invalid: Counter[str] = Counter()
    answers: list[ClaimAnswer] = []
    blank_slots = 0
    empty_answers = 0
    with open_snapshot(database) as snapshot:
        db = snapshot.connection
        schema = db.execute("PRAGMA user_version").fetchone()[0]
        available = (
            db.execute(
                "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='computation_results'"
            ).fetchone()
            is not None
        )
        if available:
            for row in db.execute("SELECT * FROM computation_results ORDER BY result_id"):
                try:
                    result = read_computation_result(row)
                except ComputationRowError as exc:
                    rows["invalid"] += 1
                    invalid[exc.code] += 1
                    continue
                if result.purpose not in ("analysis", "reanalysis"):
                    rows["other_purposes"] += 1
                    continue
                if result.validation == "error":
                    rows["failed_primary"] += 1
                    continue
                record = result.record
                assert isinstance(record, AnalysisRecord), (
                    "the computation reader enforces the primary record type"
                )
                rows["validated_primary"] += 1
                slots: list[dict[str, object]] = []
                for field, index, value in _claim_slots(record):
                    if not value.strip():
                        blank_slots += 1
                        continue
                    claims[field] += 1
                    slots.append(
                        {
                            "field": field,
                            "index": index,
                            "claim_text_sha256": digest(value.encode()),
                        }
                    )
                empty_answers += not slots
                answers.append(
                    {"result_id": result.result_id, "purpose": result.purpose, "claims": slots}
                )
        if not snapshot.unchanged():
            raise ValueError("Input database changed during the audit")
    return {
        "version": 1,
        "status": "not_run" if not available else "failed" if invalid else "recorded",
        "database_sha256": snapshot.sha256,
        "schema_version": schema,
        "scope": "Canonical computation_results rows, one answer per validated primary result.",
        "exclusions": [
            "Bills, memos, content snapshots and pre-ledger answers are outside this census.",
            "Free-form adoption or commencement assertions are not inferred.",
        ],
        "citation_evidence": "legacy_missing",
        "semantic": "semantic_not_assessed",
        "result_rows": sum(rows.values()),
        "row_counts": dict(rows),
        "invalid_counts": dict(sorted(invalid.items())),
        "nonblank_claim_counts": dict(claims),
        "blank_claim_slots": blank_slots,
        "answers_without_nonblank_claims": empty_answers,
        "answers": answers,
        **(
            {
                "review_text_coverage": _review_text_coverage(
                    database, snapshot.sha256, answers, review_checkpoints
                )
            }
            if review_checkpoints is not None and available and not invalid
            else {}
        ),
    }


def run(
    database: Path,
    output: Path,
    *,
    review_checkpoints: tuple[ReviewInputCheckpoint, ...] | None = None,
) -> int:
    protected = snapshot_files(database)
    for checkpoint in review_checkpoints or ():
        protected.update(snapshot_files(checkpoint.database))
    if output.resolve() in protected:
        raise ValueError("Report must be separate from the input database")
    report = audit(database, review_checkpoints=review_checkpoints)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, report)
    return 1 if report["status"] == "failed" else 2 if report["status"] == "not_run" else 0


@dataclass(frozen=True)
class ReviewInputCheckpoint:
    database: Path
    sha256: str

    def unchanged(self) -> bool:
        return digest(read_artifact(self.database)) == self.sha256 and not live_journal(
            self.database.resolve()
        )


class ReviewTextUnavailableError(ValueError):
    pass


@dataclass(frozen=True)
class SavedClaimReview:
    record: AnalysisRecord
    number: str
    request_sha256: str
    text_sha256: str
    text: str
    input_checkpoint_sha256: str | None = None


def _primary_batch_meta(db: sqlite3.Connection, paid: ComputationResult) -> BatchItemMeta:
    item = db.execute(
        "SELECT i.*, b.provider FROM llm_batch_items AS i"
        " JOIN llm_batches AS b USING (batch_id)"
        " WHERE i.batch_id=? AND i.custom_id=?",
        (paid.batch_id, paid.custom_id),
    ).fetchone()
    if item is None or (item["provider"], item["term"], item["number"], item["call_kind"]) != (
        paid.provider,
        paid.term,
        paid.number,
        paid.purpose,
    ):
        raise ReviewTextUnavailableError("Review requires the exact saved batch item")
    meta = batch_item_meta(item["call_kind"], item["meta_json"])
    if not isinstance(meta, BatchItemMeta):
        raise ReviewTextUnavailableError("Review requires primary batch metadata")
    return meta


def _checkpoint_text(
    checkpoint: ReviewInputCheckpoint, paid: ComputationResult, current: BatchItemMeta
) -> str:
    with open_snapshot(checkpoint.database) as snapshot:
        if snapshot.sha256 != checkpoint.sha256:
            raise ValueError("Input checkpoint SHA-256 differs from its pin")
        saved = _primary_batch_meta(snapshot.connection, paid)
        if (
            saved.input_binding != current.input_binding
            or saved.generation != current.generation
            or saved.request_sha256 != current.request_sha256
            or saved.input_chars != current.input_chars
            or saved.prepared_input_receipt != current.prepared_input_receipt
            or saved.frozen_request_manifest != current.frozen_request_manifest
            or current.text
            and current.text != saved.text
        ):
            raise ReviewTextUnavailableError(
                "Input checkpoint differs from the exact current request receipt"
            )
        validate_input_snapshots(snapshot.connection)
        if not snapshot.unchanged():
            raise ValueError("Input checkpoint changed during review preparation")
        return saved.text


def _checkpoint_intent_text(
    checkpoint: ReviewInputCheckpoint, paid: ComputationResult, current: BatchItemMeta
) -> str:
    with open_snapshot(checkpoint.database) as snapshot:
        if snapshot.sha256 != checkpoint.sha256:
            raise ValueError("Input checkpoint SHA-256 differs from its pin")
        row = snapshot.connection.execute(
            "SELECT * FROM llm_batch_intents"
            " WHERE custom_id=? AND provider=? AND state='submitting'",
            (paid.custom_id, paid.provider),
        ).fetchone()
        if row is None or paid.provider != "openai":
            raise ReviewTextUnavailableError("Review requires an exact frozen text intent")
        request = parse_batch_request(row["request_json"])
        saved = intent_metadata(request, row["meta_json"])
        question = request.question
        if (
            not isinstance(question, AnalysisQuestion)
            or not isinstance(saved, BatchItemMeta)
            or (request.custom_id, request.term, request.number, request.call_kind)
            != (paid.custom_id, paid.term, paid.number, paid.purpose)
            or request.payload_sha256 != paid.request_sha256
            or request.payload_json is None
            or saved.generation != paid.generation
            or saved.input_binding != paid.input_binding
            or saved.request_sha256 not in (None, paid.request_sha256)
            or saved.input_chars != current.input_chars
            or question.ctx.scan is not None
            or current.text_source != question.ctx.text_source
            or saved.text_source != current.text_source
        ):
            raise ReviewTextUnavailableError(
                "Input checkpoint differs from the exact saved text intent"
            )
        text = question.ctx.text
        manifest = capture_frozen_request_manifest(request, paid.provider)
        if (
            (text != "" if current.text_source == "metadata_only" else not text.strip())
            or len(text) != current.input_chars
            or saved.text != text
            or current.text
            and current.text != text
            or manifest is None
            or current.frozen_request_manifest is not None
            and current.frozen_request_manifest != manifest
        ):
            raise ReviewTextUnavailableError(
                "Input checkpoint differs from the exact saved text intent"
            )
        payload = read_json(request.payload_json)
        match payload:
            case {
                "body": {
                    "input": [
                        {"role": "system"},
                        {"role": "user", "content": [{"type": "input_text", "text": str(prompt)}]},
                    ]
                }
            } if prompt == build_user_prompt(question.ctx):
                pass
            case _:
                raise ReviewTextUnavailableError(
                    "Frozen intent does not contain the exact rendered user text"
                )
        validate_input_snapshots(snapshot.connection)
        if not snapshot.unchanged():
            raise ValueError("Input checkpoint changed during review preparation")
        return text


def _saved_review_input(
    database: Path,
    *,
    database_sha256: str,
    result_id: str,
    input_checkpoint: ReviewInputCheckpoint | None = None,
) -> SavedClaimReview:
    with open_snapshot(database) as snapshot:
        if snapshot.sha256 != database_sha256:
            raise ValueError("Review snapshot SHA-256 differs from its pin")
        db = snapshot.connection
        row = db.execute(
            "SELECT * FROM computation_results WHERE result_id=?", (result_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Review requires a saved primary result")
        paid = read_computation_result(row)
        record = paid.record
        if paid.purpose not in ("analysis", "reanalysis") or not isinstance(record, AnalysisRecord):
            raise ValueError("Review requires a validated primary result")
        meta = _primary_batch_meta(db, paid)
        receipt = meta.prepared_input_receipt
        if (
            paid.input_binding is None
            or paid.request_sha256 is None
            or paid.input_binding != meta.input_binding
            or paid.generation != meta.generation
            or paid.request_sha256 != meta.request_sha256
            or meta.input_chars != record.input_chars
        ):
            raise ReviewTextUnavailableError("Review requires the exact retained submitted text")
        if receipt is None:
            if input_checkpoint is None:
                raise ReviewTextUnavailableError(
                    "Review requires the exact retained submitted text"
                )
            text = _checkpoint_intent_text(input_checkpoint, paid, meta)
            text_sha256 = digest(text.encode())
        else:
            text = (
                meta.text
                if input_checkpoint is None
                else _checkpoint_text(input_checkpoint, paid, meta)
            )
            text_sha256 = digest(text.encode())
            if (
                not text.strip()
                or receipt.request_sha256 != paid.request_sha256
                or receipt.text_chars != meta.input_chars
                or receipt.text_chars != len(text)
                or receipt.text_sha256 != text_sha256
            ):
                raise ReviewTextUnavailableError(
                    "Review requires the exact retained submitted text"
                )
        validate_input_snapshots(db)
        if (
            not snapshot.unchanged()
            or input_checkpoint is not None
            and not input_checkpoint.unchanged()
        ):
            raise ValueError("Input database changed during review preparation")
    return SavedClaimReview(
        record,
        paid.number,
        paid.request_sha256,
        text_sha256,
        text,
        input_checkpoint.sha256 if input_checkpoint is not None else None,
    )


def saved_claim_review(
    database: Path,
    *,
    database_sha256: str,
    result_id: str,
    input_checkpoint: ReviewInputCheckpoint | None = None,
) -> SavedClaimReview:
    review = _saved_review_input(
        database,
        database_sha256=database_sha256,
        result_id=result_id,
        input_checkpoint=input_checkpoint,
    )
    if not review.text:
        raise ReviewTextUnavailableError("The verified request used metadata only")
    return review


def _review_text_coverage(
    database: Path,
    database_sha256: str,
    answers: list[ClaimAnswer],
    checkpoints: tuple[ReviewInputCheckpoint, ...],
) -> dict[str, object]:
    pins = [checkpoint.sha256 for checkpoint in checkpoints]
    if len(pins) > 12 or len(set(pins)) != len(pins) or database_sha256 in pins:
        raise ValueError("Use at most twelve distinct earlier checkpoint pins")
    if any(not checkpoint.unchanged() for checkpoint in checkpoints):
        raise ValueError("An input checkpoint differs from its pin")
    ready = []
    metadata_only = []
    unsupported_claim_sets = 0
    for answer in answers:
        if not 1 <= len(answer["claims"]) <= 128:
            unsupported_claim_sets += 1
            continue
        for checkpoint in (None, *checkpoints):
            try:
                review = _saved_review_input(
                    database,
                    database_sha256=database_sha256,
                    result_id=answer["result_id"],
                    input_checkpoint=checkpoint,
                )
            except ReviewTextUnavailableError:
                continue
            evidence = {
                "result_id": answer["result_id"],
                "checkpoint_sha256": review.input_checkpoint_sha256,
                "request_sha256": review.request_sha256,
                "claim_slots": len(answer["claims"]),
            }
            if review.text:
                ready.append({**evidence, "text_sha256": review.text_sha256})
            else:
                metadata_only.append(evidence)
            break
    if not ReviewInputCheckpoint(database, database_sha256).unchanged() or any(
        not checkpoint.unchanged() for checkpoint in checkpoints
    ):
        raise ValueError("A pinned input changed during the review coverage check")
    return {
        "checkpoint_sha256s": pins,
        "verified_in_current": sum(row["checkpoint_sha256"] is None for row in ready),
        "verified_in_checkpoint": sum(row["checkpoint_sha256"] is not None for row in ready),
        "unsupported_claim_sets": unsupported_claim_sets,
        "unavailable": len(answers) - len(ready) - len(metadata_only) - unsupported_claim_sets,
        "verified_metadata_only": len(metadata_only),
        "metadata_only": metadata_only,
        "ready": ready,
    }


def review_page(
    database: Path,
    *,
    database_sha256: str,
    result_id: str,
    input_checkpoint: ReviewInputCheckpoint | None = None,
) -> str:
    review = saved_claim_review(
        database,
        database_sha256=database_sha256,
        result_id=result_id,
        input_checkpoint=input_checkpoint,
    )
    checkpoint_line = (
        f"<br>Снимок исходного текста <code>{review.input_checkpoint_sha256}</code>"
        if review.input_checkpoint_sha256 is not None
        else ""
    )
    claims = []
    for field, index, text in _claim_slots(review.record):
        if not text.strip():
            continue
        identity = field if index is None else f"{field}[{index}]"
        claims.append(
            f"<section><h3>{identity}</h3><p>{escape(text)}</p>"
            f"<p>SHA-256 <code>{digest(text.encode())}</code></p></section>"
        )
    return (
        '<!doctype html><html lang="ru"><meta charset="utf-8">'
        "<title>Независимая проверка утверждений</title>"
        "<style>body{font:16px system-ui;max-width:75rem;margin:2rem auto;padding:0 1rem}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere}"
        "code{overflow-wrap:anywhere}</style>"
        f"<h1>Проверка анализа проекта {escape(review.number)}</h1>"
        "<p>Смысловая проверка ожидается. Для каждого пункта укажите цитату и отметьте, "
        "подтверждает ли она вывод, круг лиц, условия и дату. Для сравнения редакций "
        "и утверждений о принятии нужны отдельные источники.</p>"
        "<p>Ниже сохранённый текст, поданный в запросе. Другие поля контекста, "
        "прежняя редакция и последующие решения здесь не проверены.</p>"
        f"<p>Снимок <code>{database_sha256}</code><br>"
        f"Результат <code>{escape(result_id)}</code><br>"
        f"Запрос <code>{review.request_sha256}</code><br>"
        f"Текст <code>{review.text_sha256}</code>{checkpoint_line}</p>"
        "<h2>Утверждения</h2>"
        + "".join(claims)
        + "<h2>Текст запроса</h2><pre>"
        + escape(review.text)
        + "</pre></html>\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--review-result", help="write a local HTML review sheet for one result")
    parser.add_argument("--database-sha256", help="required snapshot pin for --review-result")
    parser.add_argument("--review-coverage", action="store_true")
    parser.add_argument("--input-checkpoint", nargs=2, action="append", metavar=("PATH", "SHA256"))
    args = parser.parse_args()
    checkpoints = tuple(
        ReviewInputCheckpoint(Path(path), sha256) for path, sha256 in args.input_checkpoint or ()
    )
    try:
        if args.review_result is None:
            if args.database_sha256 is not None or (checkpoints and not args.review_coverage):
                raise ValueError("Snapshot pins require a review sheet or review coverage")
            code = run(
                args.database,
                args.output,
                review_checkpoints=checkpoints if args.review_coverage else None,
            )
        else:
            if args.review_coverage or len(checkpoints) > 1:
                raise ValueError("A review sheet accepts at most one input checkpoint")
            input_checkpoint = next(iter(checkpoints), None)
            if not args.database_sha256 or args.output.suffix != ".html":
                raise ValueError("Review needs a database SHA-256 pin and a new .html output")
            protected = snapshot_files(args.database)
            if input_checkpoint is not None:
                protected.update(snapshot_files(input_checkpoint.database))
            if args.output.resolve() in protected:
                raise ValueError("Report must be separate from the input database")
            page = review_page(
                args.database,
                database_sha256=args.database_sha256,
                result_id=args.review_result,
                input_checkpoint=input_checkpoint,
            )
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(page)
            code = 0
    except (OSError, sqlite3.Error, ValueError) as exc:
        parser.error(str(exc))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
