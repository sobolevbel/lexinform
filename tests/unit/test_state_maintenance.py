import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tools.state_maintenance import MaintenanceSpec, apply, prepare

from lexinform.adapters.sqlite_repo import SCHEMA_VERSION, SqliteBillRepository
from lexinform.adapters.state_checkpoint import GitStateCheckpoint
from lexinform.legacy_maintenance import LegacyMaintenancePlan, LegacyMaintenanceResult
from lexinform.models import DigestItemMeta, LlmBatch, LlmBatchItem
from tests.state_snapshots import dump_at_version
from tests.unit.test_daily_workflow import execution_script
from tests.unit.test_legacy_maintenance import seed_repository
from tests.unit.test_state_checkpoint import git

NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)


@dataclass(frozen=True)
class MaintenanceCase:
    database: Path
    state: Path
    remote: Path
    spec: MaintenanceSpec
    environment: dict[str, str]

    def execute(self, step: str = "Run lexinform") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", execution_script(step)],
            cwd=self.database.parent,
            env=self.environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )

    def dump(self) -> str:
        repo = SqliteBillRepository(self.database)
        try:
            return repo.dump()
        finally:
            repo.close()


@pytest.fixture
def maintenance_case(tmp_path: Path) -> MaintenanceCase:
    database = tmp_path / "current.db"
    repo = seed_repository(database)
    repo.save_llm_batch(
        LlmBatch(
            batch_id="pending-maintenance-fixture",
            provider="openai",
            call_kind="name",
            submitted_at=NOW,
            status="submitted",
            request_count=1,
            estimated_cost_usd=0,
        ),
        [
            LlmBatchItem(
                batch_id="pending-maintenance-fixture",
                custom_id="pending-name",
                call_kind="name",
                term=10,
                number="3039",
                meta=DigestItemMeta(memo_key="pending-name", queued_at=NOW),
            )
        ],
    )
    state, remote = tmp_path / "state", tmp_path / "remote"
    state.mkdir()
    remote.mkdir()
    git(remote, "init", "--bare", "--template=")
    git(state, "init", "-b", "state", "--template=")
    git(state, "remote", "add", "origin", str(remote))
    state_file = state / "lexinform.sql"
    GitStateCheckpoint(repo.dump, state_file, "state", repo.list_open_llm_batches)()
    repo.close()
    spec = prepare(
        database, state_file, expected_state_sha=git(state, "rev-parse", "HEAD"), imported_at=NOW
    )
    wrapper = tmp_path / "uv"
    wrapper.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "args = sys.argv[4:]\n"
        "if args[:3] == ['python', '-m', 'tools.state_maintenance']:\n"
        "    os.execv(sys.executable, [sys.executable, *args[1:]])\n"
        "assert args[:3] == ['lexinform', 'db', 'checkpoint'], 'normal effects forbidden'\n"
        "from lexinform.cli import main\n"
        "sys.argv = args\n"
        "main()\n"
    )
    wrapper.chmod(0o755)
    root = Path(__file__).resolve().parents[2]
    environment = {
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "PYTHONPATH": os.pathsep.join((str(root / "src"), str(root))),
        "GIT_ALLOW_PROTOCOL": "file",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "LEXINFORM_DB_PATH": str(database),
        "LEXINFORM_TELEGRAM_BOT_TOKEN": "",
        "LEXINFORM_TELEGRAM_CHANNEL_ID": "",
        "LEXINFORM_TELEGRAM_LOG_CHANNEL_ID": "",
        "INBOX_PRESENT": "true",
        "EVENT_NAME": "workflow_dispatch",
        "INPUT_COMMAND": "run",
        "INPUT_OPTIONS": "",
        "INPUT_SINCE": "",
        "INPUT_INDEX_RCL_SINCE": "",
        "INPUT_DRY_RUN": "false",
        "INPUT_REPREFILTER_LIMIT": "0",
        "INPUT_REPREFILTER_TEXT_SKIPPED": "false",
        "INPUT_MAINTENANCE_SPEC": spec.model_dump_json(),
        "STATE_MAINTENANCE_ENABLED": "on",
        "STATE_FILE": str(state_file),
        "STATE_BRANCH": "state",
        "GITHUB_OUTPUT": str(tmp_path / "outputs"),
    }
    return MaintenanceCase(database, state, remote, spec, environment)


