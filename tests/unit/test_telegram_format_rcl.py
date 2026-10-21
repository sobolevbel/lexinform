"""Messages about government projects followed on RCL: card, update, consultation replies."""

import datetime as dt

import pytest

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import (
    RCL_STAGE_TYPE,
    AnalysisRecord,
    ApplicantType,
    Bill,
    BillStatus,
    BillSubmission,
    RclConsultation,
    RclProject,
    Stage,
    StatusChange,
    TextSource,
    next_phase,
    process_summary,
    rcl_stages,
)
from tests.fakes import make_analysis
from tests.formatting import assert_telegram_html
from tests.harness import CONSULTATION_LETTER, RCL_CONSULTATION, rcl_project, rcl_stage

NOW = dt.datetime(2026, 9, 7, tzinfo=dt.UTC)
TODAY = dt.date(2026, 9, 7)
COMMENT_FORM = "https://legislacja.rcl.gov.pl/projekt/12414100/komentarz"


@pytest.mark.parametrize("language", ["ru", "en"])
def test_legal_review_names_the_next_step_without_repeating_closed_consultations(
    language: str,
) -> None:
    project = rcl_project(stages=(rcl_stage(10, "Komisja Prawnicza", "active"),))
    bill = rcl_bill(project)
    today = dt.date(2026, 10, 20)
    change = StatusChange(
        term=bill.term,
        number=bill.number,
        old_fingerprint="old",
        new_fingerprint="legal-review",
        new_stages=list(bill.stages),
        detected_at=NOW,
    )

    phase = next_phase(bill, today=today)
    formatter = MessageFormatter(language)
    text = formatter.status_update(bill, change, today=today).text
    card = formatter.new_bill(bill, None, today=today).text

    assert phase is not None and phase.key == "rcl_legal_commission"
    assert_telegram_html(text)
    assert "Stały Komitet Rady Ministrów" in text
    assert "1–3" not in text and "ePUAP" not in text
    if language == "ru":
        assert "Общественные консультации" not in text
        assert "новое окно участия не подтверждено" in text
        assert "Общественные консультации" in card
    else:
        assert "Public consultation" not in text
        assert "no new participation window is confirmed" in text


def test_new_text_during_legal_review_leads_with_the_substantive_event() -> None:
    project = rcl_project(stages=(rcl_stage(10, "Komisja Prawnicza", "active"),))
    bill = rcl_bill(project)
    change = StatusChange(
        term=bill.term,
        number=bill.number,
        old_fingerprint="old",
        new_fingerprint="new-text",
        new_stages=list(bill.stages),
        content_changed=True,
        detected_at=NOW,
    )

    text = MessageFormatter("ru").status_update(bill, change, today=TODAY).text

    assert "<b>Новая версия текста — UC164</b>" in text


@pytest.mark.parametrize("language", ["ru", "en"])
@pytest.mark.parametrize("extension", ["zip", "DOCX", "pdf"])
def test_new_text_link_names_its_actual_format(language: str, extension: str) -> None:
    bill = rcl_bill(rcl_project())
    assert bill.analysis is not None
    url = f"https://legislacja.rcl.gov.pl/docs/dokument801844.{extension}?download=1"
    bill = bill.model_copy(
        update={"analysis": bill.analysis.model_copy(update={"source_url": url})}
    )
    change = StatusChange(
        term=bill.term,
        number=bill.number,
        old_fingerprint="old",
        new_fingerprint="new",
        new_stages=[],
        content_changed=True,
        detected_at=NOW,
    )

    text = MessageFormatter(language).status_update(bill, change, today=TODAY).text

    assert_telegram_html(text)
    assert f'href="{url}"' in text
    if extension.lower() == "pdf":
        label = "PDF" if language == "ru" else "Print PDF"
        assert f">{label}</a>" in text
    else:
        assert f"({extension.upper()})</a>" in text and ">PDF</a>" not in text


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


