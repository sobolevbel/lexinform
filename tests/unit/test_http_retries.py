"""What the HTTP clients do with a host that is refusing or broken: retry what is worth retrying,
and then raise the phase-fatal `…UnavailableError` rather than a transport error nobody catches."""

from collections.abc import Callable

import httpx2 as httpx
import pytest

from lexinform.adapters.sejm_api import SejmApiClient
from lexinform.adapters.telegram import TelegramBotClient
from lexinform.errors import SejmApiUnavailableError, TelegramUnavailableError

Handler = Callable[[httpx.Request], httpx.Response]


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
