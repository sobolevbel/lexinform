from datetime import UTC, datetime


class SystemClock:
    """Wall clock in UTC, to the second; tests use `FixedClock` instead."""

    def now(self) -> datetime:
        return datetime.now(UTC).replace(microsecond=0)