def test_the_project_that_continues_a_plan_is_tagged_once_and_by_the_wykaz_number() -> None:
    bill = rcl_bill(rcl_project()).model_copy(
        update={"linked_number": "WPL/UC164", "linked_wykaz_number": "UC164"}
    )

    text = MessageFormatter("ru").new_bill(bill, None, today=TODAY).text

    # The plan and the project are one thread, so both rows resolve to the same tag.
    assert text.count("#UC164") == 1
    assert "#RCL_" not in text


def test_card_names_the_ministry_the_letter_deadline_and_both_ways_to_react() -> None:
    bill = rcl_bill(rcl_project())

    text = MessageFormatter("ru").new_bill(bill, None, today=TODAY).text

    assert_telegram_html(text)
    assert "Правительственный проект (RCL) — UC164" in text
    assert "Стадия:</b> сбор мнений министерств и партнёров (4. Opiniowanie)" in text
    assert "Путь:</b> план ✓ → RCL ● → Сейм → комиссии → II и III чтение → Сенат" in text
    assert (
        "Инициатор:</b> правительственный — Minister Spraw Wewnętrznych i Administracji"
        " · номер в wykazie prac RM: UC164\n📄 <b>Опубликован на RCL:</b> 31 авг 2026" in text
    )
    assert (
        "Общественные консультации:</b> до 8 сент 2026 (7 дн. с даты письма) · "
        "замечания на e-mail dep.prawny@mswia.gov.pl · "
        f'<a href="{CONSULTATION_LETTER.url}">письмо о консультациях</a>' in text
    )
    assert (
        "Что можно сделать сейчас:</b> направить замечания на dep.prawny@mswia.gov.pl до "
        "8 сент 2026 (на польском, с номером UC164); "
        f'<a href="{COMMENT_FORM}">оставить комментарий через форму на RCL</a>' in text
    )
    # RCL names the minister, the table the ministry: the zgłoszenie still gets its address.
    assert (
        "письмом органу, который готовит проект: Minister Spraw Wewnętrznych i Administracji · "
        '<a href="https://www.gov.pl/web/mswia/kontakt">адреса канцелярии, ePUAP и'
        " e-Doręczenia</a>" in text
    )
    assert "Что дальше:</b> общественные консультации до 8 сент 2026, затем сбор мнений" in text
    assert '">Проект на RCL</a> | <a href="' in text
    assert ">Текст проекта (DOCX)</a> | <a href=" in text and ">OSR</a> | <a href=" in text
    assert ">Uzasadnienie</a> | <a href=" in text  # the legacy .doc is readable too
    assert ">Wykaz prac RM</a>" in text
    assert "#UC164" in text and "#RCL" in text and "#консультации" in text


def test_card_after_the_deadline_keeps_only_the_comment_form_and_says_what_follows() -> None:
    bill = rcl_bill(rcl_project())

    text = MessageFormatter("ru").new_bill(bill, None, today=dt.date(2026, 9, 20)).text

    # A closed deadline says so; the e-mail goes, the letter stays (it names the ministry).
    assert (
        "Общественные консультации:</b> завершились 8 сент 2026 · "
        f'<a href="{CONSULTATION_LETTER.url}">письмо о консультациях</a>\n' in text
    )
    assert "dep.prawny@mswia.gov.pl" not in text
    assert "направить замечания" not in text
    # The comment form and the zgłoszenie outlive the consultation, but they are what is left,
    # not what the window was: offering them with the same weight read as an open invitation.
    assert "Что можно сделать сейчас:</b> публичные консультации уже закрыты, но пока проект" in (
        text
    )
    assert f'остаётся: <a href="{COMMENT_FORM}">оставить комментарий' in text
    assert "Что дальше:</b> межведомственные согласования и сбор мнений, затем комитеты" in text
    assert "#консультации" not in text


