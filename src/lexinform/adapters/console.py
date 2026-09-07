"""Publisher that prints rendered messages instead of sending them (dry runs, previews)."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import TextIO

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import Bill, PrintInfo, RunReport, StatusChange


@dataclass
class ConsolePublishResult:
    message_id: int
    document_message_ids: list[int] = field(default_factory=list)


class ConsolePublisher:
    def __init__(self, formatter: MessageFormatter, stream: TextIO = sys.stdout) -> None:
        self._formatter = formatter
        self._stream = stream
        self._counter = 0

    def _emit(self, title: str, body: str) -> int:
        self._counter += 1
        self._stream.write(f"\n===== {title} (dry-run message #{self._counter}) =====\n{body}\n")
        self._stream.flush()
        return self._counter

    def publish_new_bill(self, bill: Bill, print_info: PrintInfo | None) -> ConsolePublishResult:
        rendered = self._formatter.new_bill(bill, print_info)
        message_id = self._emit(f"NEW BILL druk {bill.number}", rendered.text)
        return ConsolePublishResult(message_id=message_id)

    def publish_status_update(
        self, bill: Bill, change: StatusChange, reply_to: int | None
    ) -> ConsolePublishResult:
        rendered = self._formatter.status_update(bill, change)
        message_id = self._emit(
            f"STATUS UPDATE druk {bill.number} (reply to {reply_to})", rendered.text
        )
        return ConsolePublishResult(message_id=message_id)

    def publish_act_published(self, bill: Bill, reply_to: int | None) -> ConsolePublishResult:
        rendered = self._formatter.act_published(bill)
        message_id = self._emit(
            f"ACT PUBLISHED druk {bill.number} (reply to {reply_to})", rendered.text
        )
        return ConsolePublishResult(message_id=message_id)

    def publish_in_force(self, bill: Bill, reply_to: int | None) -> ConsolePublishResult:
        rendered = self._formatter.in_force(bill)
        message_id = self._emit(f"IN FORCE druk {bill.number} (reply to {reply_to})", rendered.text)
        return ConsolePublishResult(message_id=message_id)


class ConsoleRunNotifier:
    def __init__(self, formatter: MessageFormatter, stream: TextIO = sys.stdout) -> None:
        self._formatter = formatter
        self._stream = stream

    def notify(self, report: RunReport, log_lines: list[str]) -> None:
        rendered = self._formatter.run_report(report, log_lines)
        self._stream.write(f"\n===== RUN REPORT (dry-run) =====\n{rendered.text}\n")
        self._stream.flush()
