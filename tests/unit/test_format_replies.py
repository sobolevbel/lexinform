"""The replies that go under a card: a sitting on the calendar, a consultation closing, the
opinions published, the act in Dziennik Ustaw, the day it comes into force."""

import datetime as dt

import pytest

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import ProcessDetail, SittingNews
from tests.formatting import (
    ACT,
    COMMITTEE_PAGE,
    CONSULTATION_PAGE,
    PLENARY,
    SURVEY,
    TODAY,
    assert_telegram_html,
    bill_of,
    consulted,
    sitting,
)
from tests.harness import act


def test_the_sittings_roundup_names_each_sitting_in_a_line_linked_to_its_card(
    process_3039: ProcessDetail,
) -> None:
    bill = bill_of(process_3039, submission=consulted(), agenda=(sitting(), PLENARY))
    news = [
        SittingNews(bill=bill, item=sitting(), card_message_id=7),
        SittingNews(bill=bill, item=PLENARY, card_message_id=7),
    ]

    text = MessageFormatter("ru", channel="@lexinform").sittings(news).text

    assert_telegram_html(text)
    assert text.startswith("🗓 <b>Заседания</b>\n\n<b>Сейм</b>")
    assert text.index("<b>Сейм</b>") < text.index("<b>Комиссии</b>")
    card = '<a href="https://t.me/lexinform/7">druk nr 3039</a>'
    assert f"• <b>{card}</b> — заседание Сейма № 65, 15-18 сент 2026" in text
    assert (
        f"• <b>{card}</b> — Komisja Administracji i Spraw Wewnętrznych (ASW) · "
        "17 сент 2026, 09:00 · sala nr 412 · "
    ) in text
    assert 'transmisje_arch.xsp?unid=1">Трансляция</a>' in text
    assert "<i>Pierwsze czytanie projektu (druk nr 3039)" in text
    assert "Суть проекта" not in text and "Что можно сделать" not in text
    assert COMMITTEE_PAGE not in text
    assert text.splitlines()[-1] == "#заседаниесейма #заседаниекомиссии #kadencja10druk3039"


def test_a_sitting_taken_back_is_one_line_saying_which_fact_it_is(
    process_3039: ProcessDetail,
) -> None:
    bill = bill_of(process_3039)
    off = SittingNews(bill=bill, item=sitting(), cancelled=True)
    dropped = off.model_copy(update={"still_meets": True})

    fmt = MessageFormatter("ru")

    text = fmt.sittings([off]).text
    assert_telegram_html(text)
    assert "❌ <b>druk nr 3039</b> — Komisja Administracji i Spraw Wewnętrznych (ASW) · " in text
    assert "было запланировано на 17 сент 2026, 09:00\nЗаседание отменено" in text
    assert "Проект снят с повестки заседания" in fmt.sittings([dropped]).text


def test_sejm_sitting_in_english_writes_the_year_first(process_3039: ProcessDetail) -> None:
    bill = bill_of(process_3039, agenda=(PLENARY,))

    en = MessageFormatter("en").sittings([SittingNews(bill=bill, item=PLENARY)]).text

    assert "Sittings" in en
    assert "Sejm sitting no. 65, 2026-09-15 – 2026-09-18" in en


def test_a_sitting_that_runs_into_the_next_month_is_not_written_backwards(
    process_3039: ProcessDetail,
) -> None:
    crossing = PLENARY.model_copy(
        update={"date": dt.date(2026, 9, 30), "end_date": dt.date(2026, 10, 2)}
    )
    bill = bill_of(process_3039, agenda=(crossing,))

    text = MessageFormatter("ru").sittings([SittingNews(bill=bill, item=crossing)]).text

    assert "заседание Сейма № 65, 30 сент - 2 окт 2026" in text


def test_consultation_deadline_reminder(process_3039: ProcessDetail) -> None:
    bill = bill_of(process_3039, submission=consulted())
    fmt = MessageFormatter("ru")

    ahead = fmt.consultation_deadline(bill, today=dt.date(2026, 9, 28)).text
    last_day = fmt.consultation_deadline(bill, today=dt.date(2026, 9, 30)).text

    assert_telegram_html(ahead)
    assert "Консультации заканчиваются — druk nr 3039" in ahead
    assert "до 30 сент 2026 · осталось дней: 2" in ahead
    assert f'👉 <a href="{SURVEY}">мнение подаётся анкетой (ankieta) на сайте Сейма</a>' in ahead
    assert (
        f'🔗 <a href="{SURVEY}">анкета на сайте Сейма</a> | <a href="{CONSULTATION_PAGE}">' in ahead
    )
    assert "сегодня последний день" in last_day


