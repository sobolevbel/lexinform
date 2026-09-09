"""Publisher that prints rendered messages instead of sending them (dry runs, previews)."""

import sys
from dataclasses import dataclass, field
from typing import TextIO

from lexinform.adapters.publisher_base import Outgoing, RenderingPublisher
from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import RunReport


@dataclass
class ConsolePublishResult:
    message_id: int
    document_message_ids: list[int] = field(default_factory=list)


class ConsolePublisher(RenderingPublisher):
    """The messages the base class renders are printed with a title naming the kind."""

    def __init__(self, formatter: MessageFormatter, stream: TextIO = sys.stdout) -> None:
        super().__init__(formatter)
        self._stream = stream
        self._counter = 0

    def _emit(self, title: str, body: str) -> int:
        self._counter += 1
        self._stream.write(f"\n===== {title} (dry-run message #{self._counter}) =====\n{body}\n")
        self._stream.flush()
        return self._counter

    def _deliver(self, message: Outgoing) -> ConsolePublishResult:
        title = f"{message.kind.value.replace('_', ' ').upper()} druk {message.bill.number}"
        if message.detail:
            title += f" {message.detail}"
        if message.reply_to is not None:
            title += f" (reply to {message.reply_to})"
        return ConsolePublishResult(message_id=self._emit(title, message.text))

    def _edit(self, message: Outgoing, *, message_id: int) -> None:
        self._emit(f"EDIT CARD druk {message.bill.number} (message #{message_id})", message.text)


class ConsoleRunNotifier:
    def __init__(self, formatter: MessageFormatter, stream: TextIO = sys.stdout) -> None:
        self._formatter = formatter
        self._stream = stream

    def notify(self, report: RunReport, log_lines: list[str]) -> None:
        rendered = self._formatter.run_report(report, log_lines)
        self._stream.write(f"\n===== RUN REPORT (dry-run) =====\n{rendered.text}\n")
        self._stream.flush()
