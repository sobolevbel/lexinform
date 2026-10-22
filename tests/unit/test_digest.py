"""The digest's pure parts: which week a day belongs to, what a month's figures are made of,
and how the message reads."""

import datetime as dt

import pytest

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import (
    AgendaItem,
    Category,
    Digest,
    DigestEntry,
    MonthFigures,
    PublicationKind,
    RunMode,
    RunReport,
    TokenUsage,
    Upcoming,
    WeekFigures,
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


def test_weekly_filtering_counts_decisions_once_and_keeps_discovery_independent() -> None:
    reports = [
        _report(discovered=2).model_copy(update={"prefilter_rejected": ["10/1"]}),
        _report(rcl_discovered=3, analysis_skipped_cost=2).model_copy(
            update={"prefilter_rejected": ["10/1", "10/2"]}
        ),
    ]
    figures = WeekFigures.of(reports)

    assert figures.discovered == 5
    assert figures.filtered == 2


def test_legacy_reports_do_not_imply_zero_filtering() -> None:
    assert WeekFigures.of([_report()]).filtered is None


def test_run_errors_do_not_hide_recorded_filtering_decisions() -> None:
    failed = _report().model_copy(
        update={"prefilter_rejected": ["10/1"], "errors": ["Telegram outage"]}
    )
    assert WeekFigures.of([failed]).filtered == 1


@pytest.mark.parametrize("draft", [False, True])
def test_the_digest_says_in_one_line_what_the_bot_did(draft: bool) -> None:
    week = Digest(
        ref="2026-W37",
        since=dt.date(2026, 9, 7),
        until=dt.date(2026, 9, 13),
        figures=WeekFigures(discovered=97, filtered=2),
        followed=12,
        cards=(_entry("3039"),),
    )

    text = MessageFormatter("ru").digest(week, draft=draft).text

    assert (
        "🤖 Бот за неделю: новых проектов просмотрено — 97, взято в канал — 1,"
        " на контроле — 12." in text
    )


def test_a_week_with_no_run_records_states_no_figures() -> None:
    week = Digest(ref="2026-W37", since=dt.date(2026, 9, 7), until=dt.date(2026, 9, 13))

    assert "Бот за неделю" not in MessageFormatter("ru").digest(week).text


def test_may_is_declined_after_a_day_and_not_in_the_month_heading() -> None:
    fmt = MessageFormatter("ru")
    week = Digest(
        ref="2026-W22",
        since=dt.date(2026, 5, 25),
        until=dt.date(2026, 5, 31),
        month=MonthFigures.of(dt.date(2026, 5, 31), [_report()]),
    )

    text = fmt.digest(week).text

    assert fmt.fmt_date(dt.date(2026, 5, 3)) == "3 мая 2026"
    assert fmt.fmt_date_range(dt.date(2026, 4, 30), dt.date(2026, 5, 2)) == "30 апр - 2 мая 2026"
    assert fmt.fmt_date_range(dt.date(2026, 6, 29), dt.date(2026, 7, 2)) == "29 июня - 2 июля 2026"
    assert fmt.fmt_date(dt.date(2026, 3, 9)) == "9 марта 2026"
    assert "25-31 мая 2026" in text
    assert "· май 2026" in text


def test_the_digest_names_the_week_its_cards_and_its_tag() -> None:
    week = Digest(
        ref="2026-W37",
        since=dt.date(2026, 9, 7),
        until=dt.date(2026, 9, 13),
        cards=(_entry("3039"),),
    )

    text = MessageFormatter("ru", channel="@lexinform").digest(week).text

    assert_telegram_html(text)
    assert "Итоги недели</b> · 7-13 сент 2026" in text
    assert '<a href="https://t.me/lexinform/101">' in text and "· druk 3039</a>" in text
    assert "— Новый законопроект" in text
    assert "4/5" not in text  # the card carries the score; a line a bill does not
    assert text.endswith("#дайджест")


def test_only_the_draft_carries_the_button_note() -> None:
    week = Digest(ref="2026-W37", since=dt.date(2026, 9, 7), until=dt.date(2026, 9, 13))
    formatter = MessageFormatter("ru")

    assert "press the button" in formatter.digest(week, draft=True).text
    assert "press the button" not in formatter.digest(week).text


def test_a_private_numeric_channel_links_messages_with_its_internal_address() -> None:
    week = Digest(
        ref="2026-W37",
        since=dt.date(2026, 9, 7),
        until=dt.date(2026, 9, 13),
        cards=(_entry("3039"),),
    )

    text = MessageFormatter("ru", channel="-1001234567").digest(week).text

    assert "druk 3039" in text and "https://t.me/c/1234567/101" in text


def test_a_government_row_is_named_by_its_wykaz_number_and_a_print_by_its_druk() -> None:
    """The card's own header rule: only a Sejm print is a druk, and a project or a plan is the
    number the ministries use, which is also what the thread's tag carries."""
    week = Digest(
        ref="2026-W37",
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


def test_a_busy_week_is_capped_and_says_how_many_it_left_out() -> None:
    """Week 37 of the real channel carried 14 cards; showing ten and saying nothing about the
    other four is the one thing a digest must not do."""
    week = Digest(
        ref="2026-W37",
        since=dt.date(2026, 9, 7),
        until=dt.date(2026, 9, 13),
        cards=tuple(_entry(str(3000 + i)) for i in range(14)),
    )

    text = MessageFormatter("ru").digest(week).text

    assert_telegram_html(text)
    assert "druk 3007" in text and "druk 3008" not in text  # eight of them, in order
    assert "и ещё 6" in text


def test_a_section_that_fits_says_nothing_about_a_remainder() -> None:
    week = Digest(
        ref="2026-W37",
        since=dt.date(2026, 9, 7),
        until=dt.date(2026, 9, 13),
        cards=tuple(_entry(str(3000 + i)) for i in range(8)),
    )

    assert "и ещё" not in MessageFormatter("ru").digest(week).text


def test_an_open_consultation_is_dated_and_counted_down() -> None:
    week = Digest(
        ref="2026-W37",
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
    assert "⏰ <b>Успеть</b>\n• до 20 сент 2026 — " in text
    assert "· UC164" in text  # a government project is named by its wykaz number, as its card is


def test_the_readers_digest_carries_no_delivery_bookkeeping_and_no_misplaced_advice() -> None:
    """Draft W42 (18 Oct 2026) said «unknown доставок» to readers and asked for a wykaz number
    under a Sejm survey."""
    week = Digest(
        ref="2026-W42",
        since=dt.date(2026, 10, 12),
        until=dt.date(2026, 10, 18),
        pending_events=2,
        consultations=(
            Upcoming(
                term=10,
                number="3205",
                title="Projekt",
                action_url="https://opiniowanie.sejm.gov.pl/RPW-31544-2026",
                deadline=dt.date(2026, 10, 21),
            ),
            Upcoming(
                term=10,
                number="RCL/12414100",
                title="Projekt",
                action_email="dep@mswia.gov.pl",
                wykaz_number="UC164",
                deadline=dt.date(2026, 10, 30),
            ),
        ),
    )
    formatter = MessageFormatter("ru", today=lambda: dt.date(2026, 10, 18), attention_enabled=True)

    readers = "\n".join(formatter.digest_parts(week))
    draft = "\n".join(formatter.digest_parts(week, draft=True))

    assert "unknown" not in readers and "Полнота обзора" not in readers
    assert "Полнота обзора" in draft
    assert '· <a href="https://opiniowanie.sejm.gov.pl/RPW-31544-2026">анкета для мнения</a>' in (
        readers
    )
    assert "· замечания на dep@mswia.gov.pl" in readers
    assert "wykaz" not in readers


def test_a_routine_step_is_covered_by_the_digest_without_a_line_of_its_own() -> None:
    week = Digest(
        ref="2026-W42",
        since=dt.date(2026, 10, 12),
        until=dt.date(2026, 10, 18),
        updates=(
            _entry(
                "RCL/12409404",
                kind=PublicationKind.STATUS_UPDATE,
                event="rcl_to_sejm",
                wykaz_number="UDER106",
                source_publication_ids=(164,),
            ),
            _entry("3039", kind=PublicationKind.STATUS_UPDATE, event="signed"),
        ),
    )

    (part,) = MessageFormatter("ru").digest_layout(week)
    (draft,) = MessageFormatter("ru").digest_layout(week, draft=True)

    assert "UDER106" not in part.text and "— Президент подписал закон" in part.text
    assert part.event_ids == (164,) and draft.event_ids == ()


def test_a_bill_is_one_line_named_by_the_post_that_says_most() -> None:
    card = _entry("3039", mnemonic="Цифровой курсант", card_message_id=101)
    week = Digest(
        ref="2026-W42",
        since=dt.date(2026, 10, 12),
        until=dt.date(2026, 10, 18),
        cards=(card.model_copy(update={"consequence": "Карта курсанта"}),),
        updates=(
            _entry(
                "3039",
                kind=PublicationKind.STATUS_UPDATE,
                event="text_changed",
                mnemonic="Цифровой курсант",
                consequence="Экзамен действует 12 месяцев, а не 24.",
                card_message_id=101,
                message_id=150,
            ),
        ),
    )

    text = MessageFormatter("ru", channel="@lexinform").digest(week).text

    assert text.count("Цифровой курсант") == 1
    assert (
        '<a href="https://t.me/lexinform/101">Цифровой курсант · druk 3039</a>'
        " — Новая версия текста: Экзамен действует 12 месяцев, а не 24." in text
    )


def _sitting(**overrides: object) -> AgendaItem:
    facts: dict[str, object] = {
        "kind": "committee",
        "ref": "ASW/136/2026-09-17",
        "date": dt.date(2026, 9, 17),
        "start_time": dt.time(9, 0),
        "committee_code": "ASW",
        "committee_name": "Komisja Administracji i Spraw Wewnętrznych",
    }
    return AgendaItem.model_validate(facts | overrides)


def _ahead(*sittings: AgendaItem) -> Digest:
    return Digest(
        ref="2026-W37",
        since=dt.date(2026, 9, 7),
        until=dt.date(2026, 9, 13),
        sittings=tuple(
            Upcoming(term=10, number="3039", title="Projekt", sitting=s) for s in sittings
        ),
    )


def test_a_conditional_sitting_is_marked_and_not_stated_as_a_date() -> None:
    """21 committee sittings of term 10 happen only if the Sejm refers something first; the card
    marks them «условно» and a digest that promises the day instead is the same old bug."""
    text = MessageFormatter("ru").digest(_ahead(_sitting(condition="referral"))).text

    assert_telegram_html(text)
    assert "17 сент 2026" in text and "(условно)" in text


def test_a_settled_sitting_carries_no_hedge() -> None:
    assert "условно" not in MessageFormatter("ru").digest(_ahead(_sitting())).text


def test_a_plenary_sitting_names_itself_once() -> None:
    plenary = _sitting(
        kind="sejm", committee_code=None, committee_name=None, sitting_number=65, start_time=None
    )

    text = MessageFormatter("ru").digest(_ahead(plenary)).text

    # `_agenda_when` names it and numbers it; naming it again gave «Заседание Сейма, ... № 65».
    assert text.lower().count("заседание сейма") == 1
    assert "№ 65" in text


@pytest.mark.parametrize("language", ["ru", "en"])
def test_sitting_groups_list_linked_bills_with_titles(language: str) -> None:
    plenary = _sitting(
        kind="sejm",
        ref="sejm/65/2026-09-15",
        date=dt.date(2026, 9, 15),
        end_date=dt.date(2026, 9, 18),
        sitting_number=65,
        committee_code=None,
        committee_name=None,
        start_time=None,
    )
    windows = tuple(
        Upcoming(
            term=10,
            number=number,
            title=title,
            message_id=message_id,
            sitting=plenary.model_copy(update={"text": f"druk nr {number}"}),
        )
        for number, title, message_id in (
            ("3039", "Projekt ustawy o cudzoziemcach", 101),
            ("3100", "Projekt ustawy o obywatelstwie & ochronie", 102),
        )
    )
    week = _ahead().model_copy(update={"sittings": (*windows, windows[0])})

    text = MessageFormatter(language, channel="@test").digest(week).text

    assert_telegram_html(text)
    assert text.count("заседание Сейма" if language == "ru" else "Sejm sitting") == 1
    assert '<a href="https://t.me/test/101">O cudzoziemcach · druk 3039</a>' in text
    assert '<a href="https://t.me/test/102">O obywatelstwie &amp; ochronie · druk 3100</a>' in text
    assert text.count("druk 3039") == 1
    assert text.index("65,") < text.index("druk 3039") < text.index("druk 3100")


def test_a_sitting_with_many_bills_names_them_by_number() -> None:
    plenary = _sitting(kind="sejm", committee_code=None, committee_name=None, sitting_number=67)
    week = _ahead().model_copy(
        update={
            "sittings": tuple(
                Upcoming(
                    term=10, number=number, title="Projekt ustawy o cudzoziemcach", sitting=plenary
                )
                for number in ("3003", "3141", "3146")
            )
        }
    )

    text = MessageFormatter("ru").digest(week).text

    assert "— druk 3003, druk 3141, druk 3146" in text
    assert "cudzoziemcach" not in text


def test_committee_sittings_are_separate_lines_in_date_order() -> None:
    early = _sitting(sitting_number=136)
    later = _sitting(ref="ASW/137/2026-09-18", date=dt.date(2026, 9, 18), sitting_number=137)

    text = MessageFormatter("ru").digest(_ahead(later, early)).text

    assert_telegram_html(text)
    assert text.count("Komisja Administracji i Spraw Wewnętrznych") == 2
    assert text.index("• 17 сент 2026, 09:00, Komisja") < text.index(
        "• 18 сент 2026, 09:00, Komisja"
    )


def test_a_sitting_without_bill_entries_is_omitted() -> None:
    week = _ahead().model_copy(
        update={"sittings": (Upcoming(term=10, number="3039", title="Projekt"),)}
    )

    assert "Заседания" not in MessageFormatter("ru").digest(week).text


def test_an_open_consultation_says_what_the_bill_is() -> None:
    """The one section a reader acts on: a bare number and a date named nothing."""
    week = Digest(
        ref="2026-W37",
        since=dt.date(2026, 9, 7),
        until=dt.date(2026, 9, 13),
        consultations=(
            Upcoming(
                term=10,
                number="RCL/12414100",
                title="Projekt ustawy o zmianie ustawy o cudzoziemcach",
                deadline=dt.date(2026, 9, 20),
            ),
        ),
    )

    text = MessageFormatter("ru", today=lambda: dt.date(2026, 9, 13)).digest(week).text

    assert "O cudzoziemcach" in text
    assert "Projekt ustawy o zmianie ustawy" not in text


def test_the_ask_is_silent_when_there_is_nowhere_to_send_it() -> None:
    week = Digest(ref="2026-W37", since=dt.date(2026, 9, 7), until=dt.date(2026, 9, 13))

    assert "поддержать" not in MessageFormatter("ru").digest(week).text
    with_url = MessageFormatter("ru", support_url="https://example.test/c").digest(week).text
    assert "поддержать" in with_url and "https://example.test/c" in with_url


def test_a_pressed_button_is_the_command_it_stands_for() -> None:
    assert command_for_callback("digest:2026-W38") == "/digest publish ref=2026-W38"


@pytest.mark.parametrize("data", ["digest:", "delete:3039", "", "digest"])
def test_data_no_button_of_ours_sent_is_no_command(data: str) -> None:
    assert command_for_callback(data) is None
