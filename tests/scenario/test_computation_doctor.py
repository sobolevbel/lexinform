import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
from tools import sqlite_snapshot
from tools.computation_doctor import audit, run
from tools.corpus_artifacts import read_artifact
from tools.request_doctor import audit as request_audit

from lexinform.models import (
    DeliveryPlan,
    Publication,
    PublicationKind,
    PublicationStatus,
    StatusChange,
)
from tests.computations import retained_result
from tests.fakes import FakeTextExtractor
from tests.harness import CHANNEL, World
from tests.scenario.test_request_doctor import snapshot

MARKER = "NEVER-EMIT-THE-COMPUTATION-CONTEXT"


@pytest.fixture
def computed_world() -> World:
    world = World(
        profile="production",
        analysis_model="gpt-5.1",
        workers=4,
        extractor=FakeTextExtractor(("Art. 1. Cudzoziemcy " + MARKER) * 80),
    )
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run(track=False)
    world.batch.resolve()
    world.run(track=False)
    change_id = world.repo.add_status_change(
        StatusChange(
            term=10,
            number="3039",
            old_fingerprint="before-graph",
            new_fingerprint="graph",
            new_stages=[],
            detected_at=world.clock.now(),
        )
    )
    assert change_id is not None, "the fixture inserts one unique event before its publication"
    world.repo.create_publication(
        Publication(
            term=10,
            number="3039",
            kind=PublicationKind.STATUS_UPDATE,
            status=PublicationStatus.QUEUED,
            channel_id=CHANNEL,
            ref="graph-fixture",
            status_change_id=change_id,
            created_at=world.clock.now(),
            delivery=DeliveryPlan(bill_json=world.bill("3039").model_dump_json()),
        )
    )
    return world


def test_graph_preserves_paid_results_and_frozen_references_without_exposing_inputs(
    computed_world: World,
    tmp_path: Path,
) -> None:
    path = snapshot(computed_world, tmp_path)
    before = path.read_bytes()
    files = sorted(tmp_path.iterdir())

    report = audit(path)

    assert report["status"] == "recorded" and report["findings"] == []
    assert report["summary"]["result_rows"] == 1
    assert report["summary"]["application_rows"] == 2
    assert report["summary"]["projection_refs_known"] >= 3
    assert len(report["references"]) == 1
    assert MARKER not in json.dumps(report)
    assert path.read_bytes() == before and sorted(tmp_path.iterdir()) == files


def test_modern_and_explicit_inline_projections_have_identical_known_reference_counts(
    computed_world: World, tmp_path: Path
) -> None:
    path = snapshot(computed_world, tmp_path)
    compact = audit(path)
    record = computed_world.bill("3039").analysis
    assert record is not None, "the computed fixture has a current primary analysis"
    with closing(sqlite3.connect(path)) as database, database:
        database.execute("UPDATE bills SET analysis_json=?", (record.model_dump_json(),))
        for key, value in computed_world.repo.load_analysis_memo().items():
            database.execute("UPDATE analysis_memo SET record_json=? WHERE key=?", (value, key))

    inline = audit(path)

    assert compact["findings"] == inline["findings"] == []
    assert compact["summary"]["projection_refs_known"] == inline["summary"]["projection_refs_known"]
    assert compact["references"] == inline["references"]
    assert compact["summary"]["modern_primary_projection_refs"] >= 2
    assert inline["summary"].get("modern_primary_projection_refs", 0) == 0


