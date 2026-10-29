"""Logging configuration: human-readable by default, JSON lines when requested."""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import override

from lexinform.log_context import correlation_fields, current_log_fields


class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation = {
            **current_log_fields(),
            **correlation_fields(getattr(record, "correlation", None)),
        }
        return True


class TextFormatter(logging.Formatter):
    @override
    def formatMessage(self, record: logging.LogRecord) -> str:
        text = super().formatMessage(record)
        fields = correlation_fields(getattr(record, "correlation", None))
        if fields:
            text += " | " + json.dumps(fields, ensure_ascii=False, sort_keys=True)
        return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        payload.update(correlation_fields(getattr(record, "correlation", None)))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO", *, json_output: bool = False) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(CorrelationFilter())
    if json_output:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            TextFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
        )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    for noisy in ("httpx2", "httpcore", "anthropic"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, root.level))
    logging.getLogger("pypdf").setLevel(logging.ERROR)


class MemoryLogHandler(logging.Handler):
    """Keeps the last `capacity` formatted records at or above `level` for the run report."""

    def __init__(self, level: int = logging.WARNING, capacity: int = 40) -> None:
        super().__init__(level)
        self.capacity = capacity
        self.lines: list[str] = []
        self.dropped = 0

    def emit(self, record: logging.LogRecord) -> None:
        """Keep the message alone: a record already listed in the run report's errors is
        skipped, and tracebacks belong in the full log, not in the Telegram report."""
        if getattr(record, "in_report", False):
            return
        if len(self.lines) >= self.capacity:
            self.dropped += 1
            return
        self.lines.append(f"{record.levelname} {record.name}: {record.getMessage()}")

    def install(self) -> None:
        logging.getLogger().addHandler(self)

    def uninstall(self) -> None:
        logging.getLogger().removeHandler(self)