def test_card_without_a_readable_deadline_points_to_the_letter() -> None:
    """The letter carried an electronic time stamp instead of a date and no relative term, so the
    timeline is what says the consultation is running: here it is, the project standing on the
    stage itself."""
    stages = tuple(
        st.model_copy(update={"state": "active" if st.number == 3 else "not_started"})
        if st.number in (3, 4)
        else st
        for st in rcl_project().stages
    )
    project = rcl_project(
        stages=stages,
        consultation=RclConsultation(
            letter_url=CONSULTATION_LETTER.url, email="uwagi@mswia.gov.pl"
        ),
    )

    text = MessageFormatter("ru").new_bill(rcl_bill(project), None, today=TODAY).text

    assert "Общественные консультации:</b> срок указан в письме · замечания на e-mail" in text
    assert "направить замечания на uwagi@mswia.gov.pl (срок указан в письме) (на польском" in text


def test_an_undated_consultation_the_project_has_moved_past_stops_inviting() -> None:
    """Same letter, but the timeline has the project on "Opiniowanie": the window is behind it,
    and there is no date to print. Saying «срок указан в письме» there invited an opinion into a
    stage that had closed, and the e-mail sat under it in the action line."""
    project = rcl_project(
        consultation=RclConsultation(letter_url=CONSULTATION_LETTER.url, email="uwagi@mswia.gov.pl")
    )

    text = MessageFormatter("ru").new_bill(rcl_bill(project), None, today=TODAY).text

    assert "Общественные консультации:</b> завершились — срок был указан в письме" in text
    assert "направить замечания" not in text
    assert "#консультации" not in text


def test_card_from_metadata_only_explains_the_unreadable_text() -> None:
    project = rcl_project(consultation=None, stages=(rcl_stage(2, "Uzgodnienia", "active"),))

    text = (
        MessageFormatter("ru").new_bill(rcl_bill(project, text_source="metadata_only"), None).text
    )

    assert "Текст проекта на RCL не удалось прочитать — анализ по названию" in text
    assert "Текст проекта" not in text.split("🔗")[1]  # no document links without documents


def _hand_over() -> StatusChange:
    sejm = Stage(
        stage_name="14. Skierowanie projektu ustawy do Sejmu",
        stage_type=RCL_STAGE_TYPE,
        date=dt.date(2026, 9, 2),
    )
    return StatusChange(
        term=10,
        number=RCL_NUMBER,
        old_fingerprint="a",
        new_fingerprint="b",
        new_stages=[sejm],
        detected_at=NOW,
    )


RCL_NUMBER = "RCL/12414100"
GOVERNMENT_ENTRY = BillSubmission(
    term=10,
    number="RPW/35455/2026",
    title="Rządowy projekt ustawy o zmianie ustawy o udziale RP w SIS i VIS",
    applicant=ApplicantType.GOVERNMENT,
    date_of_receipt=dt.date(2026, 9, 3),
    senders_number="RM-0610-139-26",
)


def test_update_lists_the_new_stage_and_announces_the_hand_over_to_the_sejm() -> None:
    project = rcl_project(rm_number="RM-0610-139-26", consultation=RCL_CONSULTATION)
    bill = rcl_bill(project)

    text = MessageFormatter("ru").status_update(bill, _hand_over(), today=dt.date(2026, 9, 10)).text

    assert_telegram_html(text)
    assert "🔢 <b>Проект направлен в Сейм — UC164</b>" in text
    assert "• 2 сент 2026: <i>14. Skierowanie projektu ustawy do Sejmu</i>" in text
    assert text.count("Проект направлен в Сейм") == 1  # the header, not a line repeating it
    assert "Общественные консультации" not in text  # closed on 8 Sept: the card's history
    assert "Что дальше:</b> присвоение номера druku в Сейме, затем I чтение" in text
    assert ">Проект на RCL</a>" in text and "#UC164" in text