def test_authorized_finite_maintenance_uses_exact_checkpoint_and_preserves_views(
    maintenance_case: MaintenanceCase,
) -> None:
    case = maintenance_case
    repo = SqliteBillRepository(case.database)
    view = repo.get(10, "3039")
    repo.close()
    run = case.execute()
    assert run.returncode == 0, run.stderr
    receipt = json.loads(run.stdout)
    assert receipt["applied"] and receipt["imported_at"] == NOW.isoformat()
    assert Path(case.environment["GITHUB_OUTPUT"]).read_text() == "maintenance_authorized=true\n"
    assert git(case.remote, "rev-parse", "state") == case.spec.expected_state_sha
    case.environment["MAINTENANCE_AUTHORIZED"] = "true"
    saved = case.execute("Dump database and push state branch")
    assert saved.returncode == 0, saved.stderr
    assert git(case.remote, "rev-parse", "state") != case.spec.expected_state_sha
    assert json.loads(git(case.remote, "show", "state:pending-batches.json")) == {
        "version": 1,
        "batches": [{"batch_id": "pending-maintenance-fixture", "provider": "openai"}],
    }
    compact = case.dump()
    restored = SqliteBillRepository(":memory:")
    try:
        restored.restore(git(case.remote, "show", "state:lexinform.sql") + "\n")
        assert restored.dump() == compact and restored.get(10, "3039") == view
    finally:
        restored.close()


@pytest.mark.parametrize("field", ["expected_state_sha", "snapshot_sha256", "plan_sha256"])
def test_stale_authorization_never_mutates_or_checkpoints(
    maintenance_case: MaintenanceCase, field: str
) -> None:
    case = maintenance_case
    value = case.spec.model_dump(mode="json")
    value[field] = "f" * (40 if field == "expected_state_sha" else 64)
    case.environment["INPUT_MAINTENANCE_SPEC"] = json.dumps(value)
    before = case.dump()
    run = case.execute()
    assert run.returncode != 0
    assert case.dump() == before and not Path(case.environment["GITHUB_OUTPUT"]).exists()
    save = case.execute("Dump database and push state branch")
    assert save.returncode != 0
    assert git(case.remote, "rev-parse", "state") == case.spec.expected_state_sha


@pytest.mark.parametrize(
    "environment",
    [
        {"INPUT_DRY_RUN": "true"},
        {"STATE_MAINTENANCE_ENABLED": "off"},
        {"EVENT_NAME": "repository_dispatch"},
        {"INPUT_OPTIONS": "--no-publish"},
        {"INPUT_INDEX_RCL_SINCE": "2026-10-01"},
        {"INPUT_REPREFILTER_LIMIT": "1"},
        {"INPUT_MAINTENANCE_SPEC": '{"operation":"arbitrary_sql"}'},
        {"INPUT_MAINTENANCE_SPEC": "x" * 4097},
    ],
)
def test_maintenance_dry_run_disabled_and_invalid_paths_never_authorize_save(
    maintenance_case: MaintenanceCase, environment: dict[str, str]
) -> None:
    case = maintenance_case
    case.environment.update(environment)
    before = case.dump()
    result = case.execute()
    assert (result.returncode == 0) == (environment == {"INPUT_DRY_RUN": "true"})
    assert case.dump() == before and not Path(case.environment["GITHUB_OUTPUT"]).exists()
    assert case.execute("Dump database and push state branch").returncode != 0
    assert git(case.remote, "rev-parse", "state") == case.spec.expected_state_sha


def test_existing_finite_import_noop_does_not_authorize_schema_only_save(
    maintenance_case: MaintenanceCase,
) -> None:
    case = maintenance_case
    assert apply(case.database, case.state / "lexinform.sql", case.spec, dry_run=False)["applied"]
    repo = SqliteBillRepository(case.database)
    try:
        GitStateCheckpoint(
            repo.dump, case.state / "lexinform.sql", "state", repo.list_open_llm_batches
        )()
    finally:
        repo.close()
    repeated = prepare(
        case.database,
        case.state / "lexinform.sql",
        expected_state_sha=git(case.state, "rev-parse", "HEAD"),
        imported_at=NOW,
    )
    case.environment["INPUT_MAINTENANCE_SPEC"] = repeated.model_dump_json()
    before = case.dump()
    result = case.execute()
    assert result.returncode == 0 and json.loads(result.stdout)["applied"] is False
    assert case.dump() == before and not Path(case.environment["GITHUB_OUTPUT"]).exists()
    assert case.execute("Dump database and push state branch").returncode != 0


