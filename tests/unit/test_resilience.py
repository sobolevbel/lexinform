"""Outages of external systems end a phase with a clear error and never crash the run."""

from collections.abc import Callable
from datetime import datetime

import httpx2 as httpx
import pytest

from lexinform.adapters.sejm_api import SejmApiClient
from lexinform.adapters.telegram import TelegramBotClient
from lexinform.errors import (
    LlmUnavailableError,
    SejmApiUnavailableError,
    TelegramUnavailableError,
)
from lexinform.models import BillStatus, PublicationStatus
from tests.harness import World

Handler = Callable[[httpx.Request], httpx.Response]

# --------------------------------------------------------------------------- phases


def test_sejm_api_down_during_discovery_is_reported_not_raised() -> None:
    w = World()
    w.gateway.outages.add("iter_processes")

    report = w.run()

    assert not report.ok
    assert report.errors == ["discovery: Sejm API unavailable: iter_processes: connection refused"]
    assert w.notifier.calls[-1][0] is report


def test_sejm_api_down_during_analysis_keeps_the_attempts() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.gateway.outages.add("get_process")

    report = w.run()

    assert any("analysis: Sejm API unavailable" in e for e in report.errors)
    bill = w.bill("3039")
    assert (bill.analysis_attempts, bill.status) == (0, BillStatus.ANALYSIS_PENDING)
    assert w.llm.contexts == []


def test_llm_down_stops_the_phase_without_consuming_attempts() -> None:
    w = World(
        llm_script={
            "3039": LlmUnavailableError("RateLimitError: 429"),
            "3040": LlmUnavailableError("x"),
        }
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.add_bill("3040", "Projekt ustawy o obywatelstwie polskim")

    report = w.run()

    assert report.errors == ["analysis: LLM API unavailable: RateLimitError: 429"]
    assert [w.bill(n).analysis_attempts for n in ("3039", "3040")] == [0, 0]
    assert len(w.llm.contexts) == 1  # stopped after the first outage


def test_telegram_down_marks_the_post_failed_and_stops_publishing() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.add_bill("3040", "Projekt ustawy o obywatelstwie polskim")
    w.publisher.outage_on = {"3039", "3040"}

    report = w.run()

    assert report.errors == [
        "publishing: Telegram API unavailable: sendMessage: ConnectError after 3 attempts"
    ]
    first = w.publication("3039")
    assert first is not None and first.status is PublicationStatus.FAILED
    assert w.publication("3040") is None  # not even attempted


def test_publishing_resumes_without_duplicates_when_telegram_is_back() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.add_bill("3040", "Projekt ustawy o obywatelstwie polskim")
    w.publisher.outage_on = {"3039", "3040"}
    w.run()
    w.publisher.outage_on = set()

    report = w.run()

    assert report.published == 2
    assert len(w.publisher.new_bills) == 2


def test_unexpected_bug_in_a_phase_is_reported() -> None:
    w = World()

    def bug(term: int, since: datetime, *, pre_print: bool = True) -> None:
        raise KeyError("oops")

    w.discovery.discover = bug  # type: ignore[method-assign, assignment]

    report = w.run()

    assert report.errors == ["discovery failed: KeyError: 'oops'"]


# --------------------------------------------------------------------------- clients


def _sejm(handler: Handler) -> SejmApiClient:
    return SejmApiClient(
        "https://api.test", transport=httpx.MockTransport(handler), sleep=lambda s: None
    )


def _telegram(handler: Handler) -> TelegramBotClient:
    return TelegramBotClient("T", transport=httpx.MockTransport(handler), sleep=lambda s: None)


def test_sejm_client_raises_unavailable_after_connection_errors() -> None:
    def dead(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with pytest.raises(SejmApiUnavailableError):
        _sejm(dead).get_process(10, "1")


def test_sejm_client_raises_unavailable_after_server_errors() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502)

    with pytest.raises(SejmApiUnavailableError):
        _sejm(broken).get_process(10, "1")


def test_telegram_client_retries_transport_errors_then_succeeds() -> None:
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 5}})

    message_id = _telegram(flaky).send_message("@c", "x")

    assert (message_id, calls["n"]) == (5, 3)


def test_telegram_client_gives_up_on_a_dead_connection() -> None:
    def dead(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with pytest.raises(TelegramUnavailableError):
        _telegram(dead).send_message("@c", "x")


def test_wrong_token_is_an_outage() -> None:
    def unauthorized(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"ok": False, "error_code": 401, "description": "Unauthorized"}
        )

    with pytest.raises(TelegramUnavailableError):
        _telegram(unauthorized).send_message("@c", "x")


def test_bot_not_in_the_channel_is_an_outage() -> None:
    def forbidden(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "ok": False,
                "error_code": 403,
                "description": "Forbidden: bot is not a member of the channel chat",
            },
        )

    with pytest.raises(TelegramUnavailableError, match="not a member"):
        _telegram(forbidden).send_message("@c", "x")
