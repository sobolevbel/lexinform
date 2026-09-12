"""Messages about government projects followed on RCL: card, update, consultation replies."""

import datetime as dt

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import (
    RCL_STAGE_TYPE,
    AnalysisRecord,
    Bill,
    BillStatus,
    RclConsultation,
    RclProject,
    Stage,
    StatusChange,
    TextSource,
    process_summary,
    rcl_stages,
)
from tests.fakes import make_analysis
from tests.harness import CONSULTATION_LETTER, RCL_CONSULTATION, rcl_project, rcl_stage
from tests.unit.test_telegram_format import assert_telegram_html

NOW = dt.datetime(2026, 9, 7, tzinfo=dt.UTC)
TODAY = dt.date(2026, 9, 7)
COMMENT_FORM = "https://legislacja.rcl.gov.pl/projekt/12414100/komentarz"


def rcl_bill(project: RclProject, *, text_source: TextSource = "documents") -> Bill:
    record = AnalysisRecord(
        analysis=make_analysis(),
        model="m",
        prompt_version="v",
        input_chars=10,
        truncated=False,
        text_source=text_source,
        created_at=NOW,
    )
    return Bill(
        summary=process_summary(project, term=10),
        status=BillStatus.ANALYZED,
        stages=rcl_stages(project),
        analysis=record,
        rcl=project,
        first_seen_at=NOW,
        last_checked_at=NOW,
    )


def test_card_names_the_ministry_the_letter_deadline_and_both_ways_to_react() -> None:
    bill = rcl_bill(rcl_project())

    text = MessageFormatter("ru").new_bill(bill, None, today=TODAY).text

    assert_telegram_html(text)
    assert "Правительственный проект (RCL) — UC164" in text
    assert "Стадия:</b> сбор мнений министерств и партнёров (4. Opiniowanie)" in text
    assert "Путь:</b> план ✓ → RCL ● → Сейм → комиссии → II и III чтение → Сенат" in text
    assert (
        "Инициатор:</b> правительственный — Minister Spraw Wewnętrznych i Administracji"
        " · номер в wykazie prac RM: UC164\n📄 <b>Опубликован на RCL:</b> 31.08.2026" in text
    )
    assert (
        "Общественные консультации:</b> до 08.09.2026 (7 дн. с даты письма) · "
        "замечания на e-mail dep.prawny@mswia.gov.pl · "
        f'<a href="{CONSULTATION_LETTER.url}">письмо о консультациях</a>' in text
    )
    assert (
        "Что можно сделать сейчас:</b> направить замечания на dep.prawny@mswia.gov.pl до "
        "08.09.2026 (на польском, с номером UC164); "
        f'<a href="{COMMENT_FORM}">оставить комментарий через форму на RCL</a>' in text
    )
    assert "Что дальше:</b> консультации публичные до 08.09.2026, затем opiniowanie" in text
    assert '">Проект на RCL</a> | <a href="' in text
    assert ">Текст проекта (DOCX)</a> | <a href=" in text and ">OSR</a> | <a href=" in text
    assert ">Uzasadnienie</a> | <a href=" in text  # the legacy .doc is readable too
    assert ">Wykaz prac RM</a>" in text
    assert "#RCL_UC164" in text and "#RCL" in text and "#консультации" in text


def test_card_after_the_deadline_keeps_only_the_comment_form_and_says_what_follows() -> None:
    bill = rcl_bill(rcl_project())

    text = MessageFormatter("ru").new_bill(bill, None, today=dt.date(2026, 9, 20)).text

    # A closed deadline says so; the e-mail goes, the letter stays (it names the ministry).
    assert (
        "Общественные консультации:</b> завершились 08.09.2026 · "
        f'<a href="{CONSULTATION_LETTER.url}">письмо о консультациях</a>\n' in text
    )
    assert "dep.prawny@mswia.gov.pl" not in text
    assert "направить замечания" not in text
    assert f'Что можно сделать сейчас:</b> <a href="{COMMENT_FORM}">оставить комментарий' in text
    assert "Что дальше:</b> uzgodnienia и opiniowanie, затем комитеты" in text
    assert "#консультации" not in text


def test_card_without_a_readable_deadline_points_to_the_letter() -> None:
    project = rcl_project(
        consultation=RclConsultation(letter_url=CONSULTATION_LETTER.url, email="uwagi@mswia.gov.pl")
    )

    text = MessageFormatter("ru").new_bill(rcl_bill(project), None, today=TODAY).text

    assert "Общественные консультации:</b> срок указан в письме · замечания на e-mail" in text
    assert "направить замечания на uwagi@mswia.gov.pl (срок указан в письме) (на польском" in text


def test_card_from_metadata_only_explains_the_unreadable_text() -> None:
    project = rcl_project(consultation=None, stages=(rcl_stage(2, "Uzgodnienia", "active"),))

    text = (
        MessageFormatter("ru").new_bill(rcl_bill(project, text_source="metadata_only"), None).text
    )

    assert "Текст проекта на RCL не удалось прочитать — анализ по названию" in text
    assert "Текст проекта" not in text.split("🔗")[1]  # no document links without documents


def test_update_lists_the_new_stage_and_announces_the_hand_over_to_the_sejm() -> None:
    project = rcl_project(rm_number="RM-0610-139-26", consultation=None)
    bill = rcl_bill(project)
    sejm = Stage(
        stage_name="14. Skierowanie projektu ustawy do Sejmu",
        stage_type=RCL_STAGE_TYPE,
        date=dt.date(2026, 9, 2),
    )
    change = StatusChange(
        term=10,
        number=bill.number,
        old_fingerprint="a",
        new_fingerprint="b",
        new_stages=[sejm],
        detected_at=NOW,
    )

    text = MessageFormatter("ru").status_update(bill, change, today=TODAY).text

    assert_telegram_html(text)
    assert "🔢 <b>Проект направлен в Сейм — UC164</b>" in text
    assert "• 02.09.2026: <i>14. Skierowanie projektu ustawy do Sejmu</i>" in text
    assert "Проект направлен в Сейм — ждём номер druku" in text
    assert "Что дальше:</b> присвоение номера druku в Сейме, затем I чтение" in text
    assert ">Проект на RCL</a>" in text and "#RCL_UC164" in text


def test_consultation_reminder_and_results_point_to_the_ministry_and_the_project_page() -> None:
    bill = rcl_bill(rcl_project(consultation=RCL_CONSULTATION.model_copy(update={"positions": 3})))
    fmt = MessageFormatter("ru")

    reminder = fmt.consultation_deadline(bill, today=dt.date(2026, 9, 6)).text
    results = fmt.consultation_results(bill, today=dt.date(2026, 9, 20)).text

    assert_telegram_html(reminder)
    assert "Консультации заканчиваются — UC164" in reminder
    assert "до 08.09.2026 · осталось дней: 2" in reminder
    assert "👉 замечания на e-mail dep.prawny@mswia.gov.pl · <a href=" in reminder
    assert (
        '">письмо о консультациях</a> | <a href="https://legislacja.rcl.gov.pl/projekt/12414100">Проект на RCL</a>'
        in reminder
    )
    assert_telegram_html(results)
    assert "Опубликованы мнения из консультаций — UC164" in results
    assert 'projekt/12414100">поданные мнения (stanowiska) и ответ министерства' in results
