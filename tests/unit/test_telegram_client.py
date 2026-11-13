"""The Telegram Bot API client and publisher against a mock transport."""

import json
import traceback
from collections.abc import Callable
from datetime import datetime
from typing import cast

import httpx2 as httpx
import pytest

from lexinform.adapters.llm_prompts import PROMPT_VERSION
from lexinform.adapters.telegram import (
    TelegramAcknowledger,
    TelegramBotClient,
    TelegramEditReceipt,
    TelegramError,
    TelegramOperatorReplier,
    TelegramPublisher,
    TelegramPublishResult,
    TelegramRunNotifier,
)
from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.errors import DeliveryUncertainError, TelegramUnavailableError
from lexinform.models import (
    AnalysisRecord,
    Bill,
    BillStatus,
    CommandOutcome,
    PrintInfo,
    ProcessDetail,
    RunMode,
    RunReport,
)
from tests.fakes import MonotonicClock, channel_post, make_analysis

Handler = Callable[[httpx.Request], httpx.Response]


def _client(handler: Handler, sleeps: list[float] | None = None) -> TelegramBotClient:
    clock = MonotonicClock(sleeps)
    return TelegramBotClient(
        "TOKEN",
        transport=httpx.MockTransport(handler),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )


def _ok(message_id: int) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": {"message_id": message_id}})


@pytest.mark.parametrize("identity", [-100123, 456])
def test_get_chat_resolves_a_username_without_sending(identity: int) -> None:
    calls: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/botTOKEN/getChat"
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"id": identity}})

    assert _client(handler).get_chat_id("@preview") == identity
    assert calls == [{"chat_id": "@preview"}]


@pytest.mark.parametrize("identity", [None, True, False, 0, "-100123", 1.5, {}, []])
def test_get_chat_rejects_an_unverified_identity(identity: object) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/botTOKEN/getChat"
        return httpx.Response(200, json={"ok": True, "result": {"id": identity}})

    with pytest.raises(ValueError, match="no valid chat id; preview was not sent"):
        _client(handler).get_chat_id("@preview")


def test_run_report_is_a_new_message_and_the_started_one_is_deleted() -> None:
    requests: list[tuple[str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.url.path, json.loads(request.content)))
        return _ok(77 if len(requests) == 1 else 90)

    client = _client(handler)
    formatter = MessageFormatter("ru")
    command = channel_post(1, "/run").as_command()
    ack = TelegramAcknowledger(client, channel_id="-1001")
    message_id = ack.queued(command)
    command = command.model_copy(update={"acknowledgement_id": message_id})
    ack.started(command, "run started (no inputs)", url="https://example.org/run")
    report = RunReport(started_at=command.received_at, since=command.received_at, mode=RunMode.RUN)
    TelegramRunNotifier(client, formatter, channel_id="-1001", message_id=message_id).notify(
        report, []
    )

    assert [path for path, _ in requests] == [
        "/botTOKEN/sendMessage",
        "/botTOKEN/editMessageText",
        "/botTOKEN/sendMessage",
        "/botTOKEN/deleteMessage",
    ]
    assert requests[2][1]["text"] == formatter.run_report(report, []).text
    assert requests[3][1] == {"chat_id": "-1001", "message_id": 77}


def test_command_reply_is_a_new_reply_and_the_queued_message_is_deleted() -> None:
    requests: list[tuple[str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.url.path, json.loads(request.content)))
        return _ok(88)

    command = channel_post(1, "/help").as_command().model_copy(update={"acknowledgement_id": 77})
    formatter = MessageFormatter("ru")
    outcome = CommandOutcome(status="help")
    TelegramOperatorReplier(_client(handler), formatter, channel_id="-1001").reply(command, outcome)

    assert [path for path, _ in requests] == ["/botTOKEN/sendMessage", "/botTOKEN/deleteMessage"]
    sent = requests[0][1]
    assert sent["text"] == formatter.command_reply(command, outcome).text
    assert sent["reply_parameters"] == {
        "message_id": command.message_id,
        "allow_sending_without_reply": True,
    }
    assert requests[1][1] == {"chat_id": "-1001", "message_id": 77}


@pytest.mark.parametrize("message_id", [None, 77])
def test_command_reply_survives_an_absent_or_already_deleted_acknowledgement(
    message_id: int | None,
) -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.url.path.rsplit("/", 1)[-1])
        if methods[-1] == "deleteMessage":
            return httpx.Response(
                400,
                json={
                    "ok": False,
                    "error_code": 400,
                    "description": "Bad Request: message to delete not found",
                },
            )
        return _ok(88)

    command = (
        channel_post(1, "/help").as_command().model_copy(update={"acknowledgement_id": message_id})
    )
    TelegramOperatorReplier(_client(handler), MessageFormatter("ru"), channel_id="-1001").reply(
        command, CommandOutcome(status="help")
    )

    assert methods == (["sendMessage", "deleteMessage"] if message_id else ["sendMessage"])