def test_the_hand_over_names_the_sejms_entry_and_its_file_once_the_sejm_lists_it() -> None:
    bill = rcl_bill(rcl_project(rm_number="RM-0610-139-26", consultation=None)).model_copy(
        update={"submission": GOVERNMENT_ENTRY}
    )
    fmt = MessageFormatter("ru")

    text = fmt.status_update(bill, _hand_over(), today=TODAY).text
    card = fmt.new_bill(bill, None, today=TODAY).text

    assert_telegram_html(text)
    received = "📄 Сейм получил проект 3 сент 2026: <b>RPW/35455/2026</b>"
    assert received in text and received in card
    pdf = f'<a href="{GOVERNMENT_ENTRY.pdf_url}">PDF проекта (сайт Сейма)</a>'
    assert pdf in text and pdf in card


def test_consultation_reminder_and_results_point_to_the_ministry_and_the_project_page() -> None:
    bill = rcl_bill(rcl_project(consultation=RCL_CONSULTATION.model_copy(update={"positions": 3})))
    fmt = MessageFormatter("ru")

    reminder = fmt.consultation_deadline(bill, today=dt.date(2026, 9, 6)).text
    results = fmt.consultation_results(bill, today=dt.date(2026, 9, 20)).text

    assert_telegram_html(reminder)
    assert "Консультации заканчиваются — UC164" in reminder
    assert "до 8 сент 2026 · осталось дней: 2" in reminder
    assert "👉 замечания на e-mail dep.prawny@mswia.gov.pl · <a href=" in reminder
    assert (
        '">письмо о консультациях</a> | <a href="https://legislacja.rcl.gov.pl/projekt/12414100">Проект на RCL</a>'
        in reminder
    )
    assert_telegram_html(results)
    assert "Опубликованы мнения из консультаций — UC164" in results
    assert 'projekt/12414100">поданные мнения (stanowiska) и ответ министерства' in results


def _consulting_now(**consultation: object) -> RclProject:
    """The project standing on its "Konsultacje publiczne" stage, as the timeline shows it."""
    stages = tuple(
        st.model_copy(update={"state": "active" if st.number == 3 else "not_started"})
        if st.number in (3, 4)
        else st
        for st in rcl_project().stages
    )
    return rcl_project(stages=stages, consultation=RclConsultation(**consultation))


def test_a_consultation_whose_letter_gave_no_date_is_still_a_consultation() -> None:
    """RCL letters sometimes carry an electronic time stamp instead of a date and no relative
    term either, so `ConsultationWindow.is_open` — which needs an end date — said no to every
    question at once: the phase skipped to «сбор мнений», the card lost its #консультации tag,
    and the action line went on inviting an e-mail to the ministry three lines below. The
    timeline is what answers when the letter does not."""
    bill = rcl_bill(
        _consulting_now(letter_url=CONSULTATION_LETTER.url, email="dep.prawny@mswia.gov.pl")
    )

    text = MessageFormatter("ru").new_bill(bill, None, today=TODAY).text

    assert_telegram_html(text)
    assert "Что дальше:</b> общественные консультации (срок указан в письме)" in text
    assert "межведомственные согласования и сбор мнений" not in text
    assert "направить замечания на dep.prawny@mswia.gov.pl (срок указан в письме)" in text
    assert "#консультации" in text


def test_the_consultation_reminder_says_how_to_write_as_the_card_does() -> None:
    """This is the reply a reader acts on — three days before the window shuts, with the card
    weeks up the thread — and it used to give the address without the two things that make a
    comment land: the language and the number to quote."""
    bill = rcl_bill(rcl_project())

    text = MessageFormatter("ru").consultation_deadline(bill, today=dt.date(2026, 9, 5)).text

    assert_telegram_html(text)
    assert "замечания на e-mail dep.prawny@mswia.gov.pl" in text
    assert "(на польском, с номером UC164)" in text
