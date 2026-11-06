"""Inventory saved primary claim slots without exporting text or assessing support."""

import argparse
import sqlite3
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from html import escape
from pathlib import Path

from lexinform.adapters.computation_history import ComputationRowError, read_computation_result
from lexinform.adapters.input_receipts import validate_input_snapshots
from lexinform.models import AnalysisRecord, BatchItemMeta
from lexinform.models.batch import batch_item_meta
from tools.corpus_artifacts import digest, write_json
from tools.sqlite_snapshot import open_snapshot


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


def audit(database: Path) -> dict[str, object]:
    rows: Counter[str] = Counter(validated_primary=0, failed_primary=0, other_purposes=0, invalid=0)
    claims: Counter[str] = Counter(
        key_changes=0, affected_groups=0, effective_date=0, changes_since_previous=0
    )
    invalid: Counter[str] = Counter()
    answers: list[dict[str, object]] = []
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
    }


def run(database: Path, output: Path) -> int:
    if output.resolve() == database.resolve():
        raise ValueError("Report must be separate from the input database")
    report = audit(database)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, report)
    return 1 if report["status"] == "failed" else 2 if report["status"] == "not_run" else 0


@dataclass(frozen=True)
class SavedClaimReview:
    record: AnalysisRecord
    number: str
    request_sha256: str
    text_sha256: str
    text: str


def saved_claim_review(database: Path, *, database_sha256: str, result_id: str) -> SavedClaimReview:
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
            raise ValueError("Review requires the exact saved batch item")
        meta = batch_item_meta(item["call_kind"], item["meta_json"])
        if not isinstance(meta, BatchItemMeta):
            raise ValueError("Review requires primary batch metadata")
        receipt = meta.prepared_input_receipt
        if (
            paid.input_binding is None
            or paid.request_sha256 is None
            or paid.input_binding != meta.input_binding
            or paid.generation != meta.generation
            or paid.request_sha256 != meta.request_sha256
            or receipt is None
            or receipt.request_sha256 != paid.request_sha256
            or not meta.text.strip()
            or receipt.text_chars != meta.input_chars
            or receipt.text_chars != record.input_chars
            or receipt.text_chars != len(meta.text)
            or receipt.text_sha256 != digest(meta.text.encode())
        ):
            raise ValueError("Review requires the exact retained submitted text")
        validate_input_snapshots(db)
        if not snapshot.unchanged():
            raise ValueError("Input database changed during review preparation")
    return SavedClaimReview(
        record, paid.number, paid.request_sha256, receipt.text_sha256, meta.text
    )


def review_page(database: Path, *, database_sha256: str, result_id: str) -> str:
    review = saved_claim_review(database, database_sha256=database_sha256, result_id=result_id)
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
        f"Текст <code>{review.text_sha256}</code></p>"
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
    args = parser.parse_args()
    try:
        if args.review_result is None:
            if args.database_sha256 is not None:
                raise ValueError("The database pin is only used for review sheets")
            code = run(args.database, args.output)
        else:
            if not args.database_sha256 or args.output.suffix != ".html":
                raise ValueError("Review needs a database SHA-256 pin and a new .html output")
            page = review_page(
                args.database, database_sha256=args.database_sha256, result_id=args.review_result
            )
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(page)
            code = 0
    except (OSError, sqlite3.Error, ValueError) as exc:
        parser.error(str(exc))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
