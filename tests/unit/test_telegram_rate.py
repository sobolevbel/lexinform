from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import httpx2 as httpx
import pytest

from lexinform.adapters.telegram import TelegramBotClient
from lexinform.adapters.telegram_rate import TelegramRateLimiter
from lexinform.errors import DeliveryUncertainError, TelegramUnavailableError
from tests.fakes import MonotonicClock

Handler = Callable[[httpx.Request], httpx.Response]


def client_for(clock: MonotonicClock, handler: Handler) -> TelegramBotClient:
    return TelegramBotClient(
        "TOKEN",
        transport=httpx.MockTransport(handler),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )


def success() -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})


def flood(seconds: object = 42) -> httpx.Response:
    return httpx.Response(
        429,
        json={"ok": False, "error_code": 429, "parameters": {"retry_after": seconds}},
    )


def test_bulk_edits_and_sends_share_the_chat_budget() -> None:
    clock = MonotonicClock()
    times: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        times.append(clock.monotonic())
        return success()

    client = client_for(clock, handler)
    for i in range(30):
        if i % 3 == 0:
            client.send_message("@reader", "card")
        elif i % 3 == 1:
            client.edit_message("@reader", 1, "updated")
        else:
            client.delete_message("@reader", 1)

    assert len(times) == 30
    assert all(b - a >= 3.0 for a, b in zip(times, times[1:], strict=False))
    assert all(sum(start <= t < start + 60 for t in times) <= 20 for start in times)


def test_channels_are_independent_and_control_calls_are_not_paced() -> None:
    clock = MonotonicClock()
    times: list[tuple[str, float]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        times.append((request.url.path.rsplit("/", 1)[-1], clock.monotonic()))
        return success()

    client = client_for(clock, handler)
    client.send_message("@reader", "card")
    client.send_message("@operator", "queued")
    client.answer_callback("callback")
    client.get_updates(offset=1, timeout=0)
    client.edit_message("@reader", 1, "new card")

    assert times[1][1] < 0.1
    assert times[2][1] == times[3][1] == times[1][1]
    assert times[4][1] >= 3


def test_many_chats_still_respect_the_global_budget() -> None:
    clock = MonotonicClock()
    times: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        times.append(clock.monotonic())
        return success()

    client = client_for(clock, handler)
    for i in range(50):
        client.send_message(str(i), "card")

    assert all(sum(start <= t < start + 1 for t in times) <= 20 for start in times)
    assert times[-1] >= 2.4


def test_numeric_id_and_case_insensitive_username_share_budget() -> None:
    clock = MonotonicClock()
    times: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        times.append(clock.monotonic())
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {"message_id": 1, "chat": {"id": -100, "username": "Reader"}},
            },
        )

    client = client_for(clock, handler)
    client.send_message("@Reader", "a")
    client.edit_message("-100", 1, "b")
    client.delete_message("@reader", 1)

    assert times == pytest.approx([0, 3.1, 6.2])


def test_repeated_flood_responses_wait_and_retry_the_same_payload() -> None:
    clock = MonotonicClock()
    requests: list[tuple[float, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((clock.monotonic(), request.content))
        return flood(42 if len(requests) == 1 else 7) if len(requests) < 3 else success()

    client = client_for(clock, handler)
    assert client.send_message("@reader", "card", reply_to=123) == 1
    assert [t for t, _ in requests] == pytest.approx([0, 42.1, 49.2])
    assert len({payload for _, payload in requests}) == 1


def test_flood_on_the_last_connection_attempt_has_its_own_retry_budget() -> None:
    clock = MonotonicClock()
    calls: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(clock.monotonic())
        if len(calls) < 3:
            raise httpx.ConnectError("offline")
        return flood() if len(calls) == 3 else success()

    assert client_for(clock, handler).send_message("@reader", "card") == 1
    assert len(calls) == 4 and calls[-1] - calls[-2] >= 42


def test_exhausted_flood_retries_keep_a_bot_wide_cooldown() -> None:
    clock = MonotonicClock()
    times: list[tuple[str, float]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        times.append((method, clock.monotonic()))
        return flood() if method == "sendMessage" else success()

    client = client_for(clock, handler)
    with pytest.raises(TelegramUnavailableError, match="rate limit persists"):
        client.send_message("@reader", "card")
    client.answer_callback("callback")

    assert len(times) == 5
    assert times[-1][1] - times[-2][1] >= 42


@pytest.mark.parametrize("retry_after", [None, "invalid", -1, True])
def test_unusable_flood_delay_uses_bounded_exponential_backoff(retry_after: object) -> None:
    clock = MonotonicClock()
    calls: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(clock.monotonic())
        return flood(retry_after)

    with pytest.raises(TelegramUnavailableError):
        client_for(clock, handler).send_message("@reader", "card")

    assert calls == pytest.approx([0, 5.1, 15.2, 35.3])


def test_non_json_flood_response_is_also_retried() -> None:
    clock = MonotonicClock()
    calls: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(clock.monotonic())
        return httpx.Response(429, text="Too Many Requests") if len(calls) == 1 else success()

    assert client_for(clock, handler).send_message("@reader", "card") == 1
    assert calls[1] >= 5


@pytest.mark.parametrize("failure", ["read_timeout", "write_error", "502", "html", "missing_id"])
def test_uncertain_send_is_never_automatically_repeated(failure: str) -> None:
    clock = MonotonicClock()
    calls: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.content)
        if failure == "read_timeout":
            raise httpx.ReadTimeout("response lost")
        if failure == "write_error":
            raise httpx.WriteError("partially written")
        if failure == "502":
            return httpx.Response(502)
        if failure == "html":
            return httpx.Response(200, text="<html>gateway</html>")
        return httpx.Response(200, json={"ok": True, "result": {}})

    with pytest.raises(DeliveryUncertainError):
        client_for(clock, handler).send_message("@reader", "card")
    assert len(calls) == 1


@pytest.mark.parametrize("failure", ["read_timeout", "503"])
def test_idempotent_edits_can_retry_after_uncertain_failures(failure: str) -> None:
    clock = MonotonicClock()
    calls: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(clock.monotonic())
        if len(calls) < 3:
            if failure == "read_timeout":
                raise httpx.ReadTimeout("response lost")
            return httpx.Response(503)
        return success()

    client_for(clock, handler).edit_message("@reader", 1, "new card")
    assert len(calls) == 3 and calls[1] - calls[0] >= 3


def test_a_waiting_chat_does_not_lock_other_chats_and_rechecks_new_cooldowns() -> None:
    clock = MonotonicClock()
    waiting = Event()
    resume = Event()

    def sleep(seconds: float) -> None:
        if not waiting.is_set():
            waiting.set()
            assert resume.wait(timeout=5)
        clock.sleep(seconds)

    limiter = TelegramRateLimiter(monotonic=clock.monotonic, sleep=sleep)
    limiter.wait("@reader")
    clock.elapsed = 0.1
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(limiter.wait, "@reader")
        try:
            assert waiting.wait(timeout=5)
            limiter.wait("@operator")
            limiter.wait(None)
            limiter.defer(42)
        finally:
            resume.set()
        future.result(timeout=5)

    assert clock.monotonic() >= 42.2
    assert len(clock.sleeps) == 2
