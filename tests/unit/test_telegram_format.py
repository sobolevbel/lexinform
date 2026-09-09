"""Rendering of every message kind: content, HTML safety, the 4096-character limit."""

import datetime as dt
from html.parser import HTMLParser
from typing import Any

import pytest

from lexinform.adapters.llm_prompts import PROMPT_VERSION
from lexinform.adapters.telegram_format import MESSAGE_LIMIT, MessageFormatter, fit, shrink_block
from lexinform.models import (
    ActInfo,
    AgendaItem,
    Analysis,
    AnalysisRecord,
    AnalysisVerdict,
    Bill,
    BillStatus,
    BillSubmission,
    ClubVotes,
    PrintInfo,
    ProcessDetail,
    RunReport,
    Stage,
    StatusChange,
    TokenUsage,
    flatten_stages,
)
from tests.fakes import make_analysis

NOW = dt.datetime(2026, 9, 7, tzinfo=dt.UTC)
TODAY = dt.date(2026, 9, 9)
CONSULTATION_PAGE = (
    "https://www.sejm.gov.pl/Sejm10.nsf/agent.xsp?symbol=KONSULTOWANY_PROJEKT"
    "&amp;NrProjektu=RPW/29075/2026"
)
COMMITTEE_PAGE = 'KodKom=ASW"'


