import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
from tools import sqlite_snapshot
from tools.corpus_artifacts import digest, read_artifact
from tools.request_doctor import audit, run

from lexinform.adapters.sqlite_repo import MIGRATIONS, SqliteBillRepository
from lexinform.models import BatchRequest
from lexinform.services.analysis import Waiting
from tests.fakes import FakeTextExtractor
from tests.harness import World

MARKER = "NEVER-EMIT-THE-LEGAL-INPUT"


@pytest.fixture
def ready() -> World:
    world = World(
        profile="production", extractor=FakeTextExtractor(("Art. 1. Cudzoziemcy " + MARKER) * 80)
    )
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.add_bill("3040", "Projekt ustawy o obywatelstwie")
    world.run(track=False)
    world.batch.resolve()
    world.run(track=False)
    return world


def snapshot(world: World, directory: Path) -> Path:
    path = directory / "state.db"
    repository = SqliteBillRepository(path)
    repository.migrate()
    repository.restore(world.repo.dump())
    repository.close()
    return path


def test_doctor_links_saved_results_without_changing_or_exposing_inputs(
    ready: World, tmp_path: Path
) -> None:
    path = snapshot(ready, tmp_path)
    before = digest(path.read_bytes())
    files = sorted(tmp_path.iterdir())

    report = audit(path)

    assert report["status"] == "recorded" and report["summary"]["errors"] == 0
    assert report["summary"]["results_known"] > 0 and report["summary"]["items_known"] == 2
    assert len(report["inputs"]) == 2
    assert all(group["items"] and group["results"] for group in report["inputs"].values())
    assert MARKER not in json.dumps(report)
    assert digest(path.read_bytes()) == before and sorted(tmp_path.iterdir()) == files


def test_legacy_synchronous_results_remain_unknown(tmp_path: Path) -> None:
    world = World()
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run(track=False)

    report = audit(snapshot(world, tmp_path))

    assert report["status"] == "recorded" and report["inputs"] == {}
    assert report["summary"]["results_unknown"] > 0
    assert not report["findings"]