def test_arbitrary_database_is_not_authorized_by_a_matching_state_checkout(
    maintenance_case: MaintenanceCase,
) -> None:
    case = maintenance_case
    repo = SqliteBillRepository(case.database)
    repo.save_analysis_memo("foreign", '{"not":"the restored source"}')
    before = repo.dump()
    repo.close()
    result = case.execute()
    assert result.returncode != 0 and case.dump() == before
    assert not Path(case.environment["GITHUB_OUTPUT"]).exists()
    assert git(case.remote, "rev-parse", "state") == case.spec.expected_state_sha


def test_failed_maintenance_cannot_publish_the_restore_migration_delta(
    maintenance_case: MaintenanceCase,
) -> None:
    case = maintenance_case
    with closing(sqlite3.connect(case.database)) as connection:
        historical = dump_at_version(connection, version=SCHEMA_VERSION - 1)
    source = case.state / "lexinform.sql"
    source.write_text(historical)
    git(case.state, "add", source.name)
    git(
        case.state,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.test",
        "commit",
        "-m",
        "state: historical fixture",
    )
    git(case.state, "push", "origin", "HEAD:state")
    baseline = git(case.state, "rev-parse", "HEAD")
    repo = SqliteBillRepository(case.database)
    repo.restore(historical)
    assert repo.dump() != historical
    repo.close()
    reviewed = prepare(case.database, source, expected_state_sha=baseline, imported_at=NOW)
    invalid = reviewed.model_dump(mode="json")
    invalid["plan_sha256"] = "0" * 64
    case.environment["INPUT_MAINTENANCE_SPEC"] = json.dumps(invalid)
    before = case.dump()
    assert case.execute().returncode != 0 and case.dump() == before
    assert case.execute("Dump database and push state branch").returncode != 0
    assert git(case.remote, "rev-parse", "state") == baseline
    assert git(case.remote, "show", "state:lexinform.sql") + "\n" == historical


def test_failure_after_finite_apply_rolls_back_the_whole_local_mutation(
    maintenance_case: MaintenanceCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = maintenance_case
    before = case.dump()
    original = SqliteBillRepository.apply_legacy_maintenance
    failure = RuntimeError("injected maintenance failure")

    def broken(repo: SqliteBillRepository, plan: LegacyMaintenancePlan) -> LegacyMaintenanceResult:
        assert original(repo, plan).applied
        raise failure

    monkeypatch.setattr(SqliteBillRepository, "apply_legacy_maintenance", broken)
    with pytest.raises(RuntimeError) as raised:
        apply(case.database, case.state / "lexinform.sql", case.spec, dry_run=False)
    assert raised.value is failure and case.dump() == before
    assert not Path(case.environment["GITHUB_OUTPUT"]).exists()


def test_maintenance_workflow_does_not_checkout_commit_or_acknowledge_inbox() -> None:
    text = (Path(__file__).resolve().parents[2] / ".github/workflows/daily.yml").read_text()
    inbox = text.split("      - name: Check out inbox branch (if it exists)\n", 1)[1]
    assert "if: ${{ inputs.maintenance_spec == '' }}" in inbox.split("run: |", 1)[0]
    commit = text.split("      - name: Commit handled commands and push inbox branch\n", 1)[1]
    assert "inputs.maintenance_spec == ''" in commit.splitlines()[0]
    assert (
        "LEXINFORM_RUN_MESSAGE_ID: ${{ inputs.maintenance_spec == '' && inputs.log_message_id || '' }}"
        in text
    )
    final = text.split("      - name: Dump database and push state branch\n", 1)[1]
    assert "steps.run.outputs.maintenance_authorized == 'true'" in final.split("run: |", 1)[0]


@pytest.mark.parametrize(
    "update",
    [
        {"version": True},
        {"version": 1.0},
        {"imported_at": "2026-10-30T12:00:00"},
        {"imported_at": "2026-10-30T12:00:00+01:00"},
        {"extra_sql": "PRIVATE_SENTINEL $(touch injected)"},
    ],
)
def test_spec_refuses_unsupported_fields_without_printing_input_or_authorizing(
    maintenance_case: MaintenanceCase, update: dict[str, object]
) -> None:
    case = maintenance_case
    value = case.spec.model_dump(mode="json")
    value.update(update)
    case.environment["INPUT_MAINTENANCE_SPEC"] = json.dumps(value)
    before = case.dump()
    result = case.execute()
    assert result.returncode != 0 and "PRIVATE_SENTINEL" not in result.stdout + result.stderr
    assert case.dump() == before and not Path(case.environment["GITHUB_OUTPUT"]).exists()
    assert not (case.database.parent / "injected").exists()
