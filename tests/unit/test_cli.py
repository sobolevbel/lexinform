"""Operator commands through typer, against a temporary database (no network, no Telegram).

These build the real `Container`, which is the point: `daily.yml` runs `db init` → `db restore`
→ `run` → `db dump` through this very CLI, so the commands must not first be exercised there.
What the API would answer comes from `_StubApi`, a local server over the recorded fixtures;
`_env` points every other command at a dead port, so a command that reaches the network by
accident fails here instead of calling api.sejm.gov.pl from the suite.
"""

import json
import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import anthropic
import httpx2 as httpx
import pytest
from typer.testing import CliRunner

from lexinform.adapters.github_inbox import GitHubError
from lexinform.adapters.sqlite_repo import SCHEMA_VERSION, SqliteBillRepository
from lexinform.cli import app
from lexinform.models import (
    SILENCED_BY_OPERATOR,
    AnalysisRecord,
    BillStatus,
    PendingBatch,
    ProcessDetail,
    ProcessSummary,
    RunMode,
    RunReport,
    TokenUsage,
    process_summary,
    wykaz_summary,
)
from lexinform.services.lookup import BillNotFoundError
from tests.conftest import FIXTURES
from tests.fakes import make_analysis
from tests.harness import rcl_project, submission, summary, wykaz_entry

runner = CliRunner()

NOWHERE = "http://127.0.0.1:9"  # the discard port: a request here is refused, never routed out


@pytest.fixture(autouse=True)
def instant_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """A command pointed at the dead port retries three times, 1+2+4 real seconds of the suite."""
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)


class _StubApi(BaseHTTPRequestHandler):
    """api.sejm.gov.pl as far as a CLI command can tell: the recorded fixtures by path, and an
    empty listing for everything else, which is what makes a `run` here a quiet run."""

    fixtures = {
        "/sejm/term10/processes/3039": "process_3039.json",
        "/sejm/term10/prints/3039": "print_3039.json",
    }

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler's own spelling)
        name = self.fixtures.get(self.path.split("?")[0])
        body = (FIXTURES / name).read_bytes() if name else b"[]"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture(scope="module")
