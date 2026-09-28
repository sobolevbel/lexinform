from datetime import UTC, date, datetime

import pytest
from bs4 import BeautifulSoup
from django.test import Client, override_settings

from lexinform_web.participation import (
    ParticipationState,
    ParticipationWindow,
    participation_display,
)

WINDOW = ParticipationWindow(start=date(2026, 9, 1), end=date(2026, 9, 21), confirmed=True)


@pytest.mark.parametrize(
    ("now", "state", "days_left"),
    [
        (datetime(2026, 8, 31, 12, tzinfo=UTC), ParticipationState.UPCOMING, None),
        (datetime(2026, 8, 31, 22, 30, tzinfo=UTC), ParticipationState.OPEN, 20),
        (datetime(2026, 9, 20, 21, 59, tzinfo=UTC), ParticipationState.OPEN, 1),
        (datetime(2026, 9, 20, 22, 0, tzinfo=UTC), ParticipationState.LAST_DAY, 0),
        (datetime(2026, 9, 21, 21, 59, tzinfo=UTC), ParticipationState.LAST_DAY, 0),
        (datetime(2026, 9, 21, 22, 0, tzinfo=UTC), ParticipationState.CLOSED, None),
    ],
)
def test_state_follows_the_warsaw_calendar_day(
    now: datetime, state: ParticipationState, days_left: int | None
) -> None:
    display = participation_display(WINDOW, now)

    assert display.state is state
    assert display.days_left == days_left


def test_winter_time_moves_the_warsaw_midnight() -> None:
    window = ParticipationWindow(start=None, end=date(2026, 11, 2), confirmed=True)

    assert participation_display(window, datetime(2026, 11, 1, 22, 59, tzinfo=UTC)).state is (
        ParticipationState.OPEN
    )
    assert participation_display(window, datetime(2026, 11, 1, 23, 0, tzinfo=UTC)).state is (
        ParticipationState.LAST_DAY
    )


@pytest.mark.parametrize(
    "window",
    [
        ParticipationWindow(start=None, end=date(2026, 10, 1), confirmed=False),
        ParticipationWindow(start=date(2026, 9, 1), end=None, confirmed=True),
    ],
)
def test_unconfirmed_or_open_ended_window_is_never_an_action(window: ParticipationWindow) -> None:
    display = participation_display(window, datetime(2026, 9, 15, tzinfo=UTC))

    assert display.state is ParticipationState.UNCONFIRMED
    assert not display.is_action


@pytest.mark.django_db
def test_gallery_is_absent_without_demo_settings(client: Client) -> None:
    assert client.get("/__components__/", follow=True).status_code == 404


@override_settings(WEBSITE_DEMO_ENABLED=True)
@pytest.mark.parametrize(
    ("day", "actions", "calendar_lines", "notices"),
    [("2026-09-21", 1, 2, 2), ("2026-09-30", 1, 2, 2), ("2026-10-09", 0, 3, 2)],
)
def test_gallery_reproduces_states_for_the_chosen_day(
    client: Client, day: str, actions: int, calendar_lines: int, notices: int
) -> None:
    response = client.get("/__components__/", {"on": day})

    document = BeautifulSoup(response.content, "html.parser")
    assert response.status_code == 200
    assert document.select_one('meta[name="robots"][content="noindex,nofollow"]')
    assert document.select_one("script") is None
    assert document.select_one(f'input[name="on"][value="{day}"]')
    assert len(document.select("section.action")) == actions
    assert len(document.select("p.calendar-line")) == calendar_lines
    assert len(document.select("aside.notice")) == notices
    assert len(document.select("main h1")) == 1


@override_settings(WEBSITE_DEMO_ENABLED=True)
def test_gallery_default_day_shows_the_last_day_in_russian(client: Client) -> None:
    text = BeautifulSoup(client.get("/__components__/").content, "html.parser").get_text()

    assert "Сегодня, 21 сентября 2026, — последний день консультаций" in text
    assert "Консультации назначены: 24 сентября 2026 — 8 октября 2026" in text
    assert "Консультации завершились 10 сентября 2026" in text
    assert "Открытый срок участия не подтверждён" in text


@override_settings(WEBSITE_DEMO_ENABLED=True)
def test_gallery_rejects_an_invalid_day_with_an_associated_error(client: Client) -> None:
    response = client.get("/__components__/", {"on": "21.09.2026"})

    document = BeautifulSoup(response.content, "html.parser")
    assert response.status_code == 400
    field = document.select_one('input[name="on"]')
    assert field is not None
    assert field.get("aria-invalid") == "true"
    assert document.select_one(f"#{field['aria-describedby']}") is not None
    assert client.post("/__components__/").status_code == 405
