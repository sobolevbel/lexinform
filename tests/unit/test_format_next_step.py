"""«Что дальше» and «что можно сделать»: the step the bill stands before, the day it is dated
from, and the action the reader still has. No date is printed once it has passed."""

import datetime as dt

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import ProcessDetail, SenateAct, SenateCommittee, Stage
from tests.formatting import (
    ACT,
    COMMITTEE_PAGE,
    CONSULTATION_PAGE,
    PLENARY,
    SURVEY,
    TODAY,
    assert_telegram_html,
    bill_of,
    change_of,
    consulted,
    sitting,
)
from tests.harness import act


def test_card_links_the_consultation_form_and_names_the_next_step(
    process_3039: ProcessDetail,
) -> None:
    bill = bill_of(process_3039, submission=consulted())

    text = MessageFormatter("ru").new_bill(bill, None, today=TODAY).text

    assert_telegram_html(text)
    assert f'31.08.2026 — 30.09.2026 · <a href="{SURVEY}">анкета на сайте Сейма</a>' in text
    assert "⏭ <b>Что дальше:</b> I чтение в комиссии — ASW" in text  # name unknown: the code
    action = next(line for line in text.splitlines() if line.startswith("👉"))
    assert f'заполнить анкету (ankieta) <a href="{SURVEY}">на сайте Сейма</a> до' in action
    assert "до 30.09.2026" in action
    assert f"{COMMITTEE_PAGE}>ASW</a>" in action
    assert "до заседания" not in action  # nothing scheduled yet


def test_closed_consultation_keeps_the_page_but_drops_the_action(
    process_3039: ProcessDetail,
) -> None:
    bill = bill_of(process_3039, submission=consulted())

    text = MessageFormatter("ru").new_bill(bill, None, today=dt.date(2026, 10, 1)).text

    assert CONSULTATION_PAGE in text  # the opinions that were sent appear there
    assert SURVEY not in text  # nothing can be sent any more
    assert "заполнить анкету" not in text
    assert "направить мнение в комиссию —" in text


def test_next_step_carries_the_scheduled_committee_sitting(process_3039: ProcessDetail) -> None:
    bill = bill_of(process_3039, agenda=(sitting(),))

    text = MessageFormatter("ru").new_bill(bill, None, today=TODAY).text
    en = MessageFormatter("en").new_bill(bill, None, today=TODAY).text

    assert (
        "⏭ <b>Что дальше:</b> I чтение в комиссии — "
        "Komisja Administracji i Spraw Wewnętrznych (ASW) · 17.09.2026, 09:00" in text
    )
    assert "до заседания 17.09.2026" in text
    assert "⏭ <b>What comes next:</b> first reading in committee — Komisja" in en


def test_a_conditionally_announced_sitting_does_not_date_the_step_as_a_fact(
    process_3039: ProcessDetail,
) -> None:
    """The card's most-read line quoted the day of a sitting that happens only if the Sejm
    refers the bill to the committee first — 21 sittings of term 10 carry such a note. The
    agenda post under the card says what the condition is; the step line only marks it."""
    bill = bill_of(process_3039, agenda=(sitting(condition="first_reading_referral"),))

    text = MessageFormatter("ru").new_bill(bill, None, today=TODAY).text
    en = MessageFormatter("en").new_bill(bill, None, today=TODAY).text

    assert "· 17.09.2026, 09:00 (условно)" in text
    assert "(conditional)" in en


def test_past_sitting_is_ignored(process_3039: ProcessDetail) -> None:
    bill = bill_of(process_3039, agenda=(sitting(),))

    text = MessageFormatter("ru").new_bill(bill, None, today=dt.date(2026, 9, 18)).text

    assert "· 17.09.2026" not in text and "до заседания" not in text


def test_committee_phase_prefers_the_committee_sitting_over_the_plenary(
    process_3039: ProcessDetail,
) -> None:
    bill = bill_of(process_3039, agenda=(PLENARY, sitting()))

    text = MessageFormatter("ru").new_bill(bill, None, today=TODAY).text

    assert "· 17.09.2026, 09:00" in text