def test_a_reply_that_cannot_be_sent_leaves_the_acknowledgement() -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(
            400, json={"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}
        )

    command = channel_post(1, "/help").as_command().model_copy(update={"acknowledgement_id": 77})
    replier = TelegramOperatorReplier(_client(handler), MessageFormatter("ru"), channel_id="-1001")

    with pytest.raises(TelegramUnavailableError):
        replier.reply(command, CommandOutcome(status="help"))
    assert methods == ["sendMessage"]


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

    assert message_id == 1 and sleeps == pytest.approx([3.1])


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


@pytest.mark.parametrize(
    ("channel", "returned_chat", "expected"),
    [
        ("@reader", {"id": -100123, "username": "reader"}, -100123),
        ("@reader", {"id": -100123}, -100123),
        ("-100123", {"id": -100123}, -100123),
        ("-100123", {"id": -100999}, None),
        ("@reader", None, None),
        ("@reader", "malformed", None),
        ("@reader", {"username": "reader"}, None),
        ("@reader", {"id": "-100123"}, None),
        ("@reader", {"id": True}, None),
        ("@reader", {"id": 0}, None),
        ("@reader", {"id": 2**52}, None),
    ],
)
def test_original_publish_exposes_optional_chat_from_the_same_send_response(
    channel: str,
    returned_chat: object,
    expected: int | None,
) -> None:
    calls: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/botTOKEN/sendMessage"
        calls.append(json.loads(request.content))
        result: dict[str, object] = {"message_id": 12}
        if returned_chat is not None:
            result["chat"] = returned_chat
        return httpx.Response(200, json={"ok": True, "result": result})

    publisher = TelegramPublisher(_client(handler), MessageFormatter("ru"), channel_id=channel)

    sent = publisher.publish_updates("<b>Сводка</b>")

    assert isinstance(sent, TelegramPublishResult)
    assert sent.message_id == 12 and sent.telegram_chat_id == expected
    assert sent.document_message_ids == []
    assert calls == [
        {
            "chat_id": channel,
            "text": "<b>Сводка</b>",
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
    ]


def test_send_message_still_returns_an_integer_with_an_available_chat_receipt() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/botTOKEN/sendMessage"
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {"message_id": 12, "chat": {"id": -100123}},
            },
        )

    result = _client(handler).send_message("@reader", "text")

    assert type(result) is int and result == 12


def test_verified_edit_matches_returned_message_and_pinned_chat() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/botTOKEN/editMessageText"
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "message_id": 12,
                    "chat": {"id": -100123, "username": "reader"},
                    "text": "🙂 x & y",
                },
            },
        )

    receipt = _client(handler).edit_message_verified(-100123, 12, "<b>🙂 x</b> &amp; y")

    assert receipt == TelegramEditReceipt(chat_id=-100123, message_id=12)
    assert requests == [
        {
            "chat_id": -100123,
            "message_id": 12,
            "text": "<b>🙂 x</b> &amp; y",
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
    ]


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (200, {"ok": True, "result": {"message_id": 12, "chat": {"id": -100999}}}),
        (200, {"ok": True, "result": {"message_id": 99, "chat": {"id": -100123}}}),
        (200, {"ok": True, "result": {"message_id": 12, "chat": {"id": "-100123"}}}),
        (200, {"ok": True, "result": {"message_id": "12", "chat": {"id": -100123}}}),
        (200, {"ok": True, "result": {"message_id": 12, "chat": {"id": True}}}),
        (200, {"ok": True, "result": {"message_id": 12}}),
        (200, {"ok": True, "result": True}),
        (200, {"ok": 1, "result": {"message_id": 12, "chat": {"id": -100123}}}),
        (200, b"truncated"),
        (400, {"ok": False, "error_code": 400, "description": "message is not modified"}),
        (400, {"ok": False, "error_code": "400", "description": "Bad Request"}),
        (400, b"unreadable"),
        (408, {"ok": False, "error_code": 408, "description": "Request Timeout"}),
        (502, {"ok": False, "error_code": 502, "description": "Bad Gateway"}),
    ],
)
def test_verified_edit_leaves_ambiguous_responses_unknown_after_one_call(
    status: int,
    body: dict[str, object] | bytes,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return (
            httpx.Response(status, content=body)
            if isinstance(body, bytes)
            else httpx.Response(status, json=body)
        )

    with pytest.raises(DeliveryUncertainError, match="editMessageText"):
        _client(handler).edit_message_verified(-100123, 12, "<b>text</b>")

    assert len(requests) == 1


@pytest.mark.parametrize("error", [httpx.ReadTimeout, httpx.DecodingError])
def test_verified_edit_does_not_retry_an_unreadable_response(
    error: type[httpx.RequestError],
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise error(f"response lost at {request.url}", request=request)

    with pytest.raises(DeliveryUncertainError, match=error.__name__) as raised:
        _client(handler).edit_message_verified(-100123, 12, "text")

    assert len(requests) == 1
    assert "TOKEN" not in "".join(traceback.format_exception(raised.value))


@pytest.mark.parametrize(
    ("status", "exception"), [(400, TelegramError), (429, TelegramUnavailableError)]
)
def test_verified_edit_preserves_definite_rejections_without_retrying(
    status: int,
    exception: type[Exception],
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            status,
            json={
                "ok": False,
                "error_code": status,
                "description": "request rejected",
                "parameters": {"retry_after": 2},
            },
        )

    with pytest.raises(exception, match="request rejected"):
        _client(handler).edit_message_verified(-100123, 12, "text")

    assert len(requests) == 1


@pytest.mark.parametrize(
    ("chat_id", "message_id"), [(cast(int, "@reader"), 12), (0, 12), (True, 12), (-100123, 0)]
)
def test_verified_edit_refuses_unpinned_targets_before_network(
    chat_id: int,
    message_id: int,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Unpinned edits must not reach the transport")

    with pytest.raises(ValueError, match="verified edit requires"):
        _client(handler).edit_message_verified(chat_id, message_id, "text")


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
                            "from": {"id": 123},
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
    assert pressed.actor_id == 123 and pressed.date.timestamp() > 1789200000
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
