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
from tests.fakes import FakePublisher, FakeSejmGateway, FixedClock, MonotonicClock


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


def test_legacy_batch_switch_cannot_enable_synchronous_analysis() -> None:
    on = _container(batching=True)
    off = _container(batching=False)

    assert on.analysis_options().submit_batches is True
    assert off.analysis_options().submit_batches is False
    assert on.analysis_options().require_batch and off.analysis_options().require_batch


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


@pytest.mark.parametrize(
    ("reader", "target", "identities"),
    [
        ("-100123", " -00100123 ", {}),
        ("@Readers", "@readers", {}),
        ("-100123", "@readers", {"@readers": -100123}),
        ("@readers", "-100123", {"@readers": -100123}),
        ("@readers", "@alias", {"@readers": -100123, "@alias": -100123}),
    ],
)
def test_preview_factory_refuses_reader_aliases_before_any_send(
    monkeypatch: pytest.MonkeyPatch, reader: str, target: str, identities: dict[str, int]
) -> None:
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/getChat"), "a refused preview never sends a message"
        address = str(json.loads(request.content)["chat_id"])
        calls.append(address)
        return httpx.Response(200, json={"ok": True, "result": {"id": identities[address]}})

    c = _container(batching=False)
    c.settings.telegram_channel_id = reader
    clock = MonotonicClock()
    client = TelegramBotClient(
        "TOKEN", transport=httpx.MockTransport(handle), sleep=clock.sleep, monotonic=clock.monotonic
    )
    monkeypatch.setattr(c, "telegram_client", lambda: client)

    with pytest.raises(ValueError, match="Preview cannot be sent to the reader channel"):
        c.preview_publisher(target, dry_run=False)

    assert set(calls) == set(identities)


@pytest.mark.parametrize("reader", ["", "@unresolved"])
def test_preview_factory_fails_closed_without_a_verified_reader(
    monkeypatch: pytest.MonkeyPatch, reader: str
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/getChat"), "an unknown reader prevents sending"
        return httpx.Response(200, json={"ok": True, "result": {"title": "no identity"}})

    c = _container(batching=False)
    c.settings.telegram_channel_id = reader
    client = TelegramBotClient("TOKEN", transport=httpx.MockTransport(handle))
    monkeypatch.setattr(c, "telegram_client", lambda: client)

    with pytest.raises(ValueError, match="Cannot verify preview|no valid chat id"):
        c.preview_publisher("-100999", dry_run=False)


@pytest.mark.parametrize("unavailable", [False, True])
def test_preview_factory_sends_nothing_when_chat_resolution_fails(
    monkeypatch: pytest.MonkeyPatch, unavailable: bool
) -> None:
    methods: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        methods.append(request.url.path.rsplit("/", 1)[-1])
        assert methods[-1] == "getChat", "resolution failure never reaches sendMessage"
        if unavailable:
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(
            400, json={"ok": False, "error_code": 400, "description": "chat not found"}
        )

    c = _container(batching=False)
    c.settings.telegram_channel_id = "@readers"
    clock = MonotonicClock()
    client = TelegramBotClient(
        "TOKEN", transport=httpx.MockTransport(handle), sleep=clock.sleep, monotonic=clock.monotonic
    )
    monkeypatch.setattr(c, "telegram_client", lambda: client)

    with pytest.raises(RuntimeError, match="chat not found|ConnectError"):
        c.preview_publisher("-100999", dry_run=False)

    assert len(methods) == (3 if unavailable else 1)


@pytest.mark.parametrize("override", [False, True])
def test_preview_factory_keeps_dry_runs_and_fake_overrides_off_telegram(
    monkeypatch: pytest.MonkeyPatch, override: bool
) -> None:
    c = _container(batching=False)
    c.settings.telegram_channel_id = "@readers"
    fake = FakePublisher()
    if override:
        c.publisher_override = fake

    def forbidden() -> TelegramBotClient:
        raise AssertionError("dry runs and fake publishers never resolve or send Telegram chats")

    monkeypatch.setattr(c, "telegram_client", forbidden)

    publisher = c.preview_publisher("@separate_test_chat", dry_run=not override)

    assert publisher is (fake if override else c.publisher(dry_run=True))
    with pytest.raises(ValueError, match="reader channel"):
        c.preview_publisher("@READERS", dry_run=not override)
