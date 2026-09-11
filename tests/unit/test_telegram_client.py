"""The Telegram Bot API client and publisher against a mock transport."""

import json
from collections.abc import Callable
from datetime import datetime

import httpx2 as httpx
import pytest

from lexinform.adapters.llm_prompts import PROMPT_VERSION
from lexinform.adapters.telegram import TelegramBotClient, TelegramError, TelegramPublisher
from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import AnalysisRecord, Bill, BillStatus, PrintInfo, ProcessDetail
from tests.fakes import make_analysis

Handler = Callable[[httpx.Request], httpx.Response]


def _client(handler: Handler, sleeps: list[float] | None = None) -> TelegramBotClient:
    record = sleeps if sleeps is not None else []
    return TelegramBotClient("TOKEN", transport=httpx.MockTransport(handler), sleep=record.append)


def _ok(message_id: int) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": {"message_id": message_id}})


def test_send_message_posts_html_to_the_chat_and_returns_the_message_id() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/botTOKEN/sendMessage"
        bodies.append(json.loads(request.content))
        return _ok(7)

    message_id = _client(handler).send_message("@chan", "<b>x</b>", reply_to=3)

    assert message_id == 7
    assert bodies[0]["parse_mode"] == "HTML" and bodies[0]["chat_id"] == "@chan"
    assert bodies[0]["reply_parameters"] == {"message_id": 3, "allow_sending_without_reply": True}


def test_rate_limit_is_waited_out_once() -> None:
    calls = {"n": 0}
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                429,
                json={
                    "ok": False,
                    "error_code": 429,
                    "description": "Too Many Requests",
                    "parameters": {"retry_after": 3},
                },
            )
        return _ok(1)

    message_id = _client(handler, sleeps).send_message("@chan", "x")

    assert (message_id, sleeps) == (1, [3.0])


def test_edit_message_replaces_the_text_and_tolerates_an_unchanged_one() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/botTOKEN/editMessageText"
        bodies.append(json.loads(request.content))
        if len(bodies) == 2:
            return httpx.Response(
                400,
                json={
                    "ok": False,
                    "error_code": 400,
                    "description": "Bad Request: message is not modified: ...",
                },
            )
        return _ok(12)

    client = _client(handler)
    client.edit_message("@chan", 12, "<b>x</b> #tag")
    client.edit_message("@chan", 12, "<b>x</b> #tag")  # same text again: not an error

    assert bodies[0]["message_id"] == 12 and bodies[0]["text"] == "<b>x</b> #tag"
    assert bodies[0]["parse_mode"] == "HTML" and len(bodies) == 2


def test_api_error_carries_the_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "ok": False,
                "error_code": 400,
                "description": "Bad Request: can't parse entities",
            },
        )

    with pytest.raises(TelegramError) as exc:
        _client(handler).send_message("@chan", "<b>")
    assert exc.value.code == 400


def test_publisher_sends_the_card_as_one_message(
    process_3039: ProcessDetail, print_3039: PrintInfo, now: datetime
) -> None:
    posted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(request.url.path.rsplit("/", 1)[-1])
        return _ok(len(posted))

    bill = Bill(
        summary=process_3039,
        status=BillStatus.ANALYZED,
        stages=process_3039.stages,
        analysis=AnalysisRecord(
            analysis=make_analysis(),
            model="m",
            prompt_version=PROMPT_VERSION,
            input_chars=1,
            truncated=False,
            text_source="pdf",
            created_at=now,
        ),
        first_seen_at=now,
        last_checked_at=now,
    )
    publisher = TelegramPublisher(_client(handler), MessageFormatter("ru"), channel_id="@chan")

    result = publisher.publish_new_bill(bill, print_3039)

    assert posted == ["sendMessage"]
    assert (result.message_id, result.document_message_ids) == (1, [])


def test_get_updates_long_polls_for_channel_posts_and_parses_them() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/botTOKEN/getUpdates"
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": [
                    {
                        "update_id": 10,
                        "channel_post": {
                            "message_id": 42,
                            "date": 1789200000,
                            "chat": {"id": -1001, "type": "channel", "username": "lexlog"},
                            "text": "/analyze 3039",
                        },
                    },
                    {"update_id": 11, "edited_channel_post": {"message_id": 1}},  # another kind
                    {
                        "update_id": 12,
                        "channel_post": {"message_id": 43, "date": 1, "chat": {"id": -1001}},
                    },
                ],
            },
        )

    posts = _client(handler).get_updates(offset=7, timeout=50)

    assert bodies[0] == {"timeout": 50, "allowed_updates": ["channel_post"], "offset": 7}
    assert [p.update_id for p in posts] == [10, 11, 12]  # every update moves the offset
    first = posts[0]
    assert (first.chat_id, first.chat_username, first.message_id) == (-1001, "lexlog", 42)
    assert first.text == "/analyze 3039" and first.date.isoformat() == "2026-09-12T08:00:00+00:00"
    assert posts[1].chat_id == 0 and posts[1].text is None  # a placeholder from no chat
    assert posts[2].text is None  # a post without text (a photo)
