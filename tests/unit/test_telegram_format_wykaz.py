"""The card of a bill the government has only announced: it must not read like a bill's."""

import datetime as dt

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import (
    AnalysisRecord,
    Bill,
    BillStatus,
    StatusChange,
    WykazEntry,
    wykaz_fingerprint,
    wykaz_summary,
)
from tests.fakes import make_analysis
from tests.harness import wykaz_entry
from tests.unit.test_telegram_format import assert_telegram_html

NOW = dt.datetime(2026, 9, 7, tzinfo=dt.UTC)
TODAY = dt.date(2026, 9, 7)


def wykaz_bill(entry: WykazEntry | None = None, **overrides: object) -> Bill:
    entry = entry or wykaz_entry(**overrides)
    record = AnalysisRecord(
        analysis=make_analysis(),
        model="m",
        prompt_version="v",
        input_chars=10,
        truncated=False,
        text_source="metadata_only",
        created_at=NOW,
    )
    return Bill(
        summary=wykaz_summary(entry, term=10),
        status=BillStatus.ANALYZED,
        analysis=record,
        wykaz=entry,
        first_seen_at=NOW,
        last_checked_at=NOW,
    )


def test_the_card_says_it_is_a_plan_before_it_says_anything_the_model_wrote() -> None:
    text = MessageFormatter("ru").new_bill(wykaz_bill(), None, today=TODAY).text

    assert_telegram_html(text)
    assert "План правительства — UD408" in text
    intention = "Это пока только намерение: текста проекта ещё нет"
    assert intention in text
    # Above the summary, and in the part of the message that is never shrunk away.
    assert text.index(intention) < text.index("О чём")
    assert "Оценка по описанию из плана работ правительства" in text


def test_the_card_names_the_ministry_the_stage_and_the_planned_quarter() -> None:
    text = MessageFormatter("ru").new_bill(wykaz_bill(), None, today=TODAY).text

    assert "Стадия:</b> проект внесён в план работ правительства, текста ещё нет" in text
    assert (
        "Инициатор:</b> правительственный — MSWiA · номер в wykazie prac RM: UD408"
        "\n📄 <b>Внесён в план работ:</b> 01.09.2026" in text
    )
    assert "Путь:</b> план ● → RCL → Сейм → комиссии" in text
    assert "Что дальше:</b> публикация проекта на RCL" in text
    assert "принятие правительством: III кв. 2026" in text


def test_the_card_tells_the_reader_what_the_law_lets_them_do_at_this_stage() -> None:
    text = MessageFormatter("ru").new_bill(wykaz_bill(), None, today=TODAY).text

    assert "подать в MSWiA zgłoszenie zainteresowania pracami nad projektem" in text
    # Who may, and what it buys: both, or the reader has no reason to act.
    assert "Это может любой: гражданство и юридическое лицо не нужны" in text
    assert "какой интерес защищаете и какого решения добиваетесь" in text
    assert "участвовать в публичном слушании" in text


def test_the_card_links_the_entry_and_the_register_and_tags_the_thread_once() -> None:
    text = MessageFormatter("ru").new_bill(wykaz_bill(), None, today=TODAY).text

    assert "Запись в плане работ</a>" in text
    assert "https://www.gov.pl/web/premier/wplip-rm" in text
    # The same tag the RCL card and the druk will carry: one search, one thread.
    assert text.count("#RCL_UD408") == 1
    # Latin, like `#RCL` and like "wykaz prac RM" in the card's own text: a Cyrillic "РМ" would
    # look the same and be a different string.
    assert "#wykazRM" in text
    assert "планРМ" not in text


def test_a_quarter_that_cannot_be_read_falls_back_to_the_usual_duration() -> None:
    entry = wykaz_entry(planned_adoption="niezwłocznie po uzgodnieniach")

    text = MessageFormatter("ru").new_bill(wykaz_bill(entry), None, today=TODAY).text

    assert "принятие правительством" not in text
    assert "обычно 1–6 месяцев до публикации проекта" in text


def test_the_adoption_note_in_the_field_never_reaches_the_card() -> None:
    realised = "IV kwartał 2026 r. - ZREALIZOWANY Rada Ministrów przyjęła 6 maja 2025 r."

    bill = wykaz_bill(planned_adoption=realised)

    text = MessageFormatter("ru").new_bill(bill, None, today=TODAY).text

    assert "ZREALIZOWANY" not in text
    assert "принятие правительством: IV кв. 2026" in text


def test_a_quarter_that_has_already_passed_is_not_offered_as_a_plan() -> None:
    entry = wykaz_entry(planned_adoption="II kwartał 2025 r.")

    text = MessageFormatter("ru").new_bill(wykaz_bill(entry), None, today=TODAY).text

    assert "принятие правительством" not in text


def test_a_withdrawal_is_not_told_as_a_rejection_by_the_sejm() -> None:
    entry = wykaz_entry(status="Wycofany", resignation="Wykreślenie projektu z wykazu.")
    bill = wykaz_bill(entry)
    change = StatusChange(
        term=10,
        number=bill.number,
        old_fingerprint="a",
        new_fingerprint=wykaz_fingerprint(entry),
        new_stages=[],
        closure_detected=True,
        passed=False,
        detected_at=NOW,
    )

    text = MessageFormatter("ru").status_update(bill, change).text

    assert_telegram_html(text)
    assert "Правительство отказалось от проекта" in text
    assert "Сейм" not in text.split("#")[0].split("Что дальше")[0].replace("в Сейме", "")


def test_a_plan_card_does_not_claim_an_entry_into_force() -> None:
    """Two blocks above, the card says there is no text yet; «Вступление в силу: не указано»
    reads as a fact about the bill rather than the absence of one."""
    text = MessageFormatter("ru").new_bill(wykaz_bill(), None, today=TODAY).text

    assert "текста проекта ещё нет" in text
    assert "Вступление в силу" not in text