def api() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubApi)
    # `shutdown()` waits for the serve loop's next poll, half a second by default.
    loop = {"poll_interval": 0.01}
    threading.Thread(target=server.serve_forever, kwargs=loop, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def db(tmp_path: Path, process_3039: ProcessDetail) -> Path:
    """A database with druk 3039 analysed as not relevant after two failed attempts, and one row
    of each of the other three kinds — a bill without a print number, a project on RCL and a plan
    in the register — because `show` renders each of them its own way."""
    path = tmp_path / "t.db"
    repo = SqliteBillRepository(path)
    repo.migrate()
    now = datetime(2026, 9, 7, tzinfo=UTC)
    repo.upsert_summary(process_3039, now=now)
    repo.record_analysis_failure(10, "3039", "boom")
    repo.record_analysis_failure(10, "3039", "boom")
    repo.save_analysis(
        10,
        "3039",
        AnalysisRecord(
            analysis=make_analysis(relevant=False, score=1),
            model="m",
            prompt_version="v",
            input_chars=10,
            truncated=False,
            text_source="pdf",
            created_at=now,
        ),
    )
    sub = submission()
    repo.upsert_summary(ProcessSummary.from_submission(sub), now=now)
    repo.save_submission(10, sub.number, sub)
    project = rcl_project()
    repo.upsert_summary(process_summary(project, term=10), now=now)
    repo.save_rcl(10, f"RCL/{project.id}", project)
    entry = wykaz_entry()
    repo.upsert_summary(wykaz_summary(entry, term=10), now=now)
    repo.save_wykaz(10, f"WPL/{entry.number}", entry)
    repo.close()
    return path


@pytest.mark.parametrize("command", ["analyze", "preview"])
def test_analysis_and_preview_of_linked_entry_use_print(db: Path, api: str, command: str) -> None:
    repo = SqliteBillRepository(db)
    source = submission().number
    repo.link_bills(10, source, "3039")
    repo.close()

    result = runner.invoke(app, [command, source], env=_env(db, api=api))

    assert result.exit_code == 0, result.output
    assert "3039" in result.output
    assert "номер druku ещё не присвоен" not in result.output
    repo = SqliteBillRepository(db)
    original = repo.get(10, source)
    repo.close()
    assert original is not None and original.status is BillStatus.LINKED


def _env(db: Path, term: str = "10", *, api: str = NOWHERE) -> dict[str, str]:
    # The term is pinned so that no command asks the live API which term is running. No bot
    # token and no log channel, whatever the developer's `.env` says (see `conftest.py`):
    # replies and reports go to the console, never to Telegram.
    return {
        "LEXINFORM_DB_PATH": str(db),
        "LEXINFORM_TELEGRAM_CHANNEL_ID": "@test",
        "LEXINFORM_TELEGRAM_BOT_TOKEN": "",
        "LEXINFORM_TELEGRAM_LOG_CHANNEL_ID": "",
        "LEXINFORM_TERM": term,
        "LEXINFORM_SEJM_API_BASE_URL": api,
        "LEXINFORM_RCL_ENABLED": "false",
        "LEXINFORM_WYKAZ_ENABLED": "false",
    }


def _status(db: Path) -> tuple[BillStatus, int, str | None]:
    repo = SqliteBillRepository(db)
    bill = repo.get(10, "3039")
    repo.close()
    assert bill is not None, "the CLI run under test stored druk 3039"
    return bill.status, bill.analysis_attempts, bill.last_error


def _record_run(db: Path, started: datetime, *, analyzed: int, input_tokens: int) -> None:
    repo = SqliteBillRepository(db)
    report = RunReport(
        started_at=started,
        since=started,
        mode=RunMode.RUN,
        analyzed=analyzed,
        published=1,
        llm_input_tokens=input_tokens,
        llm_output_tokens=500,
        llm_usage={"claude-opus-5": TokenUsage(input=input_tokens, output=500)},
    )
    run_id = repo.start_run(report)
    report.finished_at = started + timedelta(minutes=3)
    repo.finish_run(run_id, report)
    repo.close()


def test_runs_lists_the_recorded_runs_newest_first(db: Path) -> None:
    _record_run(db, datetime(2026, 9, 8, 4, 23, tzinfo=UTC), analyzed=2, input_tokens=100_000)
    _record_run(db, datetime(2026, 9, 9, 4, 23, tzinfo=UTC), analyzed=1, input_tokens=20_000)

    result = runner.invoke(app, ["runs", "--days", "3650"], env=_env(db))

    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if line.startswith("2026-")]
    assert lines[0].startswith("2026-09-09 04:23") and "$0.11" in lines[0]
    assert lines[1].startswith("2026-09-08 04:23") and "$0.51" in lines[1]


def test_runs_all_includes_empty_runs(db: Path) -> None:
    repo = SqliteBillRepository(db)
    started = datetime(2026, 9, 8, 4, 23, tzinfo=UTC)
    report = RunReport(started_at=started, since=started, mode=RunMode.RUN)
    run_id = repo.start_run(report)
    repo.finish_run(run_id, report)
    repo.close()

    filtered = runner.invoke(app, ["runs", "--days", "3650"], env=_env(db))
    all_runs = runner.invoke(app, ["runs", "--days", "3650", "--all"], env=_env(db))

    assert filtered.exit_code == all_runs.exit_code == 0
    assert "no non-empty runs" in filtered.output
    assert "2026-09-08 04:23" in all_runs.output


def test_cost_sums_the_runs_and_names_the_dearest_analyses(db: Path) -> None:
    _record_run(db, datetime(2026, 9, 8, 4, 23, tzinfo=UTC), analyzed=2, input_tokens=100_000)
    _record_run(db, datetime(2026, 9, 9, 4, 23, tzinfo=UTC), analyzed=1, input_tokens=20_000)

    result = runner.invoke(app, ["cost", "--days", "3650"], env=_env(db))

    assert result.exit_code == 0, result.output
    assert "2 run(s) in the last 3650 days: $0.62 total, $0.31 per run" in result.output
    assert "claude-opus-5: in 120.0k" in result.output
    assert "most expensive run: 2026-09-08 04:23 ($0.51, 2 analysed)" in result.output
    assert "3039" not in result.output  # the fixture's analysis has no token counts to rank


def test_reset_puts_the_bill_back_with_a_clean_budget(db: Path) -> None:
    result = runner.invoke(app, ["reset", "3039", "--to", "analysis_pending", "-y"], env=_env(db))

    assert result.exit_code == 0, result.output
    assert "analyzed (attempts 2) -> analysis_pending" in result.output
    assert _status(db) == (BillStatus.ANALYSIS_PENDING, 0, None)


