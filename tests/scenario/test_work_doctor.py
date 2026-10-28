import datetime as dt
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
from tools import sqlite_snapshot
from tools.corpus_artifacts import read_artifact
from tools.sqlite_snapshot import open_snapshot
from tools.work_doctor import audit, run

from lexinform.models import (
    DeliveryPlan,
    DocumentWork,
    Publication,
    PublicationKind,
    PublicationStatus,
    ReviewWork,
    Stage,
    StatusChange,
)
from tests.harness import CHANNEL, World
from tests.scenario.test_document_reviews import context
from tests.scenario.test_request_doctor import snapshot

MARKER = "NEVER-EMIT-THE-FROZEN-WORK-CONTEXT"


@pytest.fixture
def work_world() -> World:
    world = World(profile="production")
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach " + MARKER)
    world.run(track=False)
    world.batch.resolve()
    world.run(track=False)
    bill = world.bill("3039")
    change = StatusChange(
        term=10,
        number="3039",
        old_fingerprint="old",
        new_fingerprint="new",
        new_stages=[],
        detected_at=world.clock.now(),
    )
    change_id = world.repo.add_status_change(change)
    assert change_id is not None, "the fixture inserts one unique legislative event"
    world.repo.save_document_work(
        DocumentWork(
            key=f"amendments/{change_id}",
            term=10,
            number="3039",
            change_id=change_id,
            bill_json=bill.model_dump_json(),
            detail_json=world.gateway.details["3039"].model_dump_json(),
            stage_json=Stage(stage_type="CommitteeWork", stage_name=MARKER).model_dump_json(),
            created_at=world.clock.now(),
            next_at=world.clock.now(),
        )
    )
    world.repo.create_publication(
        Publication(
            term=10,
            number="3039",
            kind=PublicationKind.STATUS_UPDATE,
            status=PublicationStatus.UNKNOWN,
            channel_id=CHANNEL,
            ref="saved",
            status_change_id=change_id,
            created_at=world.clock.now(),
            delivery=DeliveryPlan(bill_json=bill.model_dump_json()),
        )
    )
    world.repo.save_review_work(
        10,
        "3039",
        (
            ReviewWork(
                memo_key="recorded-opaque-question",
                purpose="consultation_review",
                context=context(MARKER),
                since=world.clock.now(),
                baseline_day=world.clock.now().date(),
            ),
            ReviewWork(
                memo_key="recorded-input",
                purpose="commission_review",
                input_bill_json=bill.model_dump_json(),
                since=world.clock.now(),
                baseline_day=world.clock.now().date(),
            ),
            ReviewWork(
                memo_key="abandoned",
                purpose="act_application",
                since=world.clock.now(),
                baseline_day=world.clock.now().date(),
            ),
        ),
    )
    return world


def test_work_inventory_preserves_unknown_legacy_keys_and_does_not_expose_inputs(
    work_world: World,
    tmp_path: Path,
) -> None:
    path = snapshot(work_world, tmp_path)
    before = path.read_bytes()
    files = sorted(tmp_path.iterdir())

    report = audit(path)

    assert report["status"] == "recorded" and report["findings"] == []
    assert report["summary"]["document_waiting"] == 1
    assert report["summary"]["review_context"] == report["summary"]["review_snapshot"] == 1
    assert report["summary"]["review_abandoned"] == 1
    assert all(owner["protected_memo_rows"] == 1 for owner in report["owners"])
    assert report["owners"][0]["linked_publications"] == {"unknown": 1}
    assert MARKER not in json.dumps(report)
    assert "recorded-opaque-question" not in json.dumps(report)
    assert path.read_bytes() == before and sorted(tmp_path.iterdir()) == files


