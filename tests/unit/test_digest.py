"""The digest's pure parts: which week a day belongs to, what a month's figures are made of,
and how the message reads."""

import datetime as dt

import pytest

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import (
    Category,
    Digest,
    DigestEntry,
    MonthFigures,
    PublicationKind,
    RunMode,
    RunReport,
    TokenUsage,
    Upcoming,
    command_for_callback,
    is_first_digest_of_month,
    iso_week,
    previous_week,
    week_bounds,
)
from tests.formatting import assert_telegram_html

NOW = dt.datetime(2026, 9, 13, 18, 0, tzinfo=dt.UTC)


def _report(*, usage: dict[str, TokenUsage] | None = None, **counters: int) -> RunReport:
    return RunReport(
        started_at=NOW,
        finished_at=NOW,
        since=NOW,
        mode=RunMode.RUN,
        llm_usage=usage or {},
        **counters,
    )


def _entry(number: str, **overrides: object) -> DigestEntry:
    facts: dict[str, object] = {
        "term": 10,
        "number": number,
        "title": "Projekt ustawy o zmianie ustawy o cudzoziemcach",
        "kind": PublicationKind.NEW_BILL,
        "sent_at": NOW,
        "message_id": 101,
        "score": 4,
        "category": Category.LEGAL_STAY,
    }
    return DigestEntry.model_validate(facts | overrides)


def test_a_day_names_the_iso_week_it_falls_in() -> None:
    assert iso_week(dt.date(2026, 9, 13)) == "2026-W37"  # a Sunday belongs to the week it ends
    assert iso_week(dt.date(2026, 9, 14)) == "2026-W38"  # the Monday after starts the next


def test_a_week_is_monday_to_sunday() -> None:
    assert week_bounds("2026-W37") == (dt.date(2026, 9, 7), dt.date(2026, 9, 13))


def test_the_week_just_ended_is_seven_days_back() -> None:
    assert previous_week(dt.date(2026, 9, 14)) == "2026-W37"


@pytest.mark.parametrize("ref", ["nonsense", "2026", "2026-Wxx", ""])
def test_a_reference_that_is_not_a_week_is_refused(ref: str) -> None:
    with pytest.raises(ValueError, match="ISO week"):
        week_bounds(ref)


def test_the_month_belongs_to_the_week_that_ends_in_it() -> None:
    """A week straddling the turn is the first of the month it ends in, so the figures are whole."""
    assert is_first_digest_of_month("2026-W40") is True  # ends 04.10
    assert is_first_digest_of_month("2026-W41") is False  # ends 11.10


def test_a_month_counts_what_was_taken_in_and_never_what_a_phase_looked_at() -> None:
    figures = MonthFigures.of(
        dt.date(2026, 9, 30),
        [
            _report(discovered=10, prefilter_hits=2, analyzed=2, published=1),
            _report(rcl_discovered=4, wykaz_discovered=1, rcl_prefilter_hits=1, published=1),
        ],
    )

    assert (figures.month, figures.runs) == (dt.date(2026, 9, 1), 2)
    assert figures.discovered == 15
    assert figures.dropped_by_keywords == 12
    assert (figures.analyzed, figures.published) == (2, 2)


def test_a_month_sums_the_spend_per_model() -> None:
    usage = {"claude-opus-5": TokenUsage(input=1000, output=100)}
    figures = MonthFigures.of(dt.date(2026, 9, 5), [_report() for _ in range(2)])
    assert figures.usage == {}

    spent = MonthFigures.of(dt.date(2026, 9, 5), [_report(usage=usage), _report(usage=usage)])

    assert spent.usage["claude-opus-5"] == TokenUsage(input=2000, output=200)


def test_the_digest_names_the_week_its_cards_and_its_tag() -> None:
    week = Digest(
        ref="2026-W37",
        term=10,
        since=dt.date(2026, 9, 7),
        until=dt.date(2026, 9, 13),
        cards=(_entry("3039"),),
    )

    text = MessageFormatter("ru", channel="@lexinform").digest(week).text

    assert_telegram_html(text)
    assert "Итоги недели" in text and "07.09.2026 — 13.09.2026" in text
    assert '<a href="https://t.me/lexinform/101">druk 3039</a>' in text
    assert "🟠 4/5" in text
    assert text.endswith("#дайджест")


def test_only_the_draft_carries_the_button_note() -> None:
    week = Digest(ref="2026-W37", term=10, since=dt.date(2026, 9, 7), until=dt.date(2026, 9, 13))
    formatter = MessageFormatter("ru")

    assert "press the button" in formatter.digest(week, draft=True).text
    assert "press the button" not in formatter.digest(week).text


def test_a_numeric_channel_gets_no_links_because_it_has_no_public_address() -> None:
    week = Digest(
        ref="2026-W37",
        term=10,
        since=dt.date(2026, 9, 7),
        until=dt.date(2026, 9, 13),
        cards=(_entry("3039"),),
    )

    text = MessageFormatter("ru", channel="-1001234567").digest(week).text

    assert "druk 3039" in text and "t.me" not in text


def test_a_government_row_is_named_by_its_wykaz_number_and_a_print_by_its_druk() -> None:
    """The card's own header rule: only a Sejm print is a druk, and a project or a plan is the
    number the ministries use, which is also what the thread's tag carries."""
    week = Digest(
        ref="2026-W37",
        term=10,
        since=dt.date(2026, 9, 7),
        until=dt.date(2026, 9, 13),
        cards=(
            _entry("3039"),
            _entry("RCL/12412103", wykaz_number="UC104"),
            _entry("WPL/UD368", wykaz_number="UD368"),
            _entry("RCL/12414402"),  # a project the register does not number
            _entry("RPW/29075/2026"),
        ),
    )

    text = MessageFormatter("ru").digest(week).text

    assert "druk 3039" in text
    assert "UC104" in text and "RCL/12412103" not in text
    assert "UD368" in text and "WPL/UD368" not in text
    assert "RCL/12414402" in text  # named by its page, never called a druk
    assert "RPW/29075/2026" in text and "druk RPW" not in text


def test_an_open_consultation_is_dated_and_counted_down() -> None:
    week = Digest(
        ref="2026-W37",
        term=10,
        since=dt.date(2026, 9, 7),
        until=dt.date(2026, 9, 13),
        consultations=(
            Upcoming(
                term=10,
                number="RCL/12414100",
                title="Projekt",
                wykaz_number="UC164",
                deadline=dt.date(2026, 9, 20),
            ),
        ),
    )

    text = MessageFormatter("ru", today=lambda: dt.date(2026, 9, 13)).digest(week).text

    assert_telegram_html(text)
    assert "Идут консультации" in text and "20.09.2026" in text
    assert "UC164" in text  # a government project is named by its wykaz number, as its card is


def test_the_ask_is_silent_when_there_is_nowhere_to_send_it() -> None:
    week = Digest(ref="2026-W37", term=10, since=dt.date(2026, 9, 7), until=dt.date(2026, 9, 13))

    assert "поддержать" not in MessageFormatter("ru").digest(week).text
    with_url = MessageFormatter("ru", support_url="https://example.test/c").digest(week).text
    assert "поддержать" in with_url and "https://example.test/c" in with_url


def test_a_pressed_button_is_the_command_it_stands_for() -> None:
    assert command_for_callback("digest:2026-W38") == "/digest publish ref=2026-W38"


@pytest.mark.parametrize("data", ["digest:", "delete:3039", "", "digest"])
def test_data_no_button_of_ours_sent_is_no_command(data: str) -> None:
    assert command_for_callback(data) is None
