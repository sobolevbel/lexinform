"""The Telegram Bot API client and publisher against a mock transport."""

import json
from collections.abc import Callable
from datetime import datetime

import httpx2 as httpx
import pytest

from lexinform.adapters.llm_prompts import PROMPT_VERSION
from lexinform.adapters.telegram import TelegramBotClient, TelegramError, TelegramPublisher
from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.errors import TelegramUnavailableError
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


def test_a_button_carries_its_label_and_callback_data_under_the_message() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return _ok(7)

    _client(handler).send_message("@log", "draft", button=("Publish", "digest:2026-W38"))

    assert bodies[0]["reply_markup"] == {
        "inline_keyboard": [[{"text": "Publish", "callback_data": "digest:2026-W38"}]]
    }


def test_a_pressed_button_comes_back_as_the_command_it_stands_for() -> None:
    """The press takes the command's road: `chat_id` and `message_id` are the draft it hangs on,
    so the run answers there, and `callback_id` is what stops the button spinning."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": [
                    {
                        "update_id": 20,
                        "callback_query": {
                            "id": "4382",
                            "data": "digest:2026-W38",
                            "message": {
                                "message_id": 42,
                                "date": 1789200000,
                                "chat": {"id": -1001, "type": "channel", "username": "lexlog"},
                            },
                        },
                    },
                    {"update_id": 21, "callback_query": {"id": "9", "data": "delete:everything"}},
                ],
            },
        )

    posts = _client(handler).get_updates(offset=None, timeout=0)

    pressed = posts[0]
    assert pressed.text == "/digest publish ref=2026-W38"
    assert (pressed.chat_id, pressed.message_id, pressed.callback_id) == (-1001, 42, "4382")
    assert posts[1].text is None  # data no button of ours sent; the offset still moves past it


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
                            "from": {"id": 123},
                            "author_signature": "Operator",
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

    assert bodies[0] == {
        "timeout": 50,
        "allowed_updates": ["channel_post", "callback_query"],
        "offset": 7,
    }
    assert [p.update_id for p in posts] == [10, 11, 12]  # every update moves the offset
    first = posts[0]
    assert (first.chat_id, first.chat_username, first.message_id) == (-1001, "lexlog", 42)
    assert first.text == "/analyze 3039" and first.date.isoformat() == "2026-09-12T08:00:00+00:00"
    assert first.as_command().actor_id == 123
    assert first.as_command().author_signature == "Operator"
    assert posts[1].chat_id == 0 and posts[1].text is None  # a placeholder from no chat
    assert posts[2].text is None  # a post without text (a photo)


def test_a_second_getupdates_consumer_ends_the_phase_instead_of_raising_per_call() -> None:
    """409 "terminated by other getUpdates request" is a second relay or a webhook holding the
    same bot: every poll fails while it lives. As a per-call error it reached `run_forever`'s
    catch-all and printed a traceback once a cycle; as an outage the relay backs off."""

    def conflict(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={
                "ok": False,
                "error_code": 409,
                "description": "Conflict: terminated by other getUpdates request",
            },
        )

    with pytest.raises(TelegramUnavailableError):
        _client(conflict).get_updates(offset=None, timeout=0)


def test_an_answer_that_is_not_json_is_an_error_naming_the_status() -> None:
    with pytest.raises(TelegramError):
        _client(lambda _: httpx.Response(404, text="<html>nginx</html>")).get_updates(
            offset=None, timeout=0
        )
