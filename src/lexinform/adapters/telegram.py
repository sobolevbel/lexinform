"""Thin Telegram Bot API client and the Publisher built on it."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx2 as httpx

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.errors import TelegramUnavailableError
from lexinform.models import AgendaItem, Bill, PrintInfo, RunReport, StatusChange

log = logging.getLogger(__name__)


class TelegramError(RuntimeError):
    def __init__(self, code: int, description: str) -> None:
        super().__init__(f"Telegram API error {code}: {description}")
        self.code = code
        self.description = description


class TelegramBotClient:
    """`sendMessage` with retries; auth and membership problems are `TelegramUnavailableError`."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str = "https://api.telegram.org",
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not token:
            raise ValueError("Telegram bot token is empty")
        self._client = httpx.Client(
            base_url=f"{base_url.rstrip('/')}/bot{token}", timeout=timeout, transport=transport
        )
        self._sleep = sleep

    def send_message(self, chat_id: str, html: str, *, reply_to: int | None = None) -> int:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": html,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        if reply_to is not None:
            payload["reply_parameters"] = {
                "message_id": reply_to,
                "allow_sending_without_reply": True,
            }
        result = self._call("sendMessage", json=payload)
        return int(result["message_id"])

    def close(self) -> None:
        self._client.close()

    MAX_ATTEMPTS = 3

    def _call(self, method: str, **kwargs: Any) -> dict[str, Any]:
        rate_limited_once = False
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                response = self._client.post(f"/{method}", **kwargs)
            except httpx.TransportError as exc:
                if attempt == self.MAX_ATTEMPTS:
                    raise TelegramUnavailableError(
                        f"{method}: {type(exc).__name__} after {attempt} attempts"
                    ) from exc
                log.warning("Telegram %s: %s, retry %d", method, type(exc).__name__, attempt)
                self._sleep(2.0 * attempt)
                continue
            if response.status_code >= 500:
                if attempt == self.MAX_ATTEMPTS:
                    raise TelegramUnavailableError(f"{method}: HTTP {response.status_code}")
                self._sleep(2.0 * attempt)
                continue
            try:
                body = response.json()
            except ValueError as exc:
                raise TelegramError(response.status_code, response.text[:200]) from exc
            if body.get("ok"):
                result = body.get("result")
                return result if isinstance(result, dict) else {"result": result}
            code = int(body.get("error_code", response.status_code))
            description = str(body.get("description", ""))
            retry_after = (body.get("parameters") or {}).get("retry_after")
            if code == 429 and not rate_limited_once and retry_after is not None:
                rate_limited_once = True
                log.warning("Telegram rate limit, sleeping %ss", retry_after)
                self._sleep(float(retry_after))
                continue
            if code in (401, 403) or "chat not found" in description.lower():
                # Wrong token, bot not an admin, wrong channel id: affects every message.
                raise TelegramUnavailableError(f"HTTP {code}: {description}")
            raise TelegramError(code, description)
        raise TelegramUnavailableError(f"{method}: gave up after {self.MAX_ATTEMPTS} attempts")


@dataclass
class TelegramPublishResult:
    message_id: int
    document_message_ids: list[int] = field(default_factory=list)  # none: the PDF is linked


class TelegramPublisher:
    """Renders each message kind with the formatter and sends it to the channel."""

    def __init__(
        self,
        client: TelegramBotClient,
        formatter: MessageFormatter,
        *,
        channel_id: str,
    ) -> None:
        self._client = client
        self._formatter = formatter
        self._channel_id = channel_id

    def publish_new_bill(self, bill: Bill, print_info: PrintInfo | None) -> TelegramPublishResult:
        rendered = self._formatter.new_bill(bill, print_info)
        message_id = self._client.send_message(self._channel_id, rendered.text)
        return TelegramPublishResult(message_id=message_id)

    def publish_status_update(
        self, bill: Bill, change: StatusChange, reply_to: int | None
    ) -> TelegramPublishResult:
        rendered = self._formatter.status_update(bill, change)
        message_id = self._client.send_message(self._channel_id, rendered.text, reply_to=reply_to)
        return TelegramPublishResult(message_id=message_id)

    def publish_act_published(self, bill: Bill, reply_to: int | None) -> TelegramPublishResult:
        rendered = self._formatter.act_published(bill)
        message_id = self._client.send_message(self._channel_id, rendered.text, reply_to=reply_to)
        return TelegramPublishResult(message_id=message_id)

    def publish_in_force(self, bill: Bill, reply_to: int | None) -> TelegramPublishResult:
        rendered = self._formatter.in_force(bill)
        message_id = self._client.send_message(self._channel_id, rendered.text, reply_to=reply_to)
        return TelegramPublishResult(message_id=message_id)

    def publish_consultation_deadline(
        self, bill: Bill, reply_to: int | None, *, today: date
    ) -> TelegramPublishResult:
        rendered = self._formatter.consultation_deadline(bill, today=today)
        message_id = self._client.send_message(self._channel_id, rendered.text, reply_to=reply_to)
        return TelegramPublishResult(message_id=message_id)

    def publish_consultation_results(
        self, bill: Bill, reply_to: int | None
    ) -> TelegramPublishResult:
        rendered = self._formatter.consultation_results(bill)
        message_id = self._client.send_message(self._channel_id, rendered.text, reply_to=reply_to)
        return TelegramPublishResult(message_id=message_id)

    def publish_agenda(
        self, bill: Bill, item: AgendaItem, reply_to: int | None
    ) -> TelegramPublishResult:
        rendered = self._formatter.agenda(bill, item)
        message_id = self._client.send_message(self._channel_id, rendered.text, reply_to=reply_to)
        return TelegramPublishResult(message_id=message_id)


class TelegramRunNotifier:
    """Posts the run report and captured warnings to a technical log channel."""

    def __init__(
        self, client: TelegramBotClient, formatter: MessageFormatter, *, channel_id: str
    ) -> None:
        self._client = client
        self._formatter = formatter
        self._channel_id = channel_id

    def notify(self, report: RunReport, log_lines: list[str]) -> None:
        rendered = self._formatter.run_report(report, log_lines)
        self._client.send_message(self._channel_id, rendered.text)