def test_late_phases_name_what_follows(process_1962: ProcessDetail) -> None:
    """The Sejm is done with druk 1962 and appended "Uchwalono"; the President has not had it yet,
    so the card must still name him — and not mark his step as taken."""
    passed = process_1962.model_copy(update={"passed": True})
    signed = passed.model_copy(
        update={
            "stages": (
                *passed.stages[:-1],
                Stage(
                    stage_name="Prezydent podpisał ustawę",
                    stage_type="PresidentSignature",
                    date=dt.date(2026, 9, 20),
                ),
                passed.stages[-1],
            )
        }
    )
    fmt = MessageFormatter("ru")

    with_president = fmt.new_bill(bill_of(passed), None, today=TODAY).text
    awaiting_publication = fmt.new_bill(bill_of(signed), None, today=TODAY).text
    awaiting_force = fmt.new_bill(bill_of(passed, act=ACT), None, today=TODAY).text
    in_force = fmt.new_bill(bill_of(passed, act=ACT), None, today=dt.date(2026, 11, 20)).text

    assert (
        "⏭ <b>Что дальше:</b> подпись Президента (до 21 дня), затем публикация в Dziennik Ustaw"
        in with_president
    )
    # A committee bill: no RCL step on its path.
    assert "Путь:</b> Сейм ✓ → комиссии ✓ → II и III чтение ✓ → Сенат ✓ → Президент ● → Dz.U." in (
        with_president
    )
    assert (
        "⏭ <b>Что дальше:</b> публикация в Dziennik Ustaw · обычно 1–4 недели после подписи"
        in awaiting_publication
    )
    # Nothing a reader can do: the card says so instead of staying silent.
    assert (
        "👉 <b>Что можно сделать сейчас:</b> пока ничего — ждём публикации" in awaiting_publication
    )
    assert "→ Президент ✓ → Dz.U. ● → в силе" in awaiting_publication
    assert "вступление в силу 19.11.2026" in awaiting_force
    assert "→ Dz.U. ✓ → в силе ●" in awaiting_force
    assert "Что дальше" not in in_force
    assert "→ Dz.U. ✓ → в силе ✓" in in_force  # the whole path is done


def test_path_and_usual_duration_for_a_deputies_bill_in_committee(
    process_3039: ProcessDetail,
) -> None:
    text = MessageFormatter("ru").new_bill(bill_of(process_3039), None, today=TODAY).text
    en = MessageFormatter("en").new_bill(bill_of(process_3039), None, today=TODAY).text

    # No RCL step: a deputies' bill never went through the government.
    assert (
        "🗺 <b>Путь:</b> Сейм ✓ → комиссии ● → II и III чтение → Сенат → Президент → Dz.U." in text
    )
    assert "Что дальше:</b> I чтение в комиссии — ASW · обычно 2–6 недель после поступления" in text
    assert "<b>Path:</b> Sejm ✓ → committees ● → 2nd and 3rd reading" in en


def test_senate_stage_invites_an_opinion_to_the_senate_committee(
    process_1962: ProcessDetail,
) -> None:
    # process_1962 already carries the Senate position; cut the stages back to the third reading.
    third_reading = next(
        i
        for i, st in enumerate(process_1962.stages)
        if st.stage_type == "SejmReading" and "III" in st.stage_name
    )
    in_senate = process_1962.model_copy(
        update={"stages": process_1962.stages[: third_reading + 1], "passed": True}
    )

    text = (
        MessageFormatter("ru").new_bill(bill_of(in_senate), None, today=dt.date(2026, 7, 20)).text
    )

    # The 30 days count from the third reading (17.07.2026): the date, not only the rule.
    assert "Что дальше:</b> рассмотрение в Сенате (до 30 дней) · решение до 16.08.2026" in text
    assert "Что можно сделать сейчас:</b> мнение можно будет направить в комиссию Сената" in text
    assert "→ Сенат ● → Президент" in text


