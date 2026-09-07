"""Thin Telegram Bot API client and the Publisher built on it."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx2 as httpx

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.errors import TelegramUnavailableError
from lexinform.models import Bill, PrintInfo, RunReport, StatusChange
from lexinform.ports import SejmGateway

log = logging.getLogger(__name__)

URL_UPLOAD_LIMIT = 20 * 1024 * 1024  # Telegram fetches documents by URL up to 20 MB
UPLOAD_LIMIT = 50 * 1024 * 1024  # multipart upload limit for bots


class TelegramError(RuntimeError):
    def __init__(self, code: int, description: str) -> None:
        super().__init__(f"Telegram API error {code}: {description}")
        self.code = code
        self.description = description


class TelegramBotClient:
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

    def send_document(
        self,
        chat_id: str,
        *,
        document_url: str | None = None,
        document_bytes: bytes | None = None,
        filename: str = "document.pdf",
        caption_html: str = "",
        reply_to: int | None = None,
    ) -> int:
        data: dict[str, Any] = {"chat_id": chat_id, "parse_mode": "HTML"}
        if caption_html:
            data["caption"] = caption_html
        if reply_to is not None:
            data["reply_to_message_id"] = str(reply_to)
            data["allow_sending_without_reply"] = "true"
        if document_bytes is not None:
            files = {"document": (filename, document_bytes, "application/pdf")}
            result = self._call("sendDocument", data=data, files=files)
        elif document_url is not None:
            data["document"] = document_url
            result = self._call("sendDocument", data=data)
        else:
            raise ValueError("either document_url or document_bytes is required")
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
            if code == 401:
                raise TelegramUnavailableError(f"bot token rejected: {description}")
            raise TelegramError(code, description)
        raise TelegramUnavailableError(f"{method}: gave up after {self.MAX_ATTEMPTS} attempts")


@dataclass
class TelegramPublishResult:
    message_id: int
    document_message_ids: list[int] = field(default_factory=list)


class TelegramPublisher:
    """Publishes a card message and the main PDF as a reply to it."""

    def __init__(
        self,
        client: TelegramBotClient,
        formatter: MessageFormatter,
        *,
        channel_id: str,
        gateway: SejmGateway | None = None,
        send_documents: bool = True,
    ) -> None:
        self._client = client
        self._formatter = formatter
        self._channel_id = channel_id
        self._gateway = gateway
        self._send_documents = send_documents

    def publish_new_bill(self, bill: Bill, print_info: PrintInfo | None) -> TelegramPublishResult:
        rendered = self._formatter.new_bill(bill, print_info)
        message_id = self._client.send_message(self._channel_id, rendered.text)
        result = TelegramPublishResult(message_id=message_id)
        pdf = print_info.main_pdf if print_info else None
        if pdf is None or not self._send_documents:
            return result
        try:
            doc_id = self._send_pdf(pdf.url, pdf.name, rendered.caption, reply_to=message_id)
        except TelegramUnavailableError:
            raise
        except (TelegramError, httpx.HTTPError, RuntimeError) as exc:
            # The card is already out with a PDF link; a failed attachment is not fatal.
            log.warning("Could not attach PDF for druk %s: %s", bill.number, exc)
            return result
        if doc_id is not None:
            result.document_message_ids.append(doc_id)
        return result

    def publish_status_update(
        self, bill: Bill, change: StatusChange, reply_to: int | None
    ) -> TelegramPublishResult:
        rendered = self._formatter.status_update(bill, change)
        message_id = self._client.send_message(self._channel_id, rendered.text, reply_to=reply_to)
        return TelegramPublishResult(message_id=message_id)

    def _send_pdf(self, url: str, filename: str, caption: str, *, reply_to: int) -> int | None:
        size = self._gateway.attachment_size(url) if self._gateway else None
        if size is not None and size > UPLOAD_LIMIT:
            log.info("PDF %s is %d bytes, over the Telegram limit; link only", filename, size)
            return None
        if size is None or size <= URL_UPLOAD_LIMIT:
            try:
                return self._client.send_document(
                    self._channel_id, document_url=url, caption_html=caption, reply_to=reply_to
                )
            except TelegramError as exc:
                if self._gateway is None:
                    raise
                log.info(
                    "Telegram could not fetch %s by URL (%s); uploading", filename, exc.description
                )
        if self._gateway is None:
            return None
        data = self._gateway.download(url)
        if len(data) > UPLOAD_LIMIT:
            return None
        return self._client.send_document(
            self._channel_id,
            document_bytes=data,
            filename=filename,
            caption_html=caption,
            reply_to=reply_to,
        )


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
