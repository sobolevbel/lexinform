"""Outages of external systems must end a phase with a clear error, never crash the run."""

from collections.abc import Iterator
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
from lexinform.models import BillStatus, ProcessSummary, PublicationStatus
from tests.unit.test_pipeline import CHANNEL, World


class _DownGateway:
    """Wraps the fake gateway and makes the listed methods raise SejmApiUnavailableError."""

    def __init__(self, inner, down: set[str]) -> None:  # type: ignore[no-untyped-def]
        self._inner = inner
        self._down = down

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        if name in self._down:

            def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
                raise SejmApiUnavailableError("GET /processes: ConnectError after 4 attempts")

            return boom
        return getattr(self._inner, name)

    def iter_processes(self, *a, **kw) -> Iterator[ProcessSummary]:  # type: ignore[no-untyped-def]
        if "iter_processes" in self._down:
            raise SejmApiUnavailableError("GET /processes: ConnectError after 4 attempts")
        return self._inner.iter_processes(*a, **kw)


def test_sejm_api_down_during_discovery_is_reported_not_raised() -> None:
    w = World()
    w.pipeline._discovery._gateway = _DownGateway(w.gateway, {"iter_processes"})  # type: ignore[attr-defined]
    report = w.run()
    assert not report.ok  # type: ignore[attr-defined]
    assert report.errors == [
        "discovery: Sejm API unavailable: GET /processes: ConnectError after 4 attempts"
    ]  # type: ignore[attr-defined]
    assert w.notifier.calls[-1][0] is report


def test_sejm_api_down_during_analysis_keeps_attempts() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.pipeline._analysis._gateway = _DownGateway(w.gateway, {"get_process"})  # type: ignore[attr-defined]
    report = w.run()
    assert any("analysis: Sejm API unavailable" in e for e in report.errors)  # type: ignore[attr-defined]
    bill = w.repo.get(10, "3039")
    assert (
        bill is not None
        and bill.analysis_attempts == 0
        and bill.status is BillStatus.ANALYSIS_PENDING
    )
    assert w.llm.contexts == []


def test_llm_down_stops_phase_without_consuming_attempts() -> None:
    w = World(
        llm_script={
            "3039": LlmUnavailableError("RateLimitError: 429"),
            "3040": LlmUnavailableError("x"),
        }
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.add_bill("3040", "Projekt ustawy o obywatelstwie polskim")
    report = w.run()
    assert report.errors == ["analysis: LLM API unavailable: RateLimitError: 429"]  # type: ignore[attr-defined]
    assert all(w.repo.get(10, n).analysis_attempts == 0 for n in ("3039", "3040"))  # type: ignore[union-attr]
    assert len(w.llm.contexts) == 1  # stopped after the first outage


def test_telegram_down_marks_failed_and_stops_publishing() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.add_bill("3040", "Projekt ustawy o obywatelstwie polskim")

    def down(bill, print_info):  # type: ignore[no-untyped-def]
        raise TelegramUnavailableError("sendMessage: ConnectError after 3 attempts")

    w.publisher.publish_new_bill = down  # type: ignore[method-assign]
    report = w.run()
    assert report.errors == [
        "publishing: Telegram API unavailable: sendMessage: ConnectError after 3 attempts"
    ]  # type: ignore[attr-defined]
    statuses = {
        n: (p.status if (p := w.repo.get_publication(10, n, "new_bill", CHANNEL)) else None)
        for n in ("3039", "3040")
    }
    assert PublicationStatus.FAILED in statuses.values()
    assert None in statuses.values()  # the second bill was not even attempted

    # Telegram is back: both get published, nothing duplicated
    del w.publisher.publish_new_bill  # type: ignore[misc]
    assert w.run().published == 2  # type: ignore[attr-defined]
    assert len(w.publisher.new_bills) == 2


def test_unexpected_bug_in_a_phase_is_reported() -> None:
    w = World()

    def bug(term: int, since: datetime, **kwargs: object):  # type: ignore[no-untyped-def]
        raise KeyError("oops")

    w.pipeline._discovery.discover = bug  # type: ignore[method-assign]
    report = w.run()
    assert report.errors == ["discovery failed: KeyError: 'oops'"]  # type: ignore[attr-defined]


def test_sejm_client_raises_unavailable_after_retries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = SejmApiClient(
        "https://api.test", transport=httpx.MockTransport(handler), sleep=lambda s: None
    )
    with pytest.raises(SejmApiUnavailableError):
        client.get_process(10, "1")

    def five_hundred(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502)

    client = SejmApiClient(
        "https://api.test", transport=httpx.MockTransport(five_hundred), sleep=lambda s: None
    )
    with pytest.raises(SejmApiUnavailableError):
        client.get_process(10, "1")


def test_telegram_client_retries_transport_errors_then_gives_up() -> None:
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 5}})

    client = TelegramBotClient("T", transport=httpx.MockTransport(flaky), sleep=lambda s: None)
    assert client.send_message("@c", "x") == 5 and calls["n"] == 3

    def dead(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with pytest.raises(TelegramUnavailableError):
        TelegramBotClient(
            "T", transport=httpx.MockTransport(dead), sleep=lambda s: None
        ).send_message("@c", "x")

    def unauthorized(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"ok": False, "error_code": 401, "description": "Unauthorized"}
        )

    with pytest.raises(TelegramUnavailableError):
        TelegramBotClient("T", transport=httpx.MockTransport(unauthorized)).send_message("@c", "x")


def test_bot_not_admin_is_an_outage() -> None:
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
        TelegramBotClient("T", transport=httpx.MockTransport(forbidden)).send_message("@c", "x")