def test_urgent_bill_gets_the_shortened_terms_and_not_the_usual_ones(
    process_3039: ProcessDetail, process_1962: ProcessDetail
) -> None:
    """A bill declared pilny (art. 123) runs on shorter terms: the Senate has 14 days instead of
    30, and the Sejm measured days where a normal bill takes weeks."""
    in_committee = process_3039.model_copy(update={"urgency_status": "URGENT"})
    third_reading = next(
        i
        for i, st in enumerate(process_1962.stages)
        if st.stage_type == "SejmReading" and "III" in st.stage_name
    )
    in_senate = process_1962.model_copy(
        update={
            "stages": process_1962.stages[: third_reading + 1],
            "passed": True,
            "urgency_status": "URGENT",
        }
    )
    fmt = MessageFormatter("ru")

    committee = fmt.new_bill(bill_of(in_committee), None, today=TODAY).text
    senate = fmt.new_bill(bill_of(in_senate), None, today=dt.date(2026, 7, 20)).text

    assert (
        "Что дальше:</b> I чтение в комиссии — ASW (срочный режим, tryb pilny)"
        " · обычно несколько дней после поступления" in committee
    )
    # 14 days from the third reading (17.07.2026), where a normal bill would get 30.
    assert (
        "Что дальше:</b> рассмотрение в Сенате (срочный режим: до 14 дней) · решение до 31.07.2026"
        in senate
    )


def test_public_hearing_names_the_application_deadline(process_3039: ProcessDetail) -> None:
    hearing = Stage(
        stage_name="Wysłuchanie publiczne",
        stage_type="PublicHearing",
        date=dt.date(2026, 9, 30),
    )
    stages = (*process_3039.stages, hearing)
    bill = bill_of(process_3039.model_copy(update={"stages": stages}))
    fmt = MessageFormatter("ru")

    update = fmt.status_update(bill, change_of("3039", [hearing]), today=TODAY).text
    reminder = fmt.hearing_deadline(bill, hearing, today=dt.date(2026, 9, 18)).text

    assert update.startswith("📢 <b>Назначены публичные слушания — druk nr 3039</b>")
    assert "• 30.09.2026: 📢 Публичные слушания (wysłuchanie publiczne)" in update
    assert "заявки на участие до 20.09.2026" in update
    assert "подать заявку на участие в публичных слушаниях до 20.09.2026" in update
    assert_telegram_html(reminder)
    assert "📢 <b>Заявки на публичные слушания — druk nr 3039</b>" in reminder
    assert (
        "слушания</b> 30.09.2026 · заявки на участие до <b>20.09.2026</b> · осталось дней: 2"
        in (reminder)
    )
    assert "#слушания #важность5 #легализация #kadencja10druk3039" in reminder


def test_public_hearing_reminder_uses_the_application_address_from_the_agenda(
    process_3039: ProcessDetail,
) -> None:
    hearing = Stage(
        stage_name="Wysłuchanie publiczne",
        stage_type="PublicHearing",
        date=dt.date(2026, 9, 30),
    )
    bill = bill_of(
        process_3039.model_copy(update={"stages": (*process_3039.stages, hearing)}),
        agenda=(sitting(apply_email="hearing@sejm.gov.pl", apply_by=dt.date(2026, 9, 20)),),
    )

    text = MessageFormatter("ru").hearing_deadline(bill, hearing, today=dt.date(2026, 9, 18)).text

    assert "Заявка на участие в слушании — на адрес hearing@sejm.gov.pl" in text


def test_withdrawn_bill_gets_no_next_step(process_3039: ProcessDetail) -> None:
    change = change_of("3039", [], withdrawn=True, closure_detected=True)
    taken_back = bill_of(process_3039, submission=consulted(status="WITHDRAWN"))

    text = MessageFormatter("ru").status_update(taken_back, change).text

    assert "Что дальше" not in text and "Проект отозван" in text


def test_an_entry_the_sejm_stopped_listing_is_not_called_withdrawn(
    process_3039: ProcessDetail,
) -> None:
    """The reconciler reads the end of such an entry off its age, not off a decision: `/bills`
    no longer lists it and no print number came in a year. An entry can wait months in the
    Marszałek's "freezer", so «Проект отозван» stated something the applicant may never have
    done — and the mark it left behind would then suppress a real closure."""
    change = change_of("3039", [], withdrawn=True, closure_detected=True)
    still_active = bill_of(process_3039, submission=consulted(status="ACTIVE"))

    text = MessageFormatter("ru").status_update(still_active, change).text

    assert "🏁 <b>Проект больше не отслеживается — druk nr 3039</b>" in text
    assert "Проект отозван" not in text
    assert "формального решения Сейм не публиковал" in text