@pytest.mark.parametrize(
    ("query", "code"),
    [
        ("UPDATE computation_results SET result_json='{}'", "invalid_computation_result"),
        ("UPDATE computation_results SET provider='anthropic'", "computation_columns_mismatch"),
        ("UPDATE computation_results SET number='3040'", "computation_columns_mismatch"),
        (
            "UPDATE computation_results SET result_json=json_set(result_json, '$.term', 11)",
            "computation_columns_mismatch",
        ),
        (
            "UPDATE computation_results SET result_json="
            "json_set(result_json, '$.record.computation_id', 'broken')",
            "invalid_computation_result",
        ),
        (
            "UPDATE computation_results SET result_json="
            "json_set(result_json, '$.record.request_sha256', 'broken')",
            "invalid_computation_result",
        ),
        ("UPDATE result_applications SET application_json='{}'", "invalid_result_application"),
        ("UPDATE result_applications SET generation=generation+1", "application_columns_mismatch"),
        (
            "UPDATE result_applications SET application_json="
            "json_set(application_json, '$.result_id', 'unavailable')",
            "application_result_unavailable",
        ),
        ("DELETE FROM computation_results", "projection_result_unavailable"),
        (
            "UPDATE bills SET analysis_json=json_set(analysis_json, '$.computation_id', 'broken')",
            "invalid_computation_reference",
        ),
        (
            "UPDATE bills SET analysis_json=json_set(analysis_json, '$.request_sha256', 'broken')",
            "projection_request_mismatch",
        ),
        (
            "UPDATE bills SET analysis_json=json_set(analysis_json, '$.modern_primary_ref', 'broken')",
            "invalid_modern_primary_projection",
        ),
        (
            "UPDATE bills SET analysis_json=json_set(analysis_json, '$.request_sha256', 'broken')",
            "invalid_modern_primary_projection",
        ),
        ("UPDATE analysis_memo SET record_json='{'", "invalid_json"),
        (
            "UPDATE publications SET delivery_json=json_set(delivery_json, '$.bill_json', '{')",
            "invalid_json",
        ),
        (
            "UPDATE computation_results SET result_json=json_set(result_json,"
            " '$.validation', 'error', '$.record', NULL, '$.error', 'failed')",
            "failed_result_applied",
        ),
    ],
)
def test_graph_errors_are_reported_without_repair(
    computed_world: World,
    tmp_path: Path,
    query: str,
    code: str,
) -> None:
    path = snapshot(computed_world, tmp_path)
    with closing(sqlite3.connect(path)) as database, database:
        if code in ("invalid_computation_reference", "projection_request_mismatch"):
            record = computed_world.bill("3039").analysis
            assert record is not None, "the paid fixture retains the inline corruption baseline"
            database.execute("UPDATE bills SET analysis_json=?", (record.model_dump_json(),))
        database.execute(query)
    before = path.read_bytes()

    report = audit(path)

    assert report["status"] == "failed"
    assert any(item["code"] == code for item in report["findings"])
    assert MARKER not in json.dumps(report) and path.read_bytes() == before


def test_legacy_synchronous_projections_have_unknown_refs_without_guessed_history(
    tmp_path: Path,
) -> None:
    world = World()
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run(track=False)
    path = snapshot(world, tmp_path)
    before = path.read_bytes()

    report = audit(path)

    assert report["status"] == "recorded" and report["summary"]["projection_refs_unknown"] > 0
    assert report["references"] == {} and report["findings"] == []
    assert path.read_bytes() == before


def test_input_scope_can_differ_from_the_collection_owner(
    computed_world: World,
    tmp_path: Path,
) -> None:
    path = snapshot(computed_world, tmp_path)
    with closing(sqlite3.connect(path)) as database, database:
        database.execute(
            "UPDATE computation_results SET result_json="
            "json_set(result_json, '$.input_binding.term', 9)"
        )
    before = path.read_bytes()

    report = audit(path)

    assert report["status"] == "recorded" and report["findings"] == []
    assert path.read_bytes() == before


def test_opaque_future_application_target_does_not_execute_or_reinterpret_its_ref(
    computed_world: World,
    tmp_path: Path,
) -> None:
    path = snapshot(computed_world, tmp_path)
    with closing(sqlite3.connect(path)) as database, database:
        database.execute(
            "UPDATE result_applications SET generation=NULL, application_json="
            "json_set(json_remove(application_json, '$.projected_generation'),"
            " '$.generation', NULL, '$.target',"
            " json_object('kind', 'future_owner', 'ref', 'opaque')) WHERE phase='projection'"
        )
    before = path.read_bytes()

    report = audit(path)

    assert report["status"] == "recorded" and report["findings"] == []
    assert report["application_targets"] == {"future_owner": 1}
    assert path.read_bytes() == before


