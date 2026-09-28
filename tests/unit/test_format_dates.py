import datetime as dt

import pytest

from lexinform.adapters.telegram_format import MessageFormatter


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        ("2026-10-06", "2026-10-09", "6-9 окт 2026"),
        ("2026-09-21", "2026-09-27", "21-27 сент 2026"),
        ("2026-10-21", "2026-10-21", "21 окт 2026"),
        ("2026-09-30", "2026-10-02", "30 сент - 2 окт 2026"),
        ("2026-12-29", "2027-01-03", "29 дек 2026 - 3 янв 2027"),
    ],
)
def test_readable_date_ranges(start: str, end: str, expected: str) -> None:
    formatter = MessageFormatter("ru")

    assert (
        formatter.fmt_date_range(dt.date.fromisoformat(start), dt.date.fromisoformat(end))
        == expected
    )