def test_a_senate_term_that_has_run_out_moves_the_bill_to_the_president(
    process_1962: ProcessDetail,
) -> None:
    """Art. 121 ust. 2: the Senate saying nothing within its thirty days *is* an adoption, so
    «срок истёк» said the opposite of what had happened — and the card went on inviting an
    opinion to a committee that no longer had the act. Annotating the step was not enough either:
    «рассмотрение в Сенате (до 30 дней) · 30 дней Сената истекли» said both things in one line.
    The term is zawity, so the step has really moved on, and the wording says the Sejm has not
    recorded the hand-over."""
    third_reading = next(
        i
        for i, st in enumerate(process_1962.stages)
        if st.stage_type == "SejmReading" and "III" in st.stage_name
    )
    in_senate = process_1962.model_copy(
        update={"stages": process_1962.stages[: third_reading + 1], "passed": True}
    )

    text = MessageFormatter("ru").new_bill(bill_of(in_senate), None, today=TODAY).text

    assert "закон считается принятым в редакции Сейма и уходит к Президенту" in text
    assert "передача Президенту ещё не отмечена" in text
    assert "рассмотрение в Сенате" not in text
    assert "16.08.2026" not in text  # a date that is behind the reader promises nothing
    assert "комиссию Сената" not in text
    assert "пока ничего — закон у Президента" in text


def test_a_step_that_outlived_its_usual_duration_says_how_long(
    process_3039: ProcessDetail,
) -> None:
    """The card must not promise "usually 2–6 weeks" under a referral eighteen months old."""
    bill = bill_of(process_3039)

    fresh = MessageFormatter("ru").new_bill(bill, None, today=dt.date(2026, 9, 20)).text
    stale = MessageFormatter("ru").new_bill(bill, None, today=dt.date(2028, 3, 20)).text

    assert "обычно 2–6 недель после поступления" in fresh
    assert "обычно 2–6 недель" not in stale
    assert "без движения уже 18 мес." in stale


def test_a_vacatio_legis_is_a_date_to_diarise_and_never_a_step_standing_still(
    process_1962: ProcessDetail,
) -> None:
    """A staged entry into force a year out is common in the acts we follow, and it is the one
    stretch where the reader has a fixed date to prepare for. `PHASE_PATIENCE` has no entry for
    `in_force`, so the default 180 days applied and the card added «без движения уже 7 мес.» to a
    law that is published, final and dated — counted from the President's signature."""
    published = process_1962.model_copy(update={"eli": "DU/2026/1100"})
    bill = bill_of(published, act=act(entry_into_force=dt.date(2027, 7, 1)))

    text = MessageFormatter("ru").new_bill(bill, None, today=dt.date(2027, 3, 20)).text

    assert "Что дальше:</b> вступление в силу 01.07.2027" in text
    assert "без движения" not in text


def test_an_undated_stage_is_not_read_as_no_movement_since_the_bill_arrived(
    process_3039: ProcessDetail,
) -> None:
    """The API sometimes gives a stage without a date. That is an unknown day, not the day the
    bill was submitted — reading it as the latter ages the step by the whole life of the bill."""
    undated = process_3039.stages[:-1] + (
        process_3039.stages[-1].model_copy(update={"date": None, "children": ()}),
    )
    bill = bill_of(
        process_3039.model_copy(update={"stages": undated}),
        submission=consulted(date_of_receipt=dt.date(2025, 1, 10)),
    )

    text = MessageFormatter("ru").new_bill(bill, None, today=dt.date(2026, 9, 20)).text

    assert "без движения" not in text
    assert "обычно 2–6 недель" in text


