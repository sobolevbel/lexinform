"""Thin Telegram Bot API client and the Publisher built on it."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx2 as httpx

from lexinform.adapters.publisher_base import Outgoing, RenderingPublisher
from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.errors import TelegramUnavailableError
from lexinform.models import (
    BackfillReport,
    ChannelPost,
    CommandOutcome,
    IncomingCommand,
    RunReport,
    command_for_callback,
)

log = logging.getLogger(__name__)
_EPOCH = datetime.fromtimestamp(0, tz=UTC)


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

    def send_message(
        self,
        chat_id: str,
        html: str,
        *,
        reply_to: int | None = None,
        button: tuple[str, str] | None = None,
    ) -> int:
        """`button` is (label, callback data): one inline button under the message, which comes
        back as a `callback_query` when it is pressed."""
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": html,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        if button is not None:
            payload["reply_markup"] = {
                "inline_keyboard": [[{"text": button[0], "callback_data": button[1]}]]
            }
        if reply_to is not None:
            payload["reply_parameters"] = {
                "message_id": reply_to,
                "allow_sending_without_reply": True,
            }
        result = self._call("sendMessage", json=payload)
        return int(result["message_id"])

    def edit_message(self, chat_id: str, message_id: int, html: str) -> None:
        """Replace the text of a message the bot posted; an identical text is not an error."""
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": html,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        try:
            self._call("editMessageText", json=payload)
        except TelegramError as exc:
            if "message is not modified" not in exc.description.lower():
                raise

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        """Stop the pressed button spinning; Telegram forgets a callback id within a minute."""
        try:
            self._call("answerCallbackQuery", json={"callback_query_id": callback_id, "text": text})
        except (TelegramError, TelegramUnavailableError) as exc:
            log.warning("answerCallbackQuery: %s", exc)

    def get_updates(self, *, offset: int | None, timeout: int) -> list[ChannelPost]:
        """Long-poll `getUpdates` for channel posts and button presses; `offset` confirms every
        update below it. Only one consumer may poll at a time (Telegram answers 409 to the
        older one)."""
        payload: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": ["channel_post", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = self._call("getUpdates", json=payload, timeout=timeout + 15.0)
        updates = result.get("result")
        posts: list[ChannelPost] = []
        for update in updates if isinstance(updates, list) else []:
            post = _channel_post(update)
            if post is None and isinstance(update.get("update_id"), int):
                # Not a post (another update kind, an unreadable one): a placeholder from no
                # chat, so that the offset still moves past it.
                post = ChannelPost(
                    update_id=update["update_id"], chat_id=0, message_id=0, date=_EPOCH
                )
            if post is not None:
                posts.append(post)
        return posts

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
            if code in (401, 403, 409) or "chat not found" in description.lower():
                # Wrong token, bot not an admin, wrong channel id: affects every message. 409 is
                # "terminated by other getUpdates request" — a second relay or a webhook holding
                # the same bot; it affects every poll, and a per-call error made `run_forever`
                # log a traceback once a cycle for as long as the other consumer lived.
                raise TelegramUnavailableError(f"HTTP {code}: {description}")
            raise TelegramError(code, description)
        raise TelegramUnavailableError(f"{method}: gave up after {self.MAX_ATTEMPTS} attempts")


@dataclass
class TelegramPublishResult:
    message_id: int
    document_message_ids: list[int] = field(default_factory=list)  # none: the PDF is linked


class TelegramPublisher(RenderingPublisher):
    """The messages the base class renders go to the channel as Telegram posts."""

    def __init__(
        self,
        client: TelegramBotClient,
        formatter: MessageFormatter,
        *,
        channel_id: str,
    ) -> None:
        super().__init__(formatter)
        self._client = client
        self._channel_id = channel_id

    def _deliver(self, message: Outgoing) -> TelegramPublishResult:
        message_id = self._client.send_message(
            self._channel_id, message.text, reply_to=message.reply_to, button=message.action
        )
        return TelegramPublishResult(message_id=message_id)

    def _edit(self, message: Outgoing, *, message_id: int) -> None:
        self._client.edit_message(self._channel_id, message_id, message.text)


class TelegramRunNotifier:
    """Posts the run report and captured warnings to a technical log channel."""

    def __init__(
        self,
        client: TelegramBotClient,
        formatter: MessageFormatter,
        *,
        channel_id: str,
        run_url: str | None = None,
    ) -> None:
        self._client = client
        self._formatter = formatter
        self._channel_id = channel_id
        self._run_url = run_url

    def notify(self, report: RunReport, log_lines: list[str]) -> None:
        rendered = self._formatter.run_report(report, log_lines, run_url=self._run_url)
        self._client.send_message(self._channel_id, rendered.text)

    def notify_backfill(self, report: BackfillReport) -> None:
        rendered = self._formatter.backfill_report(report)
        self._client.send_message(self._channel_id, rendered.text)


def _channel_post(update: dict[str, Any]) -> ChannelPost | None:
    """The channel post in one `getUpdates` item; None for other kinds of update. A post
    without text (a photo, a poll) still comes back, so that the offset moves past it."""
    update_id = update.get("update_id")
    if isinstance(update_id, int) and isinstance(update.get("callback_query"), dict):
        return _button_press(update_id, update["callback_query"])
    post = update.get("channel_post") or update.get("message")
    if not isinstance(update_id, int) or not isinstance(post, dict):
        return None
    chat = post.get("chat") or {}
    try:
        return ChannelPost(
            update_id=update_id,
            chat_id=int(chat["id"]),
            chat_username=chat.get("username"),
            message_id=int(post["message_id"]),
            text=post.get("text"),
            date=datetime.fromtimestamp(int(post.get("date", 0)), tz=UTC),
            actor_id=(post.get("from") or {}).get("id"),
            author_signature=post.get("author_signature"),
        )
    except KeyError, TypeError, ValueError:
        log.warning("getUpdates: update %s has no readable post", update_id)
        return None


def _button_press(update_id: int, query: dict[str, Any]) -> ChannelPost | None:
    """A pressed button as the command it stands for, under the message it hangs on: the run
    then answers there, exactly as it answers a typed command."""
    message = query.get("message")
    data = query.get("data")
    if not isinstance(message, dict) or not isinstance(data, str):
        return None
    text = command_for_callback(data)
    if text is None:
        log.warning("callback %s: no command behind %r", update_id, data)
        return None
    chat = message.get("chat") or {}
    try:
        return ChannelPost(
            update_id=update_id,
            chat_id=int(chat["id"]),
            chat_username=chat.get("username"),
            message_id=int(message["message_id"]),
            text=text,
            date=datetime.fromtimestamp(int(message.get("date", 0)), tz=UTC),
            callback_id=str(query.get("id", "")) or None,
        )
    except KeyError, TypeError, ValueError:
        log.warning("callback %s has no readable message", update_id)
        return None


class TelegramAcknowledger:
    """`⏳ queued` under the command, so the operator knows the relay took it."""

    TEXT = "⏳ queued — the next run answers here in a few minutes"

    def __init__(self, client: TelegramBotClient, *, channel_id: str) -> None:
        self._client = client
        self._channel_id = channel_id

    def queued(self, command: IncomingCommand) -> None:
        self._client.send_message(self._channel_id, self.TEXT, reply_to=command.message_id)

    def started(self, command: IncomingCommand, note: str) -> None:
        """`/run` is answered by the relay itself: nothing was filed for a run to report on."""
        self._client.send_message(self._channel_id, f"▶️ {note}", reply_to=command.message_id)

    def pressed(self, callback_id: str) -> None:
        self._client.answer_callback(callback_id, self.TEXT)


class TelegramOperatorReplier:
    """Answers an operator command as a reply under it in the technical channel."""

    def __init__(
        self, client: TelegramBotClient, formatter: MessageFormatter, *, channel_id: str
    ) -> None:
        self._client = client
        self._formatter = formatter
        self._channel_id = channel_id

    def reply(self, command: IncomingCommand, outcome: CommandOutcome) -> None:
        rendered = self._formatter.command_reply(command, outcome)
        self._client.send_message(self._channel_id, rendered.text, reply_to=command.message_id)
