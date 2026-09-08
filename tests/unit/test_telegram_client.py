import json

import httpx2 as httpx
import pytest

from lexinform.adapters.telegram import TelegramBotClient, TelegramError, TelegramPublisher
from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import AnalysisRecord, Bill, BillStatus
from tests.fakes import make_analysis


def _client(handler, sleeps: list[float] | None = None) -> TelegramBotClient:  # type: ignore[no-untyped-def]
    record = sleeps if sleeps is not None else []
    return TelegramBotClient("TOKEN", transport=httpx.MockTransport(handler), sleep=record.append)


def test_send_message_posts_html_and_returns_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/botTOKEN/sendMessage"
        body = json.loads(request.content)
        assert body["parse_mode"] == "HTML" and body["chat_id"] == "@chan"
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    assert _client(handler).send_message("@chan", "<b>x</b>") == 7


def test_rate_limit_is_retried_once() -> None:
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
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    assert _client(handler, sleeps).send_message("@chan", "x") == 1
    assert sleeps == [3.0]


def test_api_error_raises() -> None:
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


def test_publisher_sends_card_only(process_3039, print_3039, now) -> None:  # type: ignore[no-untyped-def]
    posted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(posted)}})

    from lexinform.adapters.llm_prompts import PROMPT_VERSION

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
    assert result.message_id == 1 and result.document_message_ids == []