def test_the_third_reading_is_not_dated_by_a_committee_sitting(
    process_1962: ProcessDetail,
) -> None:
    """A committee's 08:30 slot is not the date of a third reading in the Sejm."""
    second_reading = next(
        i
        for i, st in enumerate(process_1962.stages)
        if st.stage_type == "SejmReading" and st.stage_name.startswith("II ")
    )
    stages = list(process_1962.stages[: second_reading + 1])
    stages[-1] = stages[-1].model_copy(update={"decision": "przystąpiono do III czytania"})
    in_readings = process_1962.model_copy(update={"stages": tuple(stages), "passed": None})

    text = (
        MessageFormatter("ru")
        .new_bill(bill_of(in_readings, agenda=(sitting(),)), None, today=TODAY)
        .text
    )

    assert "III чтение и голосование в Сейме" in text
    assert "17.09.2026" not in text


def test_a_step_that_happens_in_neither_house_is_not_dated_by_a_sitting(
    process_1962: ProcessDetail,
) -> None:
    """The President's twenty-one days have no sitting of the Sejm to be dated by, and a print
    considered jointly with the bill keeps it on the plenary agenda while it waits: «подпись
    Президента · заседание Сейма № 65» used to replace the constitutional deadline itself."""
    plenary = sitting(
        kind="sejm",
        ref="sejm/65/2026-09-16",
        date=dt.date(2026, 9, 16),
        committee_code=None,
        committee_name=None,
        start_time=None,
        sitting_number=65,
    )
    passed = process_1962.model_copy(update={"passed": True})

    text = (
        MessageFormatter("ru").new_bill(bill_of(passed, agenda=(plenary,)), None, today=TODAY).text
    )

    assert "подпись Президента (до 21 дня)" in text
    assert "решение до 25.09.2026" in text and "заседание Сейма" not in text


def test_an_application_deadline_in_the_past_is_not_offered_as_an_action(
    process_3039: ProcessDetail,
) -> None:
    hearing = Stage(
        stage_name="Wysłuchanie publiczne",
        stage_type="PublicHearing",
        date=dt.date(2026, 9, 12),  # applications closed on 02.09
    )
    bill = bill_of(process_3039.model_copy(update={"stages": (*process_3039.stages, hearing)}))

    text = MessageFormatter("ru").new_bill(bill, None, today=TODAY).text

    assert "подать заявку на участие в публичных слушаниях" not in text


def test_a_sitting_today_is_not_a_deadline_to_write_before(process_3039: ProcessDetail) -> None:
    today = dt.date(2026, 9, 17)
    bill = bill_of(process_3039, agenda=(sitting(),))

    text = MessageFormatter("ru").new_bill(bill, None, today=today).text

    assert "направить мнение в комиссию" in text
    assert "до заседания" not in text


def test_the_senate_window_names_the_committee_its_email_and_the_act(
    process_1962: ProcessDetail,
) -> None:
    """The one window the reader has left, and the card used to link the list of every act."""
    third_reading = next(
        i
        for i, st in enumerate(process_1962.stages)
        if st.stage_type == "SejmReading" and "III" in st.stage_name
    )
    in_senate = process_1962.model_copy(
        update={"stages": process_1962.stages[: third_reading + 1], "passed": True}
    )

    act = SenateAct(
        url="https://www.senat.gov.pl/prace/proces-legislacyjny-w-senacie/ustawa,2100.html",
        title="Ustawa o cudzoziemcach",
        print_number="801",
        received=dt.date(2026, 7, 18),
        committees=(
            SenateCommittee(id=235, name="Komisja Samorządu", email="kstap@senat.gov.pl"),
            SenateCommittee(id=228, name="Komisja Praw Człowieka", email="kpcp@senat.gov.pl"),
        ),
        committee_sittings=(dt.date(2026, 7, 22),),
    )
    bill = bill_of(in_senate, senate=act)

    text = MessageFormatter("ru").new_bill(bill, None, today=dt.date(2026, 7, 20)).text

    assert (
        "направить мнение в комиссии Сената —"
        ' <a href="https://www.senat.gov.pl/prace/komisje-senackie/komisja,235.html">'
        "Komisja Samorządu</a> (kstap@senat.gov.pl),"
        ' <a href="https://www.senat.gov.pl/prace/komisje-senackie/komisja,228.html">'
        "Komisja Praw Człowieka</a> (kpcp@senat.gov.pl) до заседания 22.07.2026"
    ) in text
    assert "сенатского druk nr 801" in text
    assert f'<a href="{act.url}">закон на сайте Сената</a>' in text
    assert "до 16.08.2026" in text
