"""The in-memory handler that feeds warnings into the run report."""

import logging

from lexinform.logging_setup import MemoryLogHandler


def _logger(handler: MemoryLogHandler) -> logging.Logger:
    logger = logging.getLogger("lexinform.test.memory")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers = [handler]
    return logger


def test_keeps_warnings_and_above_without_tracebacks() -> None:
    handler = MemoryLogHandler()
    logger = _logger(handler)

    logger.info("noise")
    logger.warning("slow %s", "download")
    try:
        raise ValueError("boom")
    except ValueError:
        logger.exception("failed")

    assert handler.lines == [
        "WARNING lexinform.test.memory: slow download",
        "ERROR lexinform.test.memory: failed",
    ]


def test_lines_already_in_the_report_are_skipped() -> None:
    handler = MemoryLogHandler()
    logger = _logger(handler)

    logger.error("phase failed", extra={"in_report": True})
    logger.error("something else")

    assert handler.lines == ["ERROR lexinform.test.memory: something else"]


def test_capacity_is_enforced_and_the_overflow_counted() -> None:
    handler = MemoryLogHandler(capacity=2)
    logger = _logger(handler)

    for i in range(5):
        logger.warning("w%d", i)

    assert len(handler.lines) == 2
    assert handler.dropped == 3