def test_legacy_retained_payload_is_observed_without_inventing_recorded_identity(
    ready: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare = ready.batch.prepare_request

    def legacy(request: BatchRequest) -> BatchRequest:
        return prepare(request).model_copy(update={"payload_sha256": None})

    monkeypatch.setattr(ready.batch, "prepare_request", legacy)
    assert isinstance(ready.analysis.name_bill(ready.bill("3039")), Waiting)

    report = audit(snapshot(ready, tmp_path))

    assert report["summary"]["legacy_retained_payloads"] == 1
    artifact = next(iter(report["intent_artifacts"].values()))
    assert artifact["recorded_sha256"] is None and artifact["observed_sha256"] is not None
    assert artifact["observed_sha256"] not in report["inputs"]
    assert report["status"] == "recorded"


def test_corruption_is_reported_without_hiding_the_next_valid_intent(
    ready: World, tmp_path: Path
) -> None:
    for number in ("3039", "3040"):
        assert isinstance(ready.analysis.name_bill(ready.bill(number)), Waiting)
    first = ready.repo.list_queued_batch_intents()[0].request.custom_id
    path = snapshot(ready, tmp_path)
    with closing(sqlite3.connect(path)) as database, database:
        database.execute(
            "UPDATE llm_batch_intents SET request_json = json_set(request_json, '$.payload_json', ?) "
            "WHERE custom_id = ?",
            ("corrupt " + MARKER, first),
        )
    before = digest(path.read_bytes())

    report = audit(path)

    assert report["status"] == "failed" and report["summary"]["verified_intent_payloads"] == 1
    assert any(
        row["code"] == "payload_checksum_mismatch" and first in row["ref"]
        for row in report["findings"]
    )
    assert digest(path.read_bytes()) == before and MARKER not in json.dumps(report)


@pytest.mark.parametrize("damage", ["invalid_json", "invalid_sha", "missing_batch"])
def test_malformed_or_orphaned_items_are_reported(
    ready: World, tmp_path: Path, damage: str
) -> None:
    path = snapshot(ready, tmp_path)
    with closing(sqlite3.connect(path)) as database, database:
        if damage == "invalid_json":
            database.execute("UPDATE llm_batch_items SET meta_json = ?", ("{" + MARKER,))
        elif damage == "invalid_sha":
            database.execute(
                "UPDATE llm_batch_items SET meta_json = json_set(meta_json, '$.request_sha256', ?)",
                (MARKER,),
            )
        else:
            database.execute("DELETE FROM llm_batches")

    report = audit(path)

    expected = {
        "invalid_json": "invalid_json",
        "invalid_sha": "invalid_request_sha256",
        "missing_batch": "missing_batch",
    }[damage]
    assert report["status"] == "failed"
    assert any(row["code"] == expected for row in report["findings"])
    assert MARKER not in json.dumps(report)


def test_unmatched_result_is_a_warning_and_never_repaired(ready: World, tmp_path: Path) -> None:
    path = snapshot(ready, tmp_path)
    with closing(sqlite3.connect(path)) as database, database:
        database.execute(
            "UPDATE bills SET analysis_json = json_set(analysis_json, '$.request_sha256', ?) "
            "WHERE number='3039'",
            ("0" * 64,),
        )
    before = digest(path.read_bytes())

    report = audit(path)

    assert report["status"] == "recorded" and report["summary"]["errors"] == 0
    assert any(row["code"] == "result_without_batch_receipt" for row in report["findings"])
    assert digest(path.read_bytes()) == before


def test_repeated_input_does_not_claim_duplicate_billing(ready: World, tmp_path: Path) -> None:
    path = snapshot(ready, tmp_path)
    with closing(sqlite3.connect(path)) as database, database:
        batch_id, custom_id = database.execute(
            "SELECT batch_id, custom_id FROM llm_batch_items LIMIT 1"
        ).fetchone()
        database.execute(
            "INSERT INTO llm_batches (batch_id, provider, call_kind, submitted_at, status, request_count, estimated_cost_usd) "
            "SELECT 'retry-batch', provider, call_kind, submitted_at, status, 1, estimated_cost_usd "
            "FROM llm_batches WHERE batch_id=?",
            (batch_id,),
        )
        database.execute(
            "INSERT INTO llm_batch_items (batch_id, custom_id, call_kind, term, number, meta_json, consumed_at) "
            "SELECT 'retry-batch', custom_id, call_kind, term, number, meta_json, consumed_at "
            "FROM llm_batch_items WHERE batch_id=? AND custom_id=?",
            (batch_id, custom_id),
        )

    report = audit(path)

    assert report["status"] == "recorded" and report["summary"]["repeated_inputs"] == 1
    assert report["findings"] == []


@pytest.mark.parametrize("old_schema", [False, True])
def test_empty_or_incomplete_population_is_not_a_completed_check(
    tmp_path: Path, old_schema: bool
) -> None:
    path = tmp_path / "empty.db"
    if old_schema:
        with closing(sqlite3.connect(path)) as database, database:
            database.executescript(MIGRATIONS[0])
    else:
        repository = SqliteBillRepository(path)
        repository.migrate()
        repository.close()
    output = tmp_path / "report.json"

    code = run(path, output)

    assert code == 2 and json.loads(output.read_bytes())["status"] == "not_run"


def test_source_and_existing_report_cannot_be_overwritten(ready: World, tmp_path: Path) -> None:
    path = snapshot(ready, tmp_path)
    before = digest(path.read_bytes())
    with pytest.raises(ValueError, match="separate"):
        run(path, path)
    output = tmp_path / "report.json"
    output.write_text("original")
    with pytest.raises(FileExistsError):
        run(path, output)
    assert output.read_text() == "original" and digest(path.read_bytes()) == before


def test_missing_input_does_not_create_a_database(tmp_path: Path) -> None:
    path = tmp_path / "missing.db"

    with pytest.raises(FileNotFoundError):
        run(path, tmp_path / "report.json")

    assert list(tmp_path.iterdir()) == []


def test_live_wal_is_rejected_without_ignoring_its_changes(ready: World, tmp_path: Path) -> None:
    path = snapshot(ready, tmp_path)
    Path(str(path) + "-wal").write_bytes(b"uncheckpointed changes")

    with pytest.raises(ValueError, match="checkpointed"):
        audit(path)


def test_credential_file_is_rejected_before_sqlite_reads_it(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("synthetic test fixture")

    with pytest.raises(ValueError, match="credential"):
        audit(path)


@pytest.mark.parametrize("corrupt", [False, True])
def test_module_entrypoint_reports_status_without_writing_the_input(
    ready: World, tmp_path: Path, corrupt: bool
) -> None:
    path = snapshot(ready, tmp_path)
    if corrupt:
        with closing(sqlite3.connect(path)) as database, database:
            database.execute("UPDATE llm_batch_items SET meta_json = '{}' || ?", (MARKER,))
    before = digest(path.read_bytes())
    output = tmp_path / "cli-report.json"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.request_doctor",
            "--database",
            str(path),
            "--output",
            str(output),
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == int(corrupt), result.stderr
    report = json.loads(output.read_bytes())
    assert report["status"] == ("failed" if corrupt else "recorded")
    assert digest(path.read_bytes()) == before and MARKER not in json.dumps(report)


def test_writer_starting_after_preflight_invalidates_the_audit(
    ready: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = snapshot(ready, tmp_path)
    read = read_artifact
    writer: sqlite3.Connection | None = None

    def start_writer(source: Path) -> bytes:
        nonlocal writer
        value = read(source)
        if writer is None:
            writer = sqlite3.connect(path)
            writer.execute("UPDATE bills SET title=title || ' concurrently changed'")
            writer.commit()
        return value

    monkeypatch.setattr(sqlite_snapshot, "read_artifact", start_writer)
    try:
        report = audit(path)
    finally:
        if writer is not None:
            writer.close()

    assert report["status"] == "failed"
    assert any(row["code"] == "database_changed_during_audit" for row in report["findings"])