def test_valid_application_with_nonprimary_projected_witness_keeps_graph_diagnostics(
    computed_world: World,
    tmp_path: Path,
) -> None:
    secondary = retained_result("amendments")
    computed_world.repo.save_computation_result(secondary)
    path = snapshot(computed_world, tmp_path)
    with closing(sqlite3.connect(path)) as database, database:
        database.execute(
            "UPDATE result_applications SET result_id=?, application_json="
            "json_set(application_json, '$.result_id', ?) WHERE phase='projection'",
            (secondary.result_id, secondary.result_id),
        )
    before = path.read_bytes()

    report = audit(path)

    assert report["status"] == "failed"
    assert any(item["code"] == "invalid_primary_projection_witness" for item in report["findings"])
    assert report["summary"]["application_rows"] == 2
    assert report["summary"]["application_projection"] == 1
    assert secondary.result_id in report["references"]
    assert MARKER not in json.dumps(report) and path.read_bytes() == before


def test_both_doctors_keep_the_nested_json_depth_guard(
    computed_world: World,
    tmp_path: Path,
) -> None:
    path = snapshot(computed_world, tmp_path)
    value: dict[str, object] = {"leaf": True}
    for _ in range(60):
        value = {"child": value}
    with closing(sqlite3.connect(path)) as database, database:
        database.execute("UPDATE publications SET delivery_json=?", (json.dumps(value),))
    before = path.read_bytes()

    for report in (audit(path), request_audit(path)):
        assert report["status"] == "failed"
        assert any(item["code"] == "record_nesting_limit" for item in report["findings"])
    assert path.read_bytes() == before


def test_graph_detects_a_writer_starting_after_preflight(
    computed_world: World,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = snapshot(computed_world, tmp_path)
    changed = False

    def change_after_read(source: Path) -> bytes:
        nonlocal changed
        raw = read_artifact(source)
        if not changed:
            with closing(sqlite3.connect(path)) as database, database:
                database.execute("UPDATE bills SET title=title || ' concurrently changed'")
            changed = True
        return raw

    monkeypatch.setattr(sqlite_snapshot, "read_artifact", change_after_read)
    report = audit(path)
    assert report["status"] == "failed"
    assert any(item["code"] == "database_changed_during_audit" for item in report["findings"])


@pytest.mark.parametrize("missing", [False, True])
def test_missing_or_empty_history_is_explicitly_not_run(tmp_path: Path, missing: bool) -> None:
    world = World()
    path = snapshot(world, tmp_path)
    if missing:
        with closing(sqlite3.connect(path)) as database, database:
            database.execute("DROP TABLE result_applications")
    report = audit(path)
    assert report["status"] == "not_run"
    assert report["missing_tables"] == (["result_applications"] if missing else [])


def test_graph_report_cannot_overwrite_input_or_prior_output(
    computed_world: World,
    tmp_path: Path,
) -> None:
    path = snapshot(computed_world, tmp_path)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="separate"):
        run(path, path)
    output = tmp_path / "report.json"
    output.write_text("original")
    with pytest.raises(FileExistsError):
        run(path, output)
    assert output.read_text() == "original" and path.read_bytes() == before


@pytest.mark.parametrize("corrupt", [False, True])
def test_computation_doctor_cli_only_reads_the_declared_copy(
    computed_world: World,
    tmp_path: Path,
    corrupt: bool,
) -> None:
    path = snapshot(computed_world, tmp_path)
    if corrupt:
        with closing(sqlite3.connect(path)) as database, database:
            database.execute("UPDATE result_applications SET application_json='{}'")
    before = path.read_bytes()
    output = tmp_path / "report.json"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.computation_doctor",
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
    report = json.loads(output.read_text())
    assert report["status"] == ("failed" if corrupt else "recorded")
    assert MARKER not in output.read_text() and path.read_bytes() == before