def test_work_ages_are_opt_in_and_preserve_the_existing_audit(
    work_world: World, tmp_path: Path
) -> None:
    path = snapshot(work_world, tmp_path)
    before = path.read_bytes()
    observed = work_world.clock.now() + dt.timedelta(days=2)

    legacy = audit(path)
    report = audit(path, observed_at=observed)
    ages = report.pop("work_ages")

    assert report == legacy and "work_ages" not in legacy
    assert ages["nonterminal_owners"] == 3 and ages["coverage"] == "recorded"
    assert all(group["oldest_seconds"] == 172800 for group in ages["groups"])
    assert ages["excluded_terminal_owners"] == {"review/abandoned": 1}
    assert MARKER not in json.dumps(ages) and path.read_bytes() == before


def test_work_age_metrics_do_not_turn_broken_dependencies_into_complete_coverage(
    work_world: World, tmp_path: Path
) -> None:
    path = snapshot(work_world, tmp_path)
    with closing(sqlite3.connect(path)) as database, database:
        database.execute("DELETE FROM status_changes")
    before = path.read_bytes()

    report = audit(path, observed_at=work_world.clock.now())

    assert report["status"] == "failed" and report["work_ages"]["coverage"] == "partial"
    assert path.read_bytes() == before


def test_work_age_metrics_refuse_naive_observation_before_opening_the_database(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="timezone"):
        audit(tmp_path / "not-present.db", observed_at=dt.datetime(2026, 10, 28))


@pytest.mark.parametrize(
    ("query", "code"),
    [
        ("DELETE FROM bills", "missing_document_bill"),
        ("DELETE FROM status_changes", "missing_document_change"),
        ("UPDATE status_changes SET number='4040'", "document_change_owner_mismatch"),
        ("UPDATE document_work SET state='unsupported'", "document_owner_mismatch"),
        ("UPDATE document_work SET next_at='2099-01-01T00:00:00Z'", "document_schedule_mismatch"),
        ("UPDATE document_work SET next_at='invalid'", "invalid_document_schedule"),
        ("UPDATE document_work SET work_json='[]'", "invalid_document_work"),
        (
            "UPDATE document_work SET work_json=json_set(work_json, '$.stage_json', '{broken')",
            "invalid_work_snapshot",
        ),
        ("UPDATE bills SET review_work_json='{'", "invalid_review_work_json"),
        ("UPDATE bills SET review_work_json='{}'", "invalid_review_work_list"),
        ("UPDATE bills SET review_work_json='[null]'", "invalid_review_work"),
        (
            "UPDATE bills SET review_work_json=json_set(review_work_json, '$[0].context.number', '4040')",
            "review_context_owner_mismatch",
        ),
        (
            "UPDATE bills SET review_work_json=json_set(review_work_json, '$[0].context.purpose', 'commission_review')",
            "review_context_owner_mismatch",
        ),
        (
            "UPDATE bills SET review_work_json=json_insert(review_work_json, '$[#]', json_extract(review_work_json, '$[0]'))",
            "duplicate_review_owner",
        ),
    ],
)
def test_broken_work_dependencies_are_reported_without_repair(
    work_world: World,
    tmp_path: Path,
    query: str,
    code: str,
) -> None:
    path = snapshot(work_world, tmp_path)
    with closing(sqlite3.connect(path)) as database, database:
        database.execute(query)
    before = path.read_bytes()

    report = audit(path)

    assert report["status"] == "failed"
    assert any(item["code"] == code for item in report["findings"])
    assert MARKER not in json.dumps(report) and path.read_bytes() == before


@pytest.mark.parametrize("legacy", [False, True])
def test_absent_historical_dependencies_are_warnings_and_are_not_rebuilt(
    work_world: World,
    tmp_path: Path,
    legacy: bool,
) -> None:
    path = snapshot(work_world, tmp_path)
    with closing(sqlite3.connect(path)) as database, database:
        if legacy:
            database.execute(
                "UPDATE document_work SET work_json=json_set(work_json, '$.bill_json', '')"
            )
        else:
            database.execute("DELETE FROM publications")
    before = path.read_bytes()

    report = audit(path)

    assert report["status"] == "recorded" and report["summary"]["warnings"] == 1
    assert report["summary"]["errors"] == 0 and path.read_bytes() == before


