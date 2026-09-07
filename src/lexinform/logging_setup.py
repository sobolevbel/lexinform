"""Logging configuration: human-readable by default, JSON lines when requested."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO", *, json_output: bool = False) -> None:
    handler = logging.StreamHandler(sys.stderr)
    if json_output:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
        )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    # Third-party noise
    for noisy in ("httpx2", "httpcore", "anthropic"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, root.level))


class MemoryLogHandler(logging.Handler):
    """Keeps the last `capacity` formatted records at or above `level` for the run report."""

    def __init__(self, level: int = logging.WARNING, capacity: int = 40) -> None:
        super().__init__(level)
        self.capacity = capacity
        self.lines: list[str] = []
        self.dropped = 0

    def emit(self, record: logging.LogRecord) -> None:
        if len(self.lines) >= self.capacity:
            self.dropped += 1
            return
        # Message only: tracebacks belong in the full log, not in the Telegram report.
        self.lines.append(f"{record.levelname} {record.name}: {record.getMessage()}")

    def install(self) -> None:
        logging.getLogger().addHandler(self)

    def uninstall(self) -> None:
        logging.getLogger().removeHandler(self)
