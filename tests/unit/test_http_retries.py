"""What the HTTP clients do with a host that is refusing or broken: retry what is worth retrying,
and then raise the phase-fatal `…UnavailableError` rather than a transport error nobody catches."""

from collections.abc import Callable

import httpx2 as httpx
import pytest

from lexinform.adapters.orka import OrkaClient
from lexinform.adapters.rcl_html import RclClient, RclPageError
from lexinform.adapters.sejm_api import SejmApiClient, SejmApiError
from lexinform.adapters.senat_html import SenateClient
from lexinform.adapters.telegram import TelegramBotClient
from lexinform.adapters.wykaz_csv import REGISTER_PATH, WykazClient
from lexinform.errors import (
    OrkaUnreachableError,
    RclUnavailableError,
    SejmApiUnavailableError,
    SenateUnavailableError,
    TelegramUnavailableError,
    WykazUnavailableError,
)

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.mark.parametrize("source", ["sejm", "rcl", "senate", "wykaz", "orka"])
@pytest.mark.parametrize("failure", ["transport", "429", "500", "404"])
@pytest.mark.parametrize("retries", [0, 2])
def test_source_retry_contract(source: str, failure: str, retries: int) -> None:
    calls: list[str] = []
    responses: list[httpx.Response] = []
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if source == "wykaz" and request.url.path == REGISTER_PATH:
            return httpx.Response(200, text="register")
        calls.append(request.url.path)
        if failure == "transport":
            raise httpx.ConnectError("offline")
        response = httpx.Response(int(failure), content=b"unavailable")
        responses.append(response)
        return response

    transport = httpx.MockTransport(handler)
    read: Callable[[], object]
    close: Callable[[], None]
    error: type[RuntimeError]
    if source == "sejm":
        sejm = SejmApiClient(transport=transport, max_retries=retries, sleep=delays.append)
        read, close = lambda: sejm.get_process(10, "1"), sejm.close
        error = SejmApiError if failure == "404" else SejmApiUnavailableError
    elif source == "rcl":
        rcl = RclClient(transport=transport, max_retries=retries, sleep=delays.append)
        read, close = lambda: rcl.get_project(1), rcl.close
        error = RclPageError if failure == "404" else RclUnavailableError
    elif source == "senate":
        senate = SenateClient(transport=transport, max_retries=retries, sleep=delays.append)
        read, close = lambda: senate.read_act("https://example.test/act"), senate.close
        error = SenateUnavailableError
    elif source == "wykaz":
        wykaz = WykazClient(transport=transport, max_retries=retries, sleep=delays.append)
        read, close = wykaz.entries, wykaz.close
        error = WykazUnavailableError
    else:
        orka = OrkaClient(transport=transport, max_retries=retries, sleep=delays.append)
        read, close = lambda: orka.download("https://example.test/file"), orka.close
        error = OrkaUnreachableError
    try:
        with pytest.raises(error):
            read()
        repeated = 0 if failure == "404" else retries
        assert len(calls) == repeated + 1
        assert delays == ([1.0, 2.0] if repeated else [])
        assert all(response.is_closed for response in responses)
    finally:
        close()


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
