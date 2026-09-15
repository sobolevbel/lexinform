"""Publisher that prints rendered messages instead of sending them (dry runs, previews)."""

import sys
from dataclasses import dataclass, field
from typing import TextIO

from lexinform.adapters.publisher_base import Outgoing, RenderingPublisher
from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import BackfillReport, CommandOutcome, IncomingCommand, RunReport


@dataclass
class ConsolePublishResult:
    message_id: int
    document_message_ids: list[int] = field(default_factory=list)


def _about(message: Outgoing) -> str:
    """What the dry run's title says the message is about; the digest is about no bill."""
    return f"druk {message.bill.number}" if message.bill is not None else message.number


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
        title = f"{message.kind.replace('_', ' ').upper()} {_about(message)}"
        if message.detail:
            title += f" {message.detail}"
        if message.reply_to is not None:
            title += f" (reply to {message.reply_to})"
        if message.action is not None:
            title += f" [button: {message.action[0]}]"
        return ConsolePublishResult(message_id=self._emit(title, message.text))

    def _edit(self, message: Outgoing, *, message_id: int) -> None:
        self._emit(f"EDIT CARD {_about(message)} (message #{message_id})", message.text)


class ConsoleRunNotifier:
    def __init__(self, formatter: MessageFormatter, stream: TextIO = sys.stdout) -> None:
        self._formatter = formatter
        self._stream = stream

    def notify(self, report: RunReport, log_lines: list[str]) -> None:
        rendered = self._formatter.run_report(report, log_lines)
        self._stream.write(f"\n===== RUN REPORT (dry-run) =====\n{rendered.text}\n")
        self._stream.flush()

    def notify_backfill(self, report: BackfillReport) -> None:
        rendered = self._formatter.backfill_report(report)
        self._stream.write(f"\n===== BACKFILL REPORT =====\n{rendered.text}\n")
        self._stream.flush()


class ConsoleReplier:
    """The answer to an operator command, printed instead of posted."""

    def __init__(self, formatter: MessageFormatter, stream: TextIO = sys.stdout) -> None:
        self._formatter = formatter
        self._stream = stream

    def reply(self, command: IncomingCommand, outcome: CommandOutcome) -> None:
        rendered = self._formatter.command_reply(command, outcome)
        self._stream.write(
            f"\n===== REPLY to update {command.update_id} (dry-run) =====\n{rendered.text}\n"
        )
        self._stream.flush()