def test_empty_legacy_review_ownership_is_visible_without_changing_retention(
    work_world: World,
    tmp_path: Path,
) -> None:
    path = snapshot(work_world, tmp_path)
    with closing(sqlite3.connect(path)) as database, database:
        database.execute("UPDATE bills SET review_work_json='[]'")
    before = path.read_bytes()

    report = audit(path)

    assert report["status"] == "recorded"
    assert report["summary"]["review_owner_rows"] == 1
    assert any(item["code"] == "empty_legacy_review_owner" for item in report["findings"])
    assert path.read_bytes() == before


def test_the_shared_snapshot_reader_is_read_only_and_closes_its_connection(
    work_world: World,
    tmp_path: Path,
) -> None:
    path = snapshot(work_world, tmp_path)
    before = path.read_bytes()
    with open_snapshot(path) as saved:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            saved.connection.execute("DELETE FROM document_work")
        assert saved.unchanged()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        saved.connection.execute("SELECT 1")
    assert path.read_bytes() == before


@pytest.mark.parametrize("suffix", ["-wal", "-journal"])
def test_work_doctor_refuses_uncheckpointed_input(
    work_world: World,
    tmp_path: Path,
    suffix: str,
) -> None:
    path = snapshot(work_world, tmp_path)
    Path(str(path) + suffix).write_bytes(b"uncheckpointed")
    with pytest.raises(ValueError, match="checkpointed"):
        audit(path)


def test_concurrent_changes_invalidate_the_work_audit(
    work_world: World,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = snapshot(work_world, tmp_path)
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


def test_work_report_cannot_overwrite_an_input_or_previous_report(
    work_world: World,
    tmp_path: Path,
) -> None:
    path = snapshot(work_world, tmp_path)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="separate"):
        run(path, path)
    output = tmp_path / "report.json"
    output.write_text("original")
    with pytest.raises(FileExistsError):
        run(path, output)
    assert output.read_text() == "original" and path.read_bytes() == before


@pytest.mark.parametrize("missing", [False, True])
def test_missing_or_empty_work_is_explicitly_not_run(tmp_path: Path, missing: bool) -> None:
    world = World()
    path = snapshot(world, tmp_path)
    if missing:
        with closing(sqlite3.connect(path)) as database, database:
            database.execute("DROP TABLE document_work")
    report = audit(path)
    assert report["status"] == "not_run"
    assert report["missing_tables"] == (["document_work"] if missing else [])


@pytest.mark.parametrize("corrupt", [False, True])
@pytest.mark.parametrize("with_age", [False, True])
def test_work_doctor_cli_uses_only_the_declared_copy(
    work_world: World,
    tmp_path: Path,
    corrupt: bool,
    with_age: bool,
) -> None:
    path = snapshot(work_world, tmp_path)
    if corrupt:
        with closing(sqlite3.connect(path)) as database, database:
            database.execute("UPDATE document_work SET work_json='{}'")
    before = path.read_bytes()
    output = tmp_path / "report.json"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.work_doctor",
            "--database",
            str(path),
            "--output",
            str(output),
            *(["--observed-at", work_world.clock.now().isoformat()] if with_age else []),
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
    assert ("work_ages" in report) == with_age
    if with_age:
        assert report["work_ages"]["coverage"] == ("partial" if corrupt else "recorded")
    assert MARKER not in output.read_text() and path.read_bytes() == before


def test_work_doctor_cli_requires_an_explicit_timezone(tmp_path: Path) -> None:
    output = tmp_path / "report.json"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.work_doctor",
            "--database",
            str(tmp_path / "missing.db"),
            "--output",
            str(output),
            "--observed-at",
            "2026-10-28T00:00:00",
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 2 and "timezone" in result.stderr
    assert not output.exists()
