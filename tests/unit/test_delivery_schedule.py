import datetime as dt
import time
from collections.abc import Iterator

import pytest

from lexinform.delivery_schedule import daily_schedule, sitting_is_close
from lexinform.models import AttentionAssessment, PublicationKind

NOW = dt.datetime(2026, 9, 7, 15, 30, tzinfo=dt.UTC)


@pytest.fixture(params=["UTC", "Asia/Tokyo"])
def host_timezone(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    try:
        with monkeypatch.context() as patch:
            patch.setenv("TZ", str(request.param))
            time.tzset()
            yield
    finally:
        time.tzset()


@pytest.mark.parametrize(
    "assessment",
    [
        None,
        AttentionAssessment(
            importance="important", urgency="normal", detected_at=NOW, assessed_at=NOW
        ),
        AttentionAssessment(
            importance="routine", urgency="urgent", detected_at=NOW, assessed_at=NOW
        ),
    ],
    ids=["unassessed", "important", "urgent"],
)
def test_daily_queue_requires_an_assessed_unprotected_event(
    assessment: AttentionAssessment | None,
) -> None:
    assert (
        daily_schedule(
            assessment, PublicationKind.STATUS_UPDATE, now=NOW, release_hour=18, urgent_days=3
        )
        is None
    )


@pytest.mark.parametrize(
    "now,expected",
    [
        (NOW, dt.datetime(2026, 9, 7, 16, tzinfo=dt.UTC)),
        (
            dt.datetime(2026, 9, 7, 16, 30, tzinfo=dt.UTC),
            dt.datetime(2026, 9, 8, 16, tzinfo=dt.UTC),
        ),
    ],
    ids=["before_release", "after_release"],
)
def test_release_uses_warsaw_wall_time_independently_of_the_host_timezone(
    host_timezone: None, now: dt.datetime, expected: dt.datetime
) -> None:
    assessment = AttentionAssessment(
        importance="routine", urgency="normal", detected_at=now, assessed_at=now
    )

    schedule = daily_schedule(
        assessment, PublicationKind.STATUS_UPDATE, now=now, release_hour=18, urgent_days=3
    )

    assert schedule is not None, "an assessed routine update has a daily release window"
    assert schedule.route == "daily_updates"
    assert schedule.detected_at == now
    assert schedule.not_before == expected
    assert schedule.deliver_by - now == dt.timedelta(hours=24)


@pytest.mark.parametrize(
    "deadline,close",
    [(None, False), (dt.date(2026, 9, 11), True), (dt.date(2026, 9, 12), False)],
    ids=["unknown", "at_urgent_boundary", "outside_urgent_boundary"],
)
def test_sitting_urgency_includes_the_boundary_on_the_warsaw_date(
    host_timezone: None, deadline: dt.date | None, close: bool
) -> None:
    now = dt.datetime(2026, 9, 7, 22, 30, tzinfo=dt.UTC)
    assessment = AttentionAssessment(
        importance="routine",
        urgency="normal",
        detected_at=now,
        assessed_at=now,
        deadline=deadline,
    )

    assert sitting_is_close(assessment, now=now, urgent_days=3) is close
