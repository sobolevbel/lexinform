"""The wiring decisions a test cannot see through a service: what the container builds and when."""

import datetime as dt
import json

import httpx2 as httpx
import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.adapters.telegram import TelegramBotClient
from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.container import Container
from lexinform.keywords import KeywordPrefilter
from lexinform.models import RunMode, RunReport
from lexinform.ports import Clock
from lexinform.services.terms import TermResolver
from lexinform.settings import Settings
from tests.fakes import FakeSejmGateway, FixedClock


def _container(*, batching: bool) -> Container:
    repo = SqliteBillRepository(":memory:")
    repo.migrate()
    gateway = FakeSejmGateway()
    clock: Clock = FixedClock()
    return Container(
        settings=Settings(_env_file=None, term=10, llm_batch_enabled=batching),
        clock=clock,
        repo=repo,
        gateway=gateway,
        formatter=MessageFormatter("ru"),
        prefilter=KeywordPrefilter(),
        terms=TermResolver(gateway, repo),
    )


def test_the_batch_backend_is_built_even_with_batching_switched_off() -> None:
    """Switching it off mid-flight would otherwise strand the filed requests, and their bills."""
    off = _container(batching=False)

    assert off.batch_backend() is not None
    assert off.analysis_service() is not None


def test_batching_off_is_what_stops_a_submission() -> None:
    on = _container(batching=True)
    off = _container(batching=False)

    assert on.analysis_options().submit_batches is True
    assert off.analysis_options().submit_batches is False


def test_secondary_batch_options_keep_each_call_model_and_selected_kinds() -> None:
    c = _container(batching=True)
    c.settings.llm_batch_kinds = frozenset({"joint", "supplement"})
    c.settings.llm_joint_model = "gpt-5.1"
    c.settings.llm_supplement_model = "claude-sonnet-5"
    options = c.analysis_options()
    assert options.batch_kinds == frozenset({"joint", "supplement"})
    assert options.batch_models["joint"] == "gpt-5.1"
    assert options.batch_models["supplement"] == "claude-sonnet-5"
    assert options.batch_max_wait.total_seconds() == 6 * 3600


def test_run_notifier_links_the_actions_run_in_the_log_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages: list[dict[str, object]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        messages.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    container = _container(batching=False)
    container.settings.telegram_log_channel_id = "@logs"
    client = TelegramBotClient("TOKEN", transport=httpx.MockTransport(handle))
    monkeypatch.setattr(container, "telegram_client", lambda: client)
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    notifier = container.run_notifier(dry_run=False)
    report = RunReport(
        started_at=dt.datetime(2026, 9, 24),
        since=dt.datetime(2026, 9, 24),
        mode=RunMode.RUN,
    )

    assert notifier is not None
    notifier.notify(report, [])

    assert messages[0]["chat_id"] == "@logs"
    assert '<a href="https://github.com/owner/repo/actions/runs/123">GitHub Actions log</a>' in str(
        messages[0]["text"]
    )
