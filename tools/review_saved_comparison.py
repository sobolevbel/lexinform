"""Render a comparison review sheet from two verified saved primary inputs."""

import argparse
import sqlite3
from html import escape
from pathlib import Path

from lexinform.adapters.computation_history import read_computation_result
from lexinform.models.claim_evidence import PrimaryComparisonTechnicalReceipt
from lexinform.models.reviews import EvidenceCitation, ReviewDocument, citation_document_in
from tools.audit_saved_primary_claims import (
    ReviewInputCheckpoint,
    SavedClaimReview,
    _primary_batch_meta,
    saved_claim_review,
    submitted_review_document,
)
from tools.corpus_artifacts import create_only, digest
from tools.sqlite_snapshot import open_snapshot, snapshot_files


def review_pair(
    database: Path,
    database_sha256: str,
    result_id: str,
    *,
    baseline_checkpoint: ReviewInputCheckpoint | None = None,
    input_checkpoint: ReviewInputCheckpoint | None = None,
) -> tuple[SavedClaimReview, SavedClaimReview]:
    with open_snapshot(database) as snapshot:
        if snapshot.sha256 != database_sha256:
            raise ValueError("Review snapshot SHA-256 differs from its pin")
        row = snapshot.connection.execute(
            "SELECT * FROM computation_results WHERE result_id=?", (result_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Comparison requires a saved reanalysis result")
        paid = read_computation_result(row)
        if paid.purpose != "reanalysis":
            raise ValueError("Comparison requires a saved reanalysis result")
        baseline = _primary_batch_meta(snapshot.connection, paid).reanalysis_baseline
        if baseline is None:
            raise ValueError("Comparison requires a recorded previous result")
        after = saved_claim_review(
            database,
            database_sha256=database_sha256,
            result_id=result_id,
            input_checkpoint=input_checkpoint,
        )
        before = saved_claim_review(
            database,
            database_sha256=database_sha256,
            result_id=baseline.result_id,
            input_checkpoint=baseline_checkpoint,
        )
        baseline.require_context(before.record.analysis.summary, before.record.analysis.key_changes)
        if before.text_sha256 == after.text_sha256:
            raise ValueError("Comparison requires distinct submitted texts")
        if not snapshot.unchanged() or any(
            not checkpoint.unchanged()
            for checkpoint in (baseline_checkpoint, input_checkpoint)
            if checkpoint is not None
        ):
            raise ValueError("A pinned snapshot changed during comparison preparation")
    return before, after


def require_submitted_citation(
    evidence: EvidenceCitation, span: tuple[int, int], source: ReviewDocument
) -> None:
    left, right = span
    if (
        evidence.document_id != source.identity
        or not 0 <= left < right <= len(source.text)
        or not 0 < len(evidence.quote) <= 1200
        or source.text[left:right] != evidence.quote
        or citation_document_in(evidence, (source,), {"project_text"}) is None
    ):
        raise ValueError("Citation differs from its exact submitted-context text span or locator")


def verify_primary_comparison_technical_receipt(
    database: Path,
    *,
    database_sha256: str,
    result_id: str,
    receipt: PrimaryComparisonTechnicalReceipt,
    baseline_checkpoint: ReviewInputCheckpoint | None = None,
    input_checkpoint: ReviewInputCheckpoint | None = None,
) -> None:
    if receipt.after.result_id != result_id:
        raise ValueError("Comparison receipt identifies a different current result")
    for side, checkpoint in (
        (receipt.before, baseline_checkpoint),
        (receipt.after, input_checkpoint),
    ):
        if side.input_checkpoint_sha256 != (checkpoint.sha256 if checkpoint is not None else None):
            raise ValueError("Comparison receipt identifies a different input checkpoint")
    before, after = review_pair(
        database,
        database_sha256,
        result_id,
        baseline_checkpoint=baseline_checkpoint,
        input_checkpoint=input_checkpoint,
    )
    receipt.require_claim(after.record.analysis)
    for side, saved in ((receipt.before, before), (receipt.after, after)):
        if (side.result_id, side.request_sha256, side.submitted_text_sha256) != (
            saved.record.computation_id,
            saved.request_sha256,
            saved.text_sha256,
        ):
            raise ValueError("Comparison receipt differs from its exact saved requests")
        source = submitted_review_document(saved, side.citation.document_id)
        require_submitted_citation(side.citation, side.submitted_span, source)


def _text_section(title: str, edition: SavedClaimReview) -> str:
    checkpoint = (
        f"<br>Снимок текста <code>{edition.input_checkpoint_sha256}</code>"
        if edition.input_checkpoint_sha256 is not None
        else ""
    )
    return (
        f"<section><h2>{title}</h2>"
        f"<p>Результат <code>{escape(edition.record.computation_id or '')}</code><br>"
        f"Запрос <code>{edition.request_sha256}</code><br>"
        f"Текст <code>{edition.text_sha256}</code>{checkpoint}</p>"
        f"<pre>{escape(edition.text)}</pre></section>"
    )


def render(before: SavedClaimReview, after: SavedClaimReview, database_sha256: str) -> str:
    claims = "".join(
        f"<li>{escape(claim)}<br>SHA-256 <code>{digest(claim.encode())}</code></li>"
        for claim in after.record.analysis.changes_since_previous
    )
    return (
        '<!doctype html><html lang="ru"><meta charset="utf-8">'
        "<title>Проверка сравнения редакций</title>"
        "<style>body{font:16px system-ui;max-width:75rem;margin:2rem auto;padding:0 1rem}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere}code{overflow-wrap:anywhere}</style>"
        f"<h1>Сравнение текстов проекта {escape(after.number)}</h1>"
        "<p>Сохранённые тексты двух запросов могут содержать только часть документов. "
        "Смысловая проверка ожидается.</p>"
        f"<p>Снимок <code>{database_sha256}</code></p>"
        "<h2>Утверждения об изменениях</h2>"
        + (f"<ol>{claims}</ol>" if claims else "<p>Сохранённых утверждений нет.</p>")
        + _text_section("Предыдущий текст", before)
        + _text_section("Текст повторного анализа", after)
        + "</html>"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--database-sha256", required=True)
    parser.add_argument("--result-id", required=True)
    parser.add_argument("--input-checkpoint", nargs=2, metavar=("PATH", "SHA256"))
    parser.add_argument("--baseline-checkpoint", nargs=2, metavar=("PATH", "SHA256"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    current = (
        ReviewInputCheckpoint(Path(args.input_checkpoint[0]), args.input_checkpoint[1])
        if args.input_checkpoint
        else None
    )
    previous = (
        ReviewInputCheckpoint(Path(args.baseline_checkpoint[0]), args.baseline_checkpoint[1])
        if args.baseline_checkpoint
        else None
    )
    protected = snapshot_files(args.database)
    for checkpoint in (current, previous):
        if checkpoint is not None:
            protected.update(snapshot_files(checkpoint.database))
    try:
        if (
            args.output.suffix != ".html"
            or args.output.resolve() in protected
            or args.output.exists()
            or args.output.is_symlink()
        ):
            raise ValueError("Review output must be a separate new .html file")
        before, after = review_pair(
            args.database,
            args.database_sha256,
            args.result_id,
            baseline_checkpoint=previous,
            input_checkpoint=current,
        )
        create_only(args.output, render(before, after, args.database_sha256).encode())
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