def test_consultation_results_notice(process_3039: ProcessDetail) -> None:
    bill = bill_of(process_3039, submission=consulted(consultation_results=True))

    text = MessageFormatter("ru").consultation_results(bill, today=dt.date(2026, 10, 5)).text

    assert_telegram_html(text)
    assert text.startswith("🗣 <b>Опубликованы мнения из консультаций — druk nr 3039</b>")
    # The window shut — that is why this post exists — so it is not shown as a date range.
    assert "📅 <b>Общественные консультации:</b> завершились 30 сент 2026" in text
    assert f'<a href="{CONSULTATION_PAGE}">поданные анкеты' in text
    assert "⏭ <b>Что дальше:</b> I чтение в комиссии — ASW" in text
    assert (
        text.splitlines()[-1] == "#мнениявконсультациях #важность5 #легализация #kadencja10druk3039"
    )


def test_consultation_messages_need_a_consultation(process_3039: ProcessDetail) -> None:
    """The formatter raises seven different `ValueError`s over a bill that is missing something,
    so the message is what says the refusal is the right one: a bill with no consultation must
    not fail for want of an analysis and count as tested."""
    bill = bill_of(process_3039)

    with pytest.raises(ValueError, match="had no public consultation"):
        MessageFormatter("ru").consultation_results(bill)
    with pytest.raises(ValueError, match="has no consultation end date"):
        MessageFormatter("ru").consultation_deadline(bill, today=TODAY)


@pytest.mark.parametrize("language", ["ru", "en"])
def test_act_messages_link_the_texts_in_both_languages(
    process_3039: ProcessDetail, language: str
) -> None:
    bill = bill_of(process_3039, act=ACT)
    fmt = MessageFormatter(language)

    published = fmt.act_published(bill).text
    in_force = fmt.in_force(bill).text

    for text in (published, in_force):
        assert_telegram_html(text)
        assert "text.pdf" in text and "isap.sejm.gov.pl" in text


def test_act_notice_names_the_journal_the_date_and_the_staged_entry_caveat(
    process_3039: ProcessDetail,
) -> None:
    fmt = MessageFormatter("ru")

    text = fmt.act_published(bill_of(process_3039, act=ACT)).text
    no_date = fmt.act_published(
        bill_of(process_3039, act=ACT.model_copy(update={"entry_into_force": None}))
    ).text

    assert "📖 <b>Опубликован в Dziennik Ustaw — druk nr 3039</b>" in text
    assert "Dz.U. 2026 poz. 1099 (опубликован 18 авг 2026)" in text
    assert "Вступает в силу:</b> 19 нояб 2026" in text
    assert "Отдельные положения могут вступать в силу" in text
    assert "дата вступления в силу пока не указана" in no_date
    # The moment to diarise the date, and the card is months up the thread: one sentence of the
    # summary and what is left to do, like every other reply carries. What the Sejm passed is an
    # ustawa, so the heading says so.
    assert "<b>Суть закона:</b>" in text
    assert "остаётся подготовиться к вступлению в силу" in text


def test_in_force_reminder_needs_a_date(process_3039: ProcessDetail) -> None:
    undated = bill_of(process_3039, act=ACT.model_copy(update={"entry_into_force": None}))

    with pytest.raises(ValueError, match="has no entry-into-force date"):
        MessageFormatter("ru").in_force(undated)


def test_the_in_force_reminder_does_not_claim_today_when_a_run_was_missed(
    process_3039: ProcessDetail,
) -> None:
    late = bill_of(process_3039, act=act(entry_into_force=dt.date(2026, 9, 1)))
    due = bill_of(process_3039, act=act(entry_into_force=TODAY))
    fmt = MessageFormatter("ru", today=lambda: TODAY)

    assert fmt.in_force(late).text.startswith("⚖️ <b>Закон вступил в силу")
    assert fmt.in_force(due).text.startswith("⚖️ <b>С сегодняшнего дня действует")
    assert "Что можно сделать сейчас:</b> закон уже применяется" in fmt.in_force(due).text