def test_a_bill_of_an_older_term_is_still_found_after_the_sejm_moved_on(db: Path) -> None:
    # The Sejm is in term 11 now; druk 3039 of term 10 is still in the database.
    result = runner.invoke(
        app, ["reset", "3039", "--to", "analysis_pending", "-y"], env=_env(db, term="11")
    )

    assert result.exit_code == 0, result.output
    assert _status(db)[0] is BillStatus.ANALYSIS_PENDING


def test_reset_asks_before_changing_anything(db: Path) -> None:
    result = runner.invoke(app, ["reset", "3039"], env=_env(db), input="n\n")

    assert result.exit_code == 1
    assert _status(db)[0] is BillStatus.ANALYZED


def test_republish_refuses_a_bill_without_a_relevant_analysis(db: Path) -> None:
    result = runner.invoke(app, ["republish", "3039", "-y"], env=_env(db))

    assert result.exit_code == 2
    assert "no relevant analysis" in result.output


def test_forget_says_so_when_the_channel_carries_no_card(db: Path) -> None:
    result = runner.invoke(app, ["forget", "3039", "-y"], env=_env(db))

    assert result.exit_code == 0, result.output
    assert "nothing to forget" in result.output


def test_commands_answers_the_inbox_and_empties_it(db: Path, tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "5.json").write_text(
        json.dumps(
            {
                "update_id": 5,
                "chat_id": "-1001",
                "message_id": 9,
                "text": "/show 3039",
                "received_at": "2026-09-11T08:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    (inbox / "README.md").write_text("not a command", encoding="utf-8")

    # The container builds the Telegram publisher eagerly: a dummy token, and an API address
    # nothing listens on, so that an accidental send fails here instead of reaching Telegram.
    env = {
        **_env(db),
        "LEXINFORM_INBOX_DIR": str(inbox),
        "LEXINFORM_TELEGRAM_BOT_TOKEN": "test-token",
        "LEXINFORM_TELEGRAM_API_BASE_URL": "http://127.0.0.1:9",
    }
    result = runner.invoke(app, ["commands"], env=env)

    assert result.exit_code == 0, result.output
    assert "/show 3039 → 3039 shown" in result.output
    assert "handled=1 failed=0 errors=0" in result.output
    assert sorted(p.name for p in inbox.iterdir()) == ["README.md"]


def test_commands_without_an_inbox_says_what_to_set(db: Path) -> None:
    result = runner.invoke(app, ["commands"], env=_env(db))

    assert result.exit_code == 2
    assert "LEXINFORM_INBOX_DIR" in result.output


def test_db_dump_and_restore_round_trip(db: Path, tmp_path: Path) -> None:
    dump = tmp_path / "state.sql"

    dumped = runner.invoke(app, ["db", "dump", str(dump)], env=_env(db))
    fresh = tmp_path / "fresh.db"
    restored = runner.invoke(app, ["db", "restore", str(dump)], env=_env(fresh))

    assert dumped.exit_code == 0 and dump.exists()
    assert restored.exit_code == 0, restored.output
    assert _status(fresh) == (BillStatus.ANALYZED, 2, None)  # save_analysis clears the error


def test_db_restore_of_a_missing_dump_is_an_error_unless_allowed(tmp_path: Path) -> None:
    missing = tmp_path / "absent.sql"
    env = _env(tmp_path / "t.db")

    strict = runner.invoke(app, ["db", "restore", str(missing)], env=env)
    lenient = runner.invoke(app, ["db", "restore", str(missing), "--missing-ok"], env=env)

    assert strict.exit_code == 2
    assert lenient.exit_code == 0 and "empty database" in lenient.output


def test_db_init_creates_the_schema_at_the_current_version(tmp_path: Path) -> None:
    """The first step of every `daily.yml` run, and the only one that builds a database."""
    fresh = tmp_path / "fresh.db"

    result = runner.invoke(app, ["db", "init"], env=_env(fresh))

    assert result.exit_code == 0, result.output
    repo = SqliteBillRepository(fresh)
    version = repo.schema_version
    repo.close()
    assert version == SCHEMA_VERSION


def test_run_over_a_quiet_sejm_reports_it_and_changes_nothing(db: Path, api: str) -> None:
    """The daily job end to end: the container, all five phases and the report, against an API
    that answers every listing with nothing. `--dry-run` rolls the database back, so a run that
    found nothing must also have recorded nothing."""
    result = runner.invoke(
        app, ["run", "--dry-run", "--since", "2026-09-01"], env=_env(db, api=api)
    )

    assert result.exit_code == 0, result.output
    assert '"discovered": 0' in result.output and '"errors": []' in result.output
    assert '"published": 0' in result.output and '"tracked": 0' in result.output
    repo = SqliteBillRepository(db)
    runs = repo.list_runs(since=datetime(2026, 1, 1, tzinfo=UTC))
    repo.close()
    assert runs == []


def test_a_run_that_cannot_even_start_says_why_instead_of_raising(tmp_path: Path) -> None:
    """A misconfigured deploy — here a database path that is a directory. The run never gets a
    pipeline, so the failure has nowhere to go but the log (and the log channel, when one is
    configured), and the exit code is what tells the workflow."""
    result = runner.invoke(app, ["run"], env=_env(tmp_path))

    assert result.exit_code == 1
    assert "startup failed: OperationalError" in result.output


def test_scan_prints_the_counters_and_the_queue_it_leaves_behind(db: Path, api: str) -> None:
    result = runner.invoke(app, ["scan", "--since", "2026-09-01"], env=_env(db, api=api))

    assert result.exit_code == 0, result.output
    assert "term=10 since=2026-09-01T00:00:00+00:00 seen=0 new=0" in result.output
    assert "rcl_new=0 rcl_hits=0" in result.output  # RCL off: the line still accounts for it


def test_track_reports_what_it_posted(db: Path, api: str) -> None:
    result = runner.invoke(app, ["track", "--dry-run"], env=_env(db, api=api))

    assert result.exit_code == 0, result.output
    assert "updates=0 errors=[]" in result.output


def test_collect_batches_reports_what_it_finished(db: Path, api: str) -> None:
    result = runner.invoke(app, ["collect-batches", "--dry-run"], env=_env(db, api=api))

    assert result.exit_code == 0, result.output
    assert "analyzed=0 published=0 updates=0 errors=[]" in result.output


def test_collect_batches_also_answers_the_inbox(db: Path, api: str, tmp_path: Path) -> None:
    # A batch-ready run can replace a pending inbox run in the workflow's concurrency group.
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "5.json").write_text(
        json.dumps(
            {
                "update_id": 5,
                "chat_id": "-1001",
                "message_id": 9,
                "text": "/show 3039",
                "received_at": "2026-09-11T08:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    env = {
        **_env(db, api=api),
        "LEXINFORM_INBOX_DIR": str(inbox),
        "LEXINFORM_TELEGRAM_BOT_TOKEN": "test-token",
        "LEXINFORM_TELEGRAM_API_BASE_URL": "http://127.0.0.1:9",
    }

    result = runner.invoke(app, ["collect-batches"], env=env)

    assert result.exit_code == 0, result.output
    assert list(inbox.iterdir()) == []


def test_poll_batches_does_nothing_when_batching_is_off(db: Path, api: str) -> None:
    result = runner.invoke(app, ["poll-batches"], env=_env(db, api=api))

    assert result.exit_code == 0, result.output
    assert "batching is off" in result.output


def test_poll_batches_needs_a_github_repo_and_token(db: Path, api: str) -> None:
    env = {**_env(db, api=api), "LEXINFORM_LLM_BATCH_ENABLED": "true"}

    result = runner.invoke(app, ["poll-batches"], env=env)

    assert result.exit_code == 2
    assert "no GitHub repo/token" in result.output


def _fake_finished_anthropic_batch(
    monkeypatch: pytest.MonkeyPatch, *, ended_minutes_ago: int = 1
) -> None:
    _fake_pending_batch(monkeypatch, "anthropic")

    class _FakeBatch:
        processing_status = "ended"
        ended_at = datetime.now(UTC) - timedelta(minutes=ended_minutes_ago)

    class _FakeBatches:
        def retrieve(self, batch_id: str) -> _FakeBatch:
            assert batch_id == "owned-batch"
            return _FakeBatch()

    class _FakeMessages:
        batches = _FakeBatches()

    class _FakeAnthropic:
        def __init__(self, *, api_key: str) -> None:
            self.messages = _FakeMessages()

        def __enter__(self) -> _FakeAnthropic:
            return self

        def __exit__(self, *args: object) -> None:
            pass

    monkeypatch.setattr("lexinform.cli.anthropic.Anthropic", _FakeAnthropic)


def _fake_pending_batch(monkeypatch: pytest.MonkeyPatch, provider: str) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    batch = PendingBatch.model_validate(
        dict(
            batch_id="owned-batch",
            provider=provider,
            call_kind="analysis",
            submitted_at=datetime.now(UTC),
            status="submitted",
            request_count=1,
            estimated_cost_usd=0.01,
        )
    )
    monkeypatch.setattr("lexinform.cli._pending_batches", lambda writer, branch: [batch])


def test_poll_batches_ignores_provider_batches_absent_from_persisted_state(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "lexinform.cli.GitHubInboxWriter.read_pending_batches",
        lambda self, branch: '{"version":1,"batches":[]}',
    )
    monkeypatch.setattr(
        "lexinform.cli.anthropic.Anthropic", _raise(AssertionError("provider called"))
    )
    monkeypatch.setattr(
        "lexinform.cli.GitHubInboxWriter.dispatch", _raise(AssertionError("dispatched"))
    )
    env = {
        **_env(db),
        "LEXINFORM_LLM_BATCH_ENABLED": "true",
        "LEXINFORM_GITHUB_REPO": "owner/repo",
        "LEXINFORM_GITHUB_TOKEN": "TOKEN",
    }

    for _ in range(2):
        result = runner.invoke(app, ["poll-batches"], env=env)
        assert result.exit_code == 0, result.output
        assert "no pending batches in persisted state" in result.output


@pytest.mark.parametrize("error", [GitHubError("read state: HTTP 403"), ValueError("bad dump")])
def test_poll_batches_does_not_dispatch_without_readable_state(
    db: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    monkeypatch.setattr("lexinform.cli.GitHubInboxWriter.read_pending_batches", _raise(error))
    monkeypatch.setattr(
        "lexinform.cli.GitHubInboxWriter.dispatch", _raise(AssertionError("dispatched"))
    )
    env = {
        **_env(db),
        "LEXINFORM_LLM_BATCH_ENABLED": "true",
        "LEXINFORM_GITHUB_REPO": "owner/repo",
        "LEXINFORM_GITHUB_TOKEN": "TOKEN",
    }

    result = runner.invoke(app, ["poll-batches"], env=env)

    assert result.exit_code == 1, result.output
    assert "could not" in result.output


def _raise(error: Exception) -> Callable[..., None]:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise error

    return fail


def test_poll_batches_asks_github_to_collect_a_finished_batch(
    db: Path, api: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_finished_anthropic_batch(monkeypatch)
    dispatched: list[str] = []
    monkeypatch.setattr(
        "lexinform.cli.GitHubInboxWriter.dispatch",
        lambda self, event_type, client_payload=None: dispatched.append(event_type),
    )
    env = {
        **_env(db, api=api),
        "LEXINFORM_LLM_BATCH_ENABLED": "true",
        "LEXINFORM_GITHUB_REPO": "owner/repo",
        "LEXINFORM_GITHUB_TOKEN": "TOKEN",
    }

    result = runner.invoke(app, ["poll-batches"], env=env)

    assert result.exit_code == 0, result.output
    assert "asked GitHub to collect" in result.output
    assert dispatched == ["batch-ready"]


def test_poll_batches_asks_for_an_anthropic_batch_that_ended_long_ago(
    db: Path, api: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_finished_anthropic_batch(monkeypatch, ended_minutes_ago=180)
    dispatched: list[str] = []
    monkeypatch.setattr(
        "lexinform.cli.GitHubInboxWriter.dispatch",
        lambda self, event_type, client_payload=None: dispatched.append(event_type),
    )
    env = {
        **_env(db, api=api),
        "LEXINFORM_LLM_BATCH_ENABLED": "true",
        "LEXINFORM_GITHUB_REPO": "owner/repo",
        "LEXINFORM_GITHUB_TOKEN": "TOKEN",
    }

    result = runner.invoke(app, ["poll-batches"], env=env)

    assert result.exit_code == 0, result.output
    assert dispatched == ["batch-ready"]


def _fake_finished_openai_batch(
    monkeypatch: pytest.MonkeyPatch, *, completed_minutes_ago: int, status: str = "completed"
) -> None:
    _fake_pending_batch(monkeypatch, "openai")

    class _FakeBatch:
        completed_at = int(
            (datetime.now(UTC) - timedelta(minutes=completed_minutes_ago)).timestamp()
        )

        def __init__(self) -> None:
            self.status = status

    class _FakeBatches:
        def retrieve(self, batch_id: str) -> _FakeBatch:
            assert batch_id == "owned-batch"
            return _FakeBatch()

    class _FakeOpenAi:
        def __init__(self, *, api_key: str) -> None:
            self.batches = _FakeBatches()

        def __enter__(self) -> _FakeOpenAi:
            return self

        def __exit__(self, *args: object) -> None:
            pass

    monkeypatch.setattr("lexinform.cli.openai.OpenAI", _FakeOpenAi)


@pytest.mark.parametrize(("minutes_ago", "asked"), [(5, True), (180, True)])
def test_poll_batches_collects_owned_openai_batch_regardless_of_age(
    db: Path, api: str, monkeypatch: pytest.MonkeyPatch, minutes_ago: int, asked: bool
) -> None:
    _fake_finished_openai_batch(monkeypatch, completed_minutes_ago=minutes_ago)
    dispatched: list[str] = []
    monkeypatch.setattr(
        "lexinform.cli.GitHubInboxWriter.dispatch",
        lambda self, event_type, client_payload=None: dispatched.append(event_type),
    )
    env = {
        **_env(db, api=api),
        "LEXINFORM_LLM_BATCH_ENABLED": "true",
        "LEXINFORM_GITHUB_REPO": "owner/repo",
        "LEXINFORM_GITHUB_TOKEN": "TOKEN",
        "LEXINFORM_LLM_BATCH_PROVIDER": "openai",
        "OPENAI_API_KEY": "test-key",
    }

    result = runner.invoke(app, ["poll-batches"], env=env)

    assert result.exit_code == 0, result.output
    assert dispatched == (["batch-ready"] if asked else [])


@pytest.mark.parametrize("status", ["completed", "failed", "expired", "cancelled", "in_progress"])
def test_poll_batches_checks_original_provider_and_terminal_failures(
    db: Path, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    _fake_finished_openai_batch(monkeypatch, completed_minutes_ago=180, status=status)
    dispatched: list[str] = []
    monkeypatch.setattr(
        "lexinform.cli.GitHubInboxWriter.dispatch",
        lambda self, event_type: dispatched.append(event_type),
    )
    env = {
        **_env(db),
        "LEXINFORM_LLM_BATCH_ENABLED": "true",
        "LEXINFORM_LLM_BATCH_PROVIDER": "anthropic",
        "LEXINFORM_GITHUB_REPO": "owner/repo",
        "LEXINFORM_GITHUB_TOKEN": "TOKEN",
    }

    result = runner.invoke(app, ["poll-batches"], env=env)

    assert result.exit_code == 0, result.output
    assert dispatched == ([] if status == "in_progress" else ["batch-ready"])


def test_poll_batches_says_so_when_the_provider_refuses(
    db: Path, api: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The VPS may hold no key for the provider at all: every 20 minutes, a clean line."""
    _fake_pending_batch(monkeypatch, "anthropic")
    monkeypatch.setattr(
        "lexinform.cli.anthropic.Anthropic",
        _raise(anthropic.APIConnectionError(request=httpx.Request("GET", "https://api.test"))),
    )
    env = {
        **_env(db, api=api),
        "LEXINFORM_LLM_BATCH_ENABLED": "true",
        "LEXINFORM_GITHUB_REPO": "owner/repo",
        "LEXINFORM_GITHUB_TOKEN": "TOKEN",
    }

    result = runner.invoke(app, ["poll-batches"], env=env)

    assert result.exit_code == 1
    assert "could not ask batch provider" in result.output


def test_poll_batches_says_so_when_github_refuses_instead_of_crashing(
    db: Path, api: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The timer fires this every 20 minutes; a GitHub that refuses (a rotated token, a network
    of the moment) must leave a line in the journal, not a traceback. The batch waits either
    way: the next scheduled run collects it."""
    _fake_finished_anthropic_batch(monkeypatch)
    monkeypatch.setattr(
        "lexinform.cli.GitHubInboxWriter.dispatch",
        _raise(GitHubError("dispatch: HTTP 401: bad credentials")),
    )
    env = {
        **_env(db, api=api),
        "LEXINFORM_LLM_BATCH_ENABLED": "true",
        "LEXINFORM_GITHUB_REPO": "owner/repo",
        "LEXINFORM_GITHUB_TOKEN": "TOKEN",
    }

    result = runner.invoke(app, ["poll-batches"], env=env)

    assert result.exit_code == 1
    assert "could not ask GitHub" in result.output and "HTTP 401" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_reprefilter_has_nothing_to_do_when_no_bill_was_skipped(db: Path, api: str) -> None:
    result = runner.invoke(app, ["reprefilter"], env=_env(db, api=api))

    assert result.exit_code == 0, result.output
    assert "scanned=0 accepted=0" in result.output


def test_reprefilter_survives_a_log_channel_it_cannot_reach(db: Path, api: str) -> None:
    """The report is the last thing the backfill does and its work is already written down, so a
    notifier that cannot even be built must not fail it. On 15 Sept 2026 one did, and 36 minutes
    of scanning went with it: the step had the log channel's credentials and not the readers',
    which `require_telegram` asks for all the same."""
    env = {**_env(db, api=api), "LEXINFORM_TELEGRAM_LOG_CHANNEL_ID": "@log"}
    env["LEXINFORM_TELEGRAM_CHANNEL_ID"] = ""

    result = runner.invoke(app, ["reprefilter"], env=env)

    assert result.exit_code == 0, result.output
    assert "scanned=0 accepted=0" in result.output


def test_reprefilter_leaves_a_bill_the_operator_silenced_alone(db: Path, api: str) -> None:
    """`/skip` writes `SKIPPED_PREFILTER` like a keyword miss, so a backfill scanned it and put
    it back in the analysis queue — druki 1039 and 1040 on the state of 15 Sept 2026, which is
    the one thing `/skip` promises will not happen."""
    repo = SqliteBillRepository(db)
    repo.reset_bill(10, "3039", BillStatus.SKIPPED_PREFILTER, reason=SILENCED_BY_OPERATOR)
    repo.close()

    result = runner.invoke(app, ["reprefilter"], env=_env(db, api=api))

    assert result.exit_code == 0, result.output
    assert "scanned=0 accepted=0" in result.output
    repo = SqliteBillRepository(db)
    bill = repo.get(10, "3039")
    repo.close()
    assert bill is not None and bill.status is BillStatus.SKIPPED_PREFILTER


def test_reprefilter_says_so_when_the_text_prefilter_is_switched_off(db: Path) -> None:
    env = {**_env(db), "LEXINFORM_TEXT_PREFILTER_ENABLED": "false"}

    result = runner.invoke(app, ["reprefilter"], env=env)

    assert result.exit_code == 2
    assert "LEXINFORM_TEXT_PREFILTER_ENABLED" in result.output


def _four_title_misses(path: Path, process_3039: ProcessDetail) -> Path:
    """Four prints waiting for the backfill; only 3039 is one the stub API can answer."""
    repo = SqliteBillRepository(path)
    repo.migrate()
    now = datetime(2026, 9, 7, tzinfo=UTC)
    repo.upsert_summary(process_3039, now=now)
    for number in ("3040", "3041", "3042"):
        repo.upsert_summary(summary(number, f"Projekt ustawy o zmianie ustawy {number}"), now=now)
    for number in ("3039", "3040", "3041", "3042"):
        repo.reset_bill(10, number, BillStatus.SKIPPED_PREFILTER)
    repo.close()
    return path


def _backfilled(path: Path, process_3039: ProcessDetail, api: str, workers: str) -> object:
    db = _four_title_misses(path, process_3039)
    env = {**_env(db, api=api), "LEXINFORM_SEJM_CONCURRENCY": workers}

    result = runner.invoke(app, ["reprefilter"], env=env)

    assert result.exit_code == 0, result.output
    assert "scanned=4" in result.output, result.output
    said = [ln for ln in result.output.splitlines() if ln.startswith(("  ", "scanned="))]
    repo = SqliteBillRepository(db)
    stored = [(n, b.status) for n in ("3039", "3040", "3041", "3042") if (b := repo.get(10, n))]
    repo.close()
    return said, stored


def test_reprefilter_gives_the_same_result_on_four_workers_as_on_one(
    tmp_path: Path, process_3039: ProcessDetail, api: str
) -> None:
    """The backfill fans out over the network and writes in the calling thread in input order, so
    four workers must say and store exactly what one does — the invariant `fan_out` exists for."""
    one = _backfilled(tmp_path / "one.db", process_3039, api, "1")
    four = _backfilled(tmp_path / "four.db", process_3039, api, "4")

    assert one == four


def test_reprefilter_keeps_what_it_scanned_when_the_source_goes_down(
    tmp_path: Path, process_3039: ProcessDetail
) -> None:
    """An outage ends the backfill instead of raising through it: the summary is printed and the
    rows already scanned stay written, which is what `_tell_the_log_channel` is there to say."""
    db = _four_title_misses(tmp_path / "down.db", process_3039)

    result = runner.invoke(app, ["reprefilter"], env=_env(db, api=NOWHERE))

    assert result.exit_code == 0, result.output
    assert "scanned=" in result.output


def test_show_prints_the_process_its_stages_and_what_the_database_knows(db: Path, api: str) -> None:
    result = runner.invoke(app, ["show", "3039"], env=_env(db, api=api))

    assert result.exit_code == 0, result.output
    assert "Poselski projekt ustawy o zmianie ustawy o udzielaniu cudzoziemcom" in result.output
    assert "type=BILL applicant=deputies passed=False closure=None" in result.output
    assert "ReadingReferral              Skierowano do I czytania w komisjach" in result.output
    assert "3039.pdf" in result.output
    assert "+ 3039-001:" in result.output  # the documents filed to the print after it
    assert "local: status=analyzed hits=[] attempts=2" in result.output


def test_show_of_a_bill_without_a_print_number_reads_its_submission(db: Path) -> None:
    """An `RPW/` row has no process to ask about: what is known is the `/bills` entry, and the
    database has it, so this branch never touches the API."""
    result = runner.invoke(app, ["show", "RPW/29075/2026"], env=_env(db))

    assert result.exit_code == 0, result.output
    assert "o udzielaniu cudzoziemcom ochrony" in result.output
    assert "received=2026-09-02 applicant=deputies" in result.output
    assert "print=- consultation=2026-09-02..2026-09-30" in result.output
    assert "local: status=discovered" in result.output


def test_show_of_an_rcl_project_prints_its_timeline_and_texts(db: Path) -> None:
    result = runner.invoke(app, ["show", "RCL/12414100"], env=_env(db))

    assert result.exit_code == 0, result.output
    assert "wykaz=UC164" in result.output
    assert "3. Konsultacje publiczne" in result.output
    assert "reached" in result.output and "not_started" in result.output
    assert "consultation: deadline=2026-09-08" in result.output
    assert "uzasadnienie.doc" in result.output and "OSR.DOCX" in result.output


def test_show_of_a_plan_in_the_register_prints_the_entry(db: Path) -> None:
    result = runner.invoke(app, ["show", "WPL/UD408"], env=_env(db))

    assert result.exit_code == 0, result.output
    assert "number=UD408" in result.output and "organ=MSWiA" in result.output
    assert "planned=III kwartał 2026 r." in result.output
    assert "responsible: Maciej Duszczyk" in result.output
    assert "istota:" in result.output


def test_show_of_a_plan_nobody_knows_is_a_message_and_not_a_traceback(db: Path) -> None:
    """Every other `show` branch already went through `_or_exit`; the register's did not, so an
    unknown number (or, as here, a register switched off) came out as a traceback."""
    result = runner.invoke(app, ["show", "WPL/UD999"], env=_env(db))

    assert result.exit_code == 1
    assert not isinstance(result.exception, BillNotFoundError)


def test_preview_renders_the_card_into_the_console_without_sending_anything(
    db: Path, api: str
) -> None:
    """`/preview` before `/republish`: the wording can be read without a post. The fixture's
    analysis is not relevant, which the card does not care about — only the publishing rule does."""
    result = runner.invoke(app, ["preview", "3039"], env=_env(db, api=api))

    assert result.exit_code == 0, result.output
    assert "📜 <b>Новый законопроект — druk nr 3039</b>" in result.output
    assert "chars]" in result.output


def test_version_is_printed_without_building_anything(tmp_path: Path) -> None:
    """`--version` is eager: it answers before a container, a database or a term is needed."""
    result = runner.invoke(app, ["--version"], env=_env(tmp_path / "absent.db"))

    assert result.exit_code == 0
    assert result.output.startswith("lexinform ")


def test_a_command_that_does_not_exist_is_refused_before_anything_is_built(
    tmp_path: Path,
) -> None:
    result = runner.invoke(app, ["nonesuch"], env=_env(tmp_path / "absent.db"))

    assert result.exit_code == 2
    assert not (tmp_path / "absent.db").exists()
