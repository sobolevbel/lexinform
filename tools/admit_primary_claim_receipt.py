"""Admit one technical citation into a new offline copy of a pinned SQLite snapshot."""

import argparse
import json
import os
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from lexinform.adapters.primary_claim_receipts import (
    MAX_TECHNICAL_CLAIM_RECEIPTS_PER_RESULT,
    CheckedPrimaryComparisonReceipt,
    read_primary_claim_receipts,
)
from lexinform.adapters.sqlite_repo import SCHEMA_VERSION
from lexinform.models.claim_evidence import (
    MAX_PRIMARY_CLAIM_RECEIPT_BYTES,
    PrimaryClaimTechnicalReceipt,
    PrimaryComparisonTechnicalReceipt,
)
from tools.audit_saved_primary_claims import (
    ReviewInputCheckpoint,
    verify_primary_claim_technical_receipt,
)
from tools.corpus_artifacts import digest
from tools.review_saved_comparison import verify_primary_comparison_technical_receipt
from tools.sqlite_snapshot import live_journal, snapshot_files


def admit_primary_claim_receipt(
    database: Path,
    output: Path,
    *,
    database_sha256: str,
    result_id: str,
    receipt: PrimaryClaimTechnicalReceipt | PrimaryComparisonTechnicalReceipt,
    input_checkpoint: ReviewInputCheckpoint | None = None,
    baseline_checkpoint: ReviewInputCheckpoint | None = None,
) -> dict[str, object]:
    receipt = type(receipt).read(receipt.model_dump_json())
    database = database.resolve(strict=True)
    target = output.parent.resolve(strict=True) / output.name
    protected = snapshot_files(database)
    for checkpoint in (baseline_checkpoint, input_checkpoint):
        if checkpoint is not None:
            protected.update(snapshot_files(checkpoint.database))
    if target in protected or target.exists() or target.is_symlink():
        raise ValueError("Receipt admission requires a new output database")
    if isinstance(receipt, PrimaryComparisonTechnicalReceipt):
        verify_primary_comparison_technical_receipt(
            database,
            database_sha256=database_sha256,
            result_id=result_id,
            receipt=receipt,
            baseline_checkpoint=baseline_checkpoint,
            input_checkpoint=input_checkpoint,
        )
    else:
        if baseline_checkpoint is not None:
            raise ValueError("A baseline checkpoint requires a comparison receipt")
        verify_primary_claim_technical_receipt(
            database,
            database_sha256=database_sha256,
            result_id=result_id,
            receipt=receipt,
            input_checkpoint=input_checkpoint,
        )
    with TemporaryDirectory(dir=target.parent) as directory:
        copy = Path(directory) / "admitted.db"
        shutil.copyfile(database, copy)
        copy.chmod(0o600)
        if live_journal(database) or digest(copy.read_bytes()) != database_sha256:
            raise ValueError("Receipt admission source changed before copying")
        with closing(sqlite3.connect(copy)) as db, db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            if db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                raise ValueError("Receipt admission requires the current reader schema")
            before = read_primary_claim_receipts(db, result_id)
            existing = next(
                (item for item in before if item.receipt.receipt_id == receipt.receipt_id), None
            )
            if existing is not None:
                if existing.receipt != receipt:
                    raise ValueError("An immutable technical claim receipt cannot be replaced")
                admitted = existing
            else:
                if len(before) >= MAX_TECHNICAL_CLAIM_RECEIPTS_PER_RESULT:
                    raise ValueError("Too many technical claim receipts for one result")
                db.execute(
                    "INSERT INTO primary_claim_technical_receipts"
                    " (receipt_id,result_id,receipt_json) VALUES (?,?,?)",
                    (receipt.receipt_id, result_id, receipt.model_dump_json()),
                )
                after = read_primary_claim_receipts(db, result_id)
                (admitted,) = tuple(item for item in after if item.receipt == receipt)
        if live_journal(copy):
            raise ValueError("Receipt admission output has an uncheckpointed journal")
        output_sha256 = digest(copy.read_bytes())
        os.link(copy, target)
    if isinstance(admitted, CheckedPrimaryComparisonReceipt):
        verification = {
            "before_input_verification": admitted.before_verification,
            "current_input_verification": admitted.after_verification,
            "baseline_checkpoint_sha256": admitted.receipt.before.input_checkpoint_sha256,
            "input_checkpoint_sha256": admitted.receipt.after.input_checkpoint_sha256,
        }
    else:
        verification = {
            "current_input_verification": admitted.verification,
            "input_checkpoint_sha256": admitted.receipt.input_checkpoint_sha256,
        }
    return {
        "receipt_id": receipt.receipt_id,
        "result_id": result_id,
        "database_sha256": database_sha256,
        "output_sha256": output_sha256,
        "inserted": existing is None,
        **verification,
        "semantic": "semantic_not_assessed",
    }


def _checkpoint(values: list[str] | None) -> ReviewInputCheckpoint | None:
    return ReviewInputCheckpoint(Path(values[0]), values[1]) if values is not None else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--database-sha256", required=True)
    parser.add_argument("--result-id", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--input-checkpoint", nargs=2, metavar=("PATH", "SHA256"))
    parser.add_argument(
        "--comparison", action="store_true", help="admit a two-request comparison receipt"
    )
    parser.add_argument(
        "--baseline-checkpoint", nargs=2, metavar=("PATH", "SHA256"), help="pin the previous input"
    )
    args = parser.parse_args()
    try:
        with args.receipt.open("rb") as source:
            raw = source.read(MAX_PRIMARY_CLAIM_RECEIPT_BYTES + 1)
        if len(raw) > MAX_PRIMARY_CLAIM_RECEIPT_BYTES:
            raise ValueError("Claim receipt exceeds its UTF-8 byte limit")
        model = (
            PrimaryComparisonTechnicalReceipt if args.comparison else PrimaryClaimTechnicalReceipt
        )
        report = admit_primary_claim_receipt(
            args.database,
            args.output,
            database_sha256=args.database_sha256,
            result_id=args.result_id,
            receipt=model.read(raw.decode()),
            input_checkpoint=_checkpoint(args.input_checkpoint),
            baseline_checkpoint=_checkpoint(args.baseline_checkpoint),
        )
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.error(str(exc))
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