class _TagChecker(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.balanced = True
        self.tags: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.stack.append(tag)
        self.tags.add(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack or self.stack.pop() != tag:
            self.balanced = False


def assert_telegram_html(text: str) -> None:
    """Balanced tags from Telegram's allowed subset, within the message limit."""
    checker = _TagChecker()
    checker.feed(text)
    assert checker.balanced, text
    assert checker.tags <= {"b", "i", "a", "code", "pre"}
    assert len(text) <= MESSAGE_LIMIT


def bill_of(
    process: ProcessDetail,
    analysis: Analysis | None = None,
    *,
    truncated: bool = False,
    **fields: Any,
) -> Bill:
    record = AnalysisRecord(
        analysis=analysis or make_analysis(),
        model="m",
        prompt_version=PROMPT_VERSION,
        input_chars=10,
        truncated=truncated,
        text_source="pdf",
        created_at=NOW,
    )
    return Bill(
        summary=process,
        status=BillStatus.ANALYZED,
        stages=process.stages,
        analysis=record,
        first_seen_at=NOW,
        last_checked_at=NOW,
        **fields,
    )


def change_of(number: str, new_stages: list[Stage], **fields: Any) -> StatusChange:
    return StatusChange(
        term=10,
        number=number,
        old_fingerprint="a",
        new_fingerprint="b",
        new_stages=new_stages,
        detected_at=NOW,
        **fields,
    )


def consulted(**overrides: Any) -> BillSubmission:
    fields: dict[str, Any] = dict(
        term=10,
        number="RPW/29075/2026",
        title="t",
        date_of_receipt=dt.date(2026, 8, 31),
        public_consultation=True,
        consultation_start=dt.date(2026, 8, 31),
        consultation_end=dt.date(2026, 9, 30),
    )
    fields.update(overrides)
    return BillSubmission(**fields)


def sitting(**overrides: Any) -> AgendaItem:
    fields: dict[str, Any] = dict(
        kind="committee",
        ref="ASW/136/2026-09-17",
        date=dt.date(2026, 9, 17),
        start_time=dt.time(9, 0),
        committee_code="ASW",
        committee_name="Komisja Administracji i Spraw Wewnętrznych",
        sitting_number=136,
        room="sala nr 412",
        text="Pierwsze czytanie projektu (druk nr 3039) – uzasadnia poseł X.",
        video_url="https://sejm.gov.pl/Sejm10.nsf/transmisje_arch.xsp?unid=1",
    )
    fields.update(overrides)
    return AgendaItem(**fields)


PLENARY = sitting(
    kind="sejm",
    ref="sejm/65/2026-09-15",
    date=dt.date(2026, 9, 15),
    end_date=dt.date(2026, 9, 18),
    start_time=None,
    committee_code=None,
    committee_name=None,
    sitting_number=65,
    room=None,
    video_url=None,
    text="Sprawozdanie Komisji (druki nr 3039 i 3055) - sprawozdawca poseł Y.",
)
ACT = ActInfo(
    eli="DU/2026/1099",
    display_address="Dz.U. 2026 poz. 1099",
    title="Ustawa z dnia 17 lipca 2026 r. o zmianie ustawy",
    promulgation_date=dt.date(2026, 8, 18),
    entry_into_force=dt.date(2026, 11, 19),
    text_pdf_url="https://api.sejm.gov.pl/eli/acts/DU/2026/1099/text.pdf",
    isap_url="https://isap.sejm.gov.pl/isap.nsf/DocDetails.xsp?id=WDU20260001099",
    fetched_at=dt.datetime(2026, 8, 20, tzinfo=dt.UTC),
)

# --------------------------------------------------------------------------- the card


def test_card_contains_every_section(process_3039: ProcessDetail, print_3039: PrintInfo) -> None:
    text = MessageFormatter("ru").new_bill(bill_of(process_3039), print_3039).text

    assert_telegram_html(text)
    assert text.startswith(
        f"📜 <b>Новый законопроект — druk nr 3039</b>\n\n<b>{process_3039.title}"
    )
    assert "🔴 <b>Важность:</b> ●●●●● 5/5" in text
    assert "О чём проект" in text and "Ключевые изменения" in text
    assert "Стадия:</b> Skierowanie" in text
    assert "PrzebiegProc.xsp?nr=3039" in text and "prints/3039/3039.pdf" in text
    assert "#kadencja10druk3039 #важность5 #легализация #каденция10" in text


def test_card_escapes_model_output_and_notes_partial_text(
    process_3039: ProcessDetail, print_3039: PrintInfo
) -> None:
    analysis = make_analysis()
    analysis.summary = "Ссылка <b>жирный</b> & амперсанд"

    text = (
        MessageFormatter("ru")
        .new_bill(bill_of(process_3039, analysis, truncated=True), print_3039)
        .text
    )

    assert_telegram_html(text)
    assert "&lt;b&gt;жирный&lt;/b&gt; &amp; амперсанд" in text
    assert "неполном тексте" in text


@pytest.mark.parametrize(
    ("n_changes", "change_len", "summary_len"), [(12, 400, 3000), (30, 200, 500), (1, 10, 20000)]
)
def test_card_shrinks_the_flexible_blocks_to_the_limit(
    process_3039: ProcessDetail,
    print_3039: PrintInfo,
    n_changes: int,
    change_len: int,
    summary_len: int,
) -> None:
    analysis = make_analysis()
    analysis.summary = "с" * summary_len
    analysis.key_changes = ["и" * change_len] * n_changes

    text = MessageFormatter("ru").new_bill(bill_of(process_3039, analysis), print_3039).text

    assert_telegram_html(text)
    assert "PrzebiegProc.xsp?nr=3039" in text and "#kadencja10druk3039" in text  # fixed parts


def test_card_groups_short_facts_and_separates_paragraphs(
    process_3039: ProcessDetail, print_3039: PrintInfo
) -> None:
    text = MessageFormatter("ru").new_bill(bill_of(process_3039), print_3039).text

    practical = text.index("Что это значит на практике")
    affected = text.index("Кого касается")
    effective = text.index("Вступление в силу")
    stage = text.index("Стадия:")
    applicant = text.index("Инициатор:")
    assert "\n\n" in text[practical:affected]
    assert "\n\n" in text[affected:effective]
    assert "\n\n" in text[effective:stage]
    assert "\n\n" not in text[stage:applicant]  # stage and applicant stay on adjacent lines


def test_labels_and_date_format_follow_the_language(
    process_3039: ProcessDetail, print_3039: PrintInfo
) -> None:
    ru = MessageFormatter("ru").new_bill(bill_of(process_3039), print_3039).text
    en = MessageFormatter("en").new_bill(bill_of(process_3039), print_3039).text

    assert "Дата druku:</b> 03.08.2026" in ru
    assert "New bill" in en and "#importance5" in en
    assert "Print date:</b> 2026-08-03" in en


def test_card_tags_open_consultations_and_ukraine(process_3039: ProcessDetail) -> None:
    bill = bill_of(
        process_3039,
        submission=consulted(number="RPW/1/2026", consultation_end=dt.date(2026, 9, 20)),
        prefilter_hits=["cudzoziemcy", "text:obywatele_ukrainy"],
    )

    tags = (
        MessageFormatter("ru")
        .new_bill(bill, None, today=dt.date(2026, 9, 10))
        .text.splitlines()[-1]
    )

    assert tags == "#kadencja10druk3039 #важность5 #легализация #консультации #Украина #каденция10"


def test_closed_consultation_loses_its_tag_and_ukraine_is_read_from_the_title(
    process_3039: ProcessDetail,
) -> None:
    ukraine = process_3039.model_copy(
        update={"title": "Rządowy projekt ustawy o pomocy obywatelom Ukrainy"}
    )
    bill = bill_of(
        ukraine, submission=consulted(number="RPW/1/2026", consultation_end=dt.date(2026, 9, 20))
    )

    tags = (
        MessageFormatter("ru")
        .new_bill(bill, None, today=dt.date(2026, 9, 21))
        .text.splitlines()[-1]
    )

    assert tags == "#kadencja10druk3039 #важность5 #легализация #Украина #каденция10"


# --------------------------------------------------------------------------- status updates


def test_status_update_lists_new_stages_and_the_closure(process_1962: ProcessDetail) -> None:
    bill = bill_of(process_1962, make_analysis(score=3))
    change = change_of(
        "1962", flatten_stages(process_1962.stages)[-3:], closure_detected=True, passed=True
    )

    text = MessageFormatter("ru").status_update(bill, change).text

    assert_telegram_html(text)
    assert "Обновление — druk nr 1962" in text
    assert "Новые стадии" in text and "Uchwalono" in text
    assert "закон принят" in text
    assert "#kadencja10druk1962" in text


def test_status_update_after_a_re_analysis_shows_the_diff(process_3039: ProcessDetail) -> None:
    analysis = make_analysis(score=4)
    analysis.changes_since_previous = ["Срок подачи сокращён", "Добавлена категория студентов"]
    change = change_of("3039", [], content_changed=True)

    text = MessageFormatter("ru").status_update(bill_of(process_3039, analysis), change).text

    assert_telegram_html(text)
    assert "🟠 ●●●●○ 4/5" in text
    assert "Суть проекта" in text
    assert "🆕 <b>Что изменилось с прошлого раза</b>" in text and "Срок подачи сокращён" in text
    assert "Новые стадии" not in text


def test_status_update_tags_name_the_events(process_1962: ProcessDetail) -> None:
    bill = bill_of(process_1962)
    flat = flatten_stages(process_1962.stages)
    voting = next(st for st in flat if st.stage_type == "Voting")
    senate = flat[0].model_copy(update={"stage_type": "SenatePosition"})
    referral = flat[0].model_copy(update={"stage_type": "Referral"})
    fmt = MessageFormatter("ru")

    eventful = fmt.status_update(bill, change_of("1962", [voting, senate], content_changed=True))
    plain = fmt.status_update(bill, change_of("1962", [referral]))
    withdrawn = fmt.status_update(
        bill, change_of("1962", [], withdrawn=True, closure_detected=True)
    )

    assert eventful.text.splitlines()[-1] == "#голосование #сенат #поправки #kadencja10druk1962"
    assert plain.text.splitlines()[-1] == "#kadencja10druk1962"
    assert "#отозван #kadencja10druk1962" in withdrawn.text


def test_senate_position_is_rendered_from_its_position_field(process_1962: ProcessDetail) -> None:
    senate = next(
        s for s in flatten_stages(process_1962.stages) if s.stage_type == "SenatePosition"
    )
    odd = senate.model_copy(update={"position": "odroczył rozpatrywanie"})
    fmt = MessageFormatter("ru")

    known = fmt.status_update(bill_of(process_1962), change_of("1962", [senate])).text
    unknown = fmt.status_update(bill_of(process_1962), change_of("1962", [odd])).text

    assert "Сенат внёс поправки" in known and "(druk 2994)" in known
    assert "odroczył rozpatrywanie" in unknown  # unknown positions pass through verbatim


def test_voting_stage_shows_totals_pdf_link_and_club_breakdown(
    process_1962: ProcessDetail,
) -> None:
    voting = next(s for s in flatten_stages(process_1962.stages) if s.stage_type == "Voting")
    assert voting.voting is not None
    clubs = (
        ClubVotes(club="KO", yes=152),
        ClubVotes(club="PSL-TD", yes=31),
        ClubVotes(club="Lewica", yes=21),
        ClubVotes(club="Polska2050", yes=13),
        ClubVotes(club="Centrum", yes=12),
        ClubVotes(club="PiS", abstain=178, absent=6),
        ClubVotes(club="Konfederacja", no=1),
    )
    enriched = voting.model_copy(
        update={"voting": voting.voting.model_copy(update={"clubs": clubs})}
    )
    fmt = MessageFormatter("ru")

    totals_only = fmt.status_update(bill_of(process_1962), change_of("1962", [voting])).text
    with_clubs = fmt.status_update(bill_of(process_1962), change_of("1962", [enriched])).text

    assert "239 за, 1 против, 199 воздержались" in totals_only
    assert "votings/62/108/pdf" in totals_only and "За: KO" not in totals_only
    assert "За: KO 152, PSL-TD 31, Lewica 21, Polska2050 13, …" in with_clubs
    assert "Против: Konfederacja 1" in with_clubs and "Воздержались: PiS 178" in with_clubs
    assert_telegram_html(with_clubs)


def test_president_stages_and_committee_referrals_have_labels(process_3039: ProcessDetail) -> None:
    signed = Stage(
        stage_name="Podpisanie", stage_type="PresidentSignature", date=dt.date(2026, 8, 13)
    )
    veto = Stage(stage_name="Wniosek Prezydenta (weto)", stage_type="Veto", print_number="2863")
    referral = Stage(
        stage_name="Skierowanie",
        stage_type="Referral",
        committee_code="ASW",
        committee_name="Komisja Administracji i Spraw Wewnętrznych",
    )
    bare = Stage(stage_name="Skierowanie", stage_type="Referral", committee_code="ASW")
    bill = bill_of(process_3039)

    text = (
        MessageFormatter("ru")
        .status_update(bill, change_of("3039", [signed, veto, referral, bare]))
        .text
    )

    assert "• 13.08.2026: ✍️ Президент подписал закон" in text
    assert "• ⛔ Президент наложил вето (druk 2863)" in text
    assert "• 📮 Направлен в комиссию: Komisja Administracji i Spraw Wewnętrznych (ASW)" in text
    assert "• Skierowanie [ASW]" in text


# --------------------------------------------------------------------------- next step and action


def test_card_links_the_consultation_form_and_names_the_next_step(
    process_3039: ProcessDetail,
) -> None:
    bill = bill_of(process_3039, submission=consulted())

    text = MessageFormatter("ru").new_bill(bill, None, today=TODAY).text

    assert_telegram_html(text)
    assert (
        f'31.08.2026 — 30.09.2026 · <a href="{CONSULTATION_PAGE}">форма для мнений на сайте Сейма</a>'
        in text
    )
    assert "⏭ <b>Что дальше:</b> I чтение в комиссии — ASW" in text  # name unknown: the code
    action = next(line for line in text.splitlines() if line.startswith("👉"))
    assert f'направить мнение через <a href="{CONSULTATION_PAGE}">' in action
    assert "до 30.09.2026" in action
    assert f"{COMMITTEE_PAGE}>ASW</a>" in action
    assert "до заседания" not in action  # nothing scheduled yet


def test_closed_consultation_keeps_the_link_but_drops_the_action(
    process_3039: ProcessDetail,
) -> None:
    bill = bill_of(process_3039, submission=consulted())

    text = MessageFormatter("ru").new_bill(bill, None, today=dt.date(2026, 10, 1)).text

    assert "форма для мнений" in text
    assert "направить мнение через" not in text
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
    passed = process_1962.model_copy(update={"passed": True})
    fmt = MessageFormatter("ru")

    awaiting_publication = fmt.new_bill(bill_of(passed), None, today=TODAY).text
    awaiting_force = fmt.new_bill(bill_of(passed, act=ACT), None, today=TODAY).text
    in_force = fmt.new_bill(bill_of(passed, act=ACT), None, today=dt.date(2026, 11, 20)).text

    assert "⏭ <b>Что дальше:</b> публикация в Dziennik Ustaw" in awaiting_publication
    assert "👉" not in awaiting_publication  # nothing a reader can do
    assert "вступление в силу 19.11.2026" in awaiting_force
    assert "Что дальше" not in in_force


def test_withdrawn_bill_gets_no_next_step(process_3039: ProcessDetail) -> None:
    change = change_of("3039", [], withdrawn=True, closure_detected=True)

    text = MessageFormatter("ru").status_update(bill_of(process_3039), change).text

    assert "Что дальше" not in text and "Проект отозван" in text


# --------------------------------------------------------------------------- sittings


def test_committee_sitting_message(process_3039: ProcessDetail) -> None:
    bill = bill_of(process_3039, submission=consulted(), agenda=(sitting(),))

    text = MessageFormatter("ru").agenda(bill, sitting(), today=TODAY).text

    assert_telegram_html(text)
    assert text.startswith("🗓 <b>Заседание комиссии — druk nr 3039</b>")
    assert "📮 <b>Komisja Administracji i Spraw Wewnętrznych (ASW)</b>" in text
    assert "📅 17.09.2026, 09:00 · sala nr 412" in text
    assert "📝 <b>Пункт повестки:</b> Pierwsze czytanie projektu (druk nr 3039)" in text
    assert "до заседания 17.09.2026" in text
    assert 'transmisje_arch.xsp?unid=1">Трансляция</a>' in text
    assert f"{COMMITTEE_PAGE}>Страница комиссии</a>" in text
    assert text.splitlines()[-1] == "#заседаниекомиссии #kadencja10druk3039"


def test_sejm_sitting_message(process_3039: ProcessDetail) -> None:
    bill = bill_of(process_3039, agenda=(PLENARY,))

    ru = MessageFormatter("ru").agenda(bill, PLENARY, today=TODAY).text
    en = MessageFormatter("en").agenda(bill, PLENARY, today=TODAY).text

    assert_telegram_html(ru)
    assert ru.startswith("🗓 <b>В повестке заседания Сейма — druk nr 3039</b>")
    assert "🏛 заседание Сейма № 65, 15–18.09.2026" in ru
    assert "Трансляция" not in ru and "Страница комиссии" not in ru
    assert ru.splitlines()[-1] == "#заседаниесейма #kadencja10druk3039"
    assert "On the agenda of a Sejm sitting" in en and "Sejm sitting no. 65, 15–2026-09-18" in en


# --------------------------------------------------------------------------- consultations


def test_consultation_deadline_reminder(process_3039: ProcessDetail) -> None:
    bill = bill_of(process_3039, submission=consulted())
    fmt = MessageFormatter("ru")

    ahead = fmt.consultation_deadline(bill, today=dt.date(2026, 9, 28)).text
    last_day = fmt.consultation_deadline(bill, today=dt.date(2026, 9, 30)).text

    assert_telegram_html(ahead)
    assert "Консультации заканчиваются — druk nr 3039" in ahead
    assert "до 30.09.2026 · осталось дней: 2" in ahead
    assert f'👉 <a href="{CONSULTATION_PAGE}">мнение можно направить' in ahead
    assert f'🔗 <a href="{CONSULTATION_PAGE}">форма для мнений на сайте Сейма</a> | ' in ahead
    assert "сегодня последний день" in last_day


def test_consultation_results_notice(process_3039: ProcessDetail) -> None:
    bill = bill_of(process_3039, submission=consulted(consultation_results=True))

    text = MessageFormatter("ru").consultation_results(bill, today=dt.date(2026, 10, 5)).text

    assert_telegram_html(text)
    assert text.startswith("🗣 <b>Опубликованы мнения из консультаций — druk nr 3039</b>")
    assert "📅 <b>Общественные консультации:</b> 31.08.2026 — 30.09.2026" in text
    assert f'<a href="{CONSULTATION_PAGE}">мнения, поданные' in text
    assert "⏭ <b>Что дальше:</b> I чтение в комиссии — ASW" in text
    assert text.splitlines()[-1] == "#консультации #kadencja10druk3039"


def test_consultation_messages_need_a_consultation(process_3039: ProcessDetail) -> None:
    bill = bill_of(process_3039)

    with pytest.raises(ValueError):
        MessageFormatter("ru").consultation_results(bill)
    with pytest.raises(ValueError):
        MessageFormatter("ru").consultation_deadline(bill, today=TODAY)


# --------------------------------------------------------------------------- acts


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
    assert "Dz.U. 2026 poz. 1099 (опубликован 18.08.2026)" in text
    assert "Вступает в силу:</b> 19.11.2026" in text
    assert "Отдельные положения могут вступать в силу" in text
    assert "дата вступления в силу пока не указана" in no_date


def test_in_force_reminder_needs_a_date(process_3039: ProcessDetail) -> None:
    undated = bill_of(process_3039, act=ACT.model_copy(update={"entry_into_force": None}))

    with pytest.raises(ValueError):
        MessageFormatter("ru").in_force(undated)


# --------------------------------------------------------------------------- run report


def _report(**overrides: Any) -> RunReport:
    fields: dict[str, Any] = dict(
        started_at=NOW,
        finished_at=NOW,
        since=NOW,
        mode="run",
        discovered=77,
        prefilter_hits=3,
        analyzed=2,
        analysis_failures=1,
        published=2,
        updates=1,
        errors=["1 publication(s) failed"],
        llm_input_tokens=50_000,
        llm_output_tokens=4_000,
        llm_usage={
            "claude-opus-5": TokenUsage(input=44_000, output=3_900, cache_read=2_000),
            "claude-sonnet-5": TokenUsage(input=4_000, output=100),
        },
        phase_seconds={"discovery": 4.1, "text prefilter": 60.0},
        rejected=[
            AnalysisVerdict(
                number="2695",
                title="Rządowy projekt ustawy o zmianie ustawy o jakości handlowej artykułów "
                "rolno-spożywczych oraz niektórych innych ustaw <x>",
                relevant=False,
                score=1,
                triaged=True,
            ),
            AnalysisVerdict(number="2411", title="Poselski projekt", relevant=True, score=2),
        ],
    )
    fields.update(overrides)
    return RunReport(**fields)


def test_run_report_lists_counters_costs_rejections_and_warnings() -> None:
    warnings = [f"WARNING lexinform.x: line {i} " + "x" * 200 for i in range(100)]

    text = MessageFormatter("ru").run_report(_report(), warnings).text

    assert_telegram_html(text)
    assert text.startswith("<b>❌") and "discovered: 77" in text
    assert "timing: discovery 4.1s · text prefilter 60.0s" in text
    # 44k*5 + 2k*0.5 + 3.9k*25 + 4k*2 + 0.1k*10 = $0.3275
    assert "tokens in/out: 50000/4000 · opus-5 46.0k/3.9k · sonnet-5 4.0k/100 · ≈ $0.33" in text
    assert "<b>analysed, not published</b>\n• druk 2695 · triage · Rządowy projekt" in text
    assert "• druk 2411 · score 2 · Poselski projekt" in text
    assert "…" in text and "<x>" not in text  # long title clipped, HTML escaped
    assert "<pre>" in text  # the warnings, trimmed to fit


def test_run_report_cost_line_adapts_to_the_models_used() -> None:
    fmt = MessageFormatter("ru")

    one_model = fmt.run_report(
        _report(llm_usage={"claude-sonnet-5": TokenUsage(input=1_000, output=100)}), []
    ).text
    unknown_model = fmt.run_report(_report(llm_usage={"fake": TokenUsage(input=1)}), []).text
    clean = fmt.run_report(_report(errors=[]), []).text

    assert "tokens in/out: 50000/4000 · ≈ $0.003" in one_model and "sonnet-5 1.0k" not in one_model
    assert "$" not in unknown_model
    assert clean.startswith("<b>✅") and "<pre>" not in clean


# --------------------------------------------------------------------------- text helpers


def test_fit_trims_at_a_word_boundary_and_marks_the_cut() -> None:
    assert fit("abc", 10) == "abc"
    trimmed = fit("word " * 100, 50)
    assert len(trimmed) <= 50 and trimmed.endswith("…")


@pytest.mark.parametrize("budget", range(41, 120, 7))
def test_shrink_block_keeps_the_header_and_well_formed_html(budget: int) -> None:
    block = "🔑 <b>Ключевые изменения</b>\n• Tom &amp; Jerry &lt;x&gt;\n• " + "и" * 200

    out = shrink_block(block, budget)

    assert len(out) <= budget
    if out:
        assert_telegram_html(out)
        assert out.startswith("🔑 <b>Ключевые изменения</b>\n")
        assert "&" not in out.replace("&amp;", "").replace("&lt;", "").replace("&gt;", "")
