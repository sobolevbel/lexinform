import os
import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pytest
from tools import admit_primary_claim_receipt as admission
from tools.corpus_artifacts import digest

from lexinform.adapters.primary_claim_receipts import MAX_TECHNICAL_CLAIM_RECEIPTS_PER_RESULT
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models.claim_evidence import PrimaryClaimTechnicalReceipt
from lexinform.models.computation_inputs import ComputationInputBinding
from tests.unit.test_primary_claim_citation_reader import primary_result
from tests.unit.test_primary_claim_technical_receipt import receipt_and_saved


@pytest.fixture
def base_snapshot(tmp_path: Path) -> tuple[Path, PrimaryClaimTechnicalReceipt]:
    path = tmp_path / "base.db"
    receipt, _ = receipt_and_saved()
    result = primary_result()
    binding = ComputationInputBinding(term=result.term, number=result.number, reuse_key="fixture")
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        repo.save_computation_result(result.model_copy(update={"input_binding": binding}))
    return path, receipt


def admit(database: Path, output: Path, receipt: PrimaryClaimTechnicalReceipt) -> dict[str, object]:
    return admission.admit_primary_claim_receipt(
        database,
        output,
        database_sha256=digest(database.read_bytes()),
        result_id=receipt.result_id,
        receipt=receipt,
    )


def test_offline_admission_only_publishes_a_new_copy_and_is_idempotent(
    base_snapshot: tuple[Path, PrimaryClaimTechnicalReceipt], tmp_path: Path
) -> None:
    database, receipt = base_snapshot
    base = database.read_bytes()
    first, second = tmp_path / "first.db", tmp_path / "second.db"
    with patch.object(admission, "verify_primary_claim_technical_receipt") as verify:
        inserted = admit(database, first, receipt)
        repeated = admit(first, second, receipt)
    assert verify.call_count == 2
    assert inserted["inserted"] is True and repeated["inserted"] is False
    assert database.read_bytes() == base and first.read_bytes() == second.read_bytes()
    with pytest.raises(ValueError, match="new output database"):
        admit(first, first, receipt)
    with pytest.raises(ValueError, match="new output database"):
        admit(first, Path(str(first) + "-wal"), receipt)


def test_unavailable_exact_input_does_not_create_an_output(
    base_snapshot: tuple[Path, PrimaryClaimTechnicalReceipt], tmp_path: Path
) -> None:
    database, receipt = base_snapshot
    output = tmp_path / "unavailable.db"
    with pytest.raises(ValueError, match="exact saved batch item"):
        admit(database, output, receipt)
    assert not output.exists()


def test_transaction_rejects_a_foreign_request_even_after_preflight(
    base_snapshot: tuple[Path, PrimaryClaimTechnicalReceipt], tmp_path: Path
) -> None:
    database, receipt = base_snapshot
    base = database.read_bytes()
    output = tmp_path / "foreign.db"
    foreign = receipt.model_copy(update={"request_sha256": "b" * 64})
    with (
        patch.object(admission, "verify_primary_claim_technical_receipt"),
        pytest.raises(ValueError, match="identity or request mismatch"),
    ):
        admit(database, output, foreign)
    assert database.read_bytes() == base and not output.exists()


def test_admission_refuses_receipt_cap_without_mutating_the_input(
    base_snapshot: tuple[Path, PrimaryClaimTechnicalReceipt], tmp_path: Path
) -> None:
    database, receipt = base_snapshot
    with closing(sqlite3.connect(database)) as db, db:
        for index in range(MAX_TECHNICAL_CLAIM_RECEIPTS_PER_RESULT):
            evidence = receipt.evidence.model_copy(update={"source_text_sha256": f"{index:064x}"})
            value = receipt.model_copy(
                update={"submitted_text_sha256": evidence.source_text_sha256, "evidence": evidence}
            )
            db.execute(
                "INSERT INTO primary_claim_technical_receipts VALUES (?,?,?)",
                (value.receipt_id, value.result_id, value.model_dump_json()),
            )
    base = database.read_bytes()
    output = tmp_path / "capped.db"
    with (
        patch.object(admission, "verify_primary_claim_technical_receipt"),
        pytest.raises(ValueError, match="Too many technical claim receipts"),
    ):
        admit(database, output, receipt)
    assert database.read_bytes() == base and not output.exists()


def test_output_publication_cannot_overwrite_a_concurrent_file(
    base_snapshot: tuple[Path, PrimaryClaimTechnicalReceipt], tmp_path: Path
) -> None:
    database, receipt = base_snapshot
    output = tmp_path / "reserved.db"
    base = database.read_bytes()
    link = os.link

    def reserve(source: Path, target: Path) -> None:
        target.write_bytes(b"another process")
        link(source, target)

    with (
        patch.object(admission, "verify_primary_claim_technical_receipt"),
        patch.object(os, "link", reserve),
        pytest.raises(FileExistsError),
    ):
        admit(database, output, receipt)
    assert output.read_bytes() == b"another process"
    assert database.read_bytes() == base
