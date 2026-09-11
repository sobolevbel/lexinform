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
    assert "Стадия:</b> направлен в комиссию ASW" in text  # translated, the body stays Polish
    assert "PrzebiegProc.xsp?nr=3039" in text and "prints/3039/3039.pdf" in text
    assert "#kadencja10druk3039 #важность5 #легализация #каденция10" in text


def test_joint_bill_reply_names_the_thread_and_carries_both_tags(
    process_3039: ProcessDetail, print_3039: PrintInfo
) -> None:
    primary = bill_of(process_3039)
    other = process_3039.model_copy(
        update={
            "number": "3050",
            "title": "Rządowy projekt ustawy o zmianie ustawy o udzielaniu cudzoziemcom ochrony",
            "prints_considered_jointly": ("3039", "3051"),
            "document_date": dt.date(2026, 9, 2),
        }
    )

    text = MessageFormatter("ru").joint_bill(bill_of(other), primary, print_3039).text

    assert_telegram_html(text)
    assert text.startswith(
        "🔀 <b>Альтернативный проект того же закона — druk nr 3050</b>\n\n<b>Rządowy projekt"
    )
    assert "Рассматривается совместно с druk 3039, 3051:" in text  # the card's print first
    assert "Инициатор:</b> правительственный\n📄 <b>Дата druku:</b> 02.09.2026" in text
    assert "О чём проект" not in text  # the analysis stays on the card
    assert "PrzebiegProc.xsp?nr=3050" in text
    assert text.endswith("#kadencja10druk3050 #kadencja10druk3039")


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

    assert "\n📄 <b>Дата druku:</b> 03.08.2026" in ru  # the date has its own line
    assert "New bill" in en and "#importance5" in en
    assert "Print date:</b> 2026-08-03" in en


def test_formatter_dates_from_its_own_clock_when_no_day_is_given(
    process_3039: ProcessDetail,
) -> None:
    bill = bill_of(
        process_3039,
        submission=consulted(number="RPW/1/2026", consultation_end=dt.date(2026, 9, 20)),
    )
    during = MessageFormatter("ru", today=lambda: dt.date(2026, 9, 10))
    after = MessageFormatter("ru", today=lambda: dt.date(2026, 10, 1))

    open_tags = during.new_bill(bill, None).text.splitlines()[-1]
    closed_tags = after.new_bill(bill, None).text.splitlines()[-1]

    assert "#консультации" in open_tags and "#консультации" not in closed_tags


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


def test_status_update_without_an_analysis_keeps_the_stages_before_the_closure(
    process_1962: ProcessDetail,
) -> None:
    bill = bill_of(process_1962).model_copy(update={"analysis": None})  # no importance badge
    change = change_of(
        "1962", flatten_stages(process_1962.stages)[-3:], closure_detected=True, passed=True
    )

    text = MessageFormatter("ru").status_update(bill, change).text

    assert_telegram_html(text)
    assert text.index("• 03.09.2026: отчёт комиссии") < text.index("Сейм принял закон.")


def test_fixed_blocks_alone_over_the_limit_are_cut_at_a_line_boundary(
    process_3039: ProcessDetail,
) -> None:
    long_title = " ".join(["Ustawa o zmianie ustawy o cudzoziemcach"] * 120)
    summary = process_3039.model_copy(update={"title": long_title})
    bill = bill_of(summary, make_analysis(score=4))

    text = MessageFormatter("ru").new_bill(bill, None).text

    assert len(text) <= MESSAGE_LIMIT
    assert_telegram_html(text)


def test_status_update_lists_new_stages_and_the_closure(process_1962: ProcessDetail) -> None:
    bill = bill_of(process_1962, make_analysis(score=3))
    change = change_of(
        "1962", flatten_stages(process_1962.stages)[-3:], closure_detected=True, passed=True
    )

    text = MessageFormatter("ru").status_update(bill, change).text

    assert_telegram_html(text)
    # The header names the newest event; the stages are bullets in the reader's language.
    assert "🏛 <b>Сейм рассмотрел поправки Сената — druk nr 1962</b>" in text
    assert (
        "• 03.09.2026: отчёт комиссии (sprawozdanie) (druk 3014): предлагает принять часть" in text
    )
    assert "• 04.09.2026: Сейм рассмотрел позицию Сената — часть поправок Сената принята" in text
    assert "• процесс в Сейме завершён" in text and "Uchwalono" not in text
    assert "Сейм принял закон." in text
    assert "#kadencja10druk1962" in text


def test_update_header_names_the_event_and_the_closure_line_is_not_repeated(
    process_1962: ProcessDetail,
) -> None:
    flat = flatten_stages(process_1962.stages)
    third = next(st for st in flat if st.stage_type == "SejmReading" and "III" in st.stage_name)
    voting = next(st for st in flat if st.stage_type == "Voting")
    referral = next(st for st in flat if st.stage_type == "Referral")
    report = next(st for st in flat if st.stage_type == "CommitteeReport")
    rejecting = report.model_copy(update={"proposal": "odrzucić projekt ustawy"})
    bill = bill_of(process_1962)
    fmt = MessageFormatter("ru")

    passed = fmt.status_update(
        bill, change_of("1962", [third, voting], closure_detected=True, passed=True)
    ).text
    rejected = fmt.status_update(
        bill, change_of("1962", [], closure_detected=True, passed=False)
    ).text
    referred = fmt.status_update(bill, change_of("1962", [referral])).text
    reported = fmt.status_update(bill, change_of("1962", [rejecting])).text

    assert passed.startswith("✅ <b>Сейм принял закон — druk nr 1962</b>")
    assert "• 17.07.2026: III чтение на заседании Сейма — закон принят" in passed
    assert "Сейм принял закон." not in passed  # the header said it
    assert rejected.startswith("❌ <b>Сейм отклонил проект — druk nr 1962</b>")
    assert referred.startswith("📮 <b>Направлен в комиссию — druk nr 1962</b>")
    assert reported.startswith("❌ <b>Комиссия предлагает отклонить проект — druk nr 1962</b>")
    assert "предлагает отклонить проект" in reported


def test_update_repeats_one_sentence_of_the_summary_unless_the_analysis_changed(
    process_1962: ProcessDetail,
) -> None:
    analysis = make_analysis()
    analysis.summary = "Первое предложение о сути. Второе предложение с деталями."
    referral = next(st for st in flatten_stages(process_1962.stages) if st.stage_type == "Referral")
    bill = bill_of(process_1962, analysis)
    fmt = MessageFormatter("ru")

    plain = fmt.status_update(bill, change_of("1962", [referral])).text
    reanalysed = fmt.status_update(bill, change_of("1962", [], content_changed=True)).text

    assert "📝 <b>Суть проекта:</b> Первое предложение о сути." in plain
    assert "Второе предложение" not in plain
    assert "📝 <b>Суть проекта</b>\nПервое предложение о сути. Второе предложение" in reanalysed


def test_frame_stages_are_dropped_when_their_children_are_listed(
    process_1962: ProcessDetail,
) -> None:
    referral_parent = process_1962.stages[1]  # "Skierowano do I czytania w komisjach" + child
    committee_work = process_1962.stages[3]  # "Praca w komisjach po I czytaniu" + report
    bill = bill_of(process_1962)
    fmt = MessageFormatter("ru")

    with_children = fmt.status_update(
        bill,
        change_of(
            "1962",
            [referral_parent, *referral_parent.children, committee_work, *committee_work.children],
        ),
    ).text
    alone = fmt.status_update(bill, change_of("1962", [referral_parent])).text

    assert "Skierowano" not in with_children and "Praca w komisjach" not in with_children
    assert "• 17.11.2025: 📮 Направлен в комиссию: SPC" in with_children
    assert "предлагает принять проект в новой редакции (текст приложен)" in with_children
    assert "• 17.11.2025: направлен на I чтение" in alone


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


def test_replies_of_a_linked_print_carry_the_tag_of_the_card_they_continue(
    process_1962: ProcessDetail,
) -> None:
    fmt = MessageFormatter("ru")
    referral = flatten_stages(process_1962.stages)[0].model_copy(update={"stage_type": "Referral"})
    change = change_of("1962", [referral])
    from_rcl = bill_of(process_1962, linked_number="RCL/12414100", linked_wykaz_number="UC164")
    from_rcl_unnumbered = bill_of(process_1962, linked_number="RCL/12414100")
    from_rpw = bill_of(process_1962, linked_number="RPW/29075/2026")

    assert fmt.status_update(from_rcl, change).text.endswith("#kadencja10druk1962 #RCL_UC164")
    assert fmt.status_update(from_rcl_unnumbered, change).text.endswith("#RCL_12414100")
    assert fmt.act_published(from_rpw.model_copy(update={"act": ACT})).text.endswith(
        "#kadencja10druk1962 #RPW_29075_2026"
    )


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
    assert "• 📮 Направлен в комиссию: ASW" in text


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

    assert (
        "⏭ <b>Что дальше:</b> публикация в Dziennik Ustaw · обычно 1–4 недели после подписи"
        in awaiting_publication
    )
    # Nothing a reader can do: the card says so instead of staying silent.
    assert (
        "👉 <b>Что можно сделать сейчас:</b> пока ничего — ждём публикации" in awaiting_publication
    )
    # A committee bill: no RCL step on its path.
    assert "Путь:</b> Сейм ✓ → комиссии ✓ → II и III чтение ✓ → Сенат ✓ → Президент ✓" in (
        awaiting_publication
    )
    assert "→ Dz.U. ● → в силе" in awaiting_publication
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

    text = MessageFormatter("ru").new_bill(bill_of(in_senate), None, today=TODAY).text

    # The 30 days count from the third reading (17.07.2026): the date, not only the rule.
    assert "Что дальше:</b> рассмотрение в Сенате (до 30 дней) · срок до 16.08.2026" in text
    assert "Что можно сделать сейчас:</b> направить мнение в профильную комиссию Сената" in text
    assert "→ Сенат ● → Президент" in text


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
    assert "можно подать заявку на участие до 20.09.2026" in update
    assert "подать заявку на участие в публичных слушаниях до 20.09.2026" in update
    assert_telegram_html(reminder)
    assert "📢 <b>Заявки на публичные слушания — druk nr 3039</b>" in reminder
    assert (
        "слушания</b> 30.09.2026 · заявки на участие до <b>20.09.2026</b> · осталось дней: 2"
        in (reminder)
    )
    assert "#слушания #kadencja10druk3039" in reminder


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
                term=10,
            ),
            AnalysisVerdict(number="2411", title="Poselski projekt", relevant=True, score=2),
            AnalysisVerdict(
                number="RCL/12414402", title="Karta Nauczyciela", relevant=False, score=1, term=10
            ),
        ],
    )
    fields.update(overrides)
    return RunReport(**fields)


def test_run_report_lists_counters_costs_rejections_and_warnings() -> None:
    warnings = [f"WARNING lexinform.x: line {i} " + "x" * 200 for i in range(100)]

    text = MessageFormatter("ru").run_report(_report(), warnings).text

    assert_telegram_html(text)
    assert text.startswith("<b>❌ lexinform run report</b>")
    assert "\n\n🔎 <b>discovery</b>\nSejm: 77 new · prefilter hits: 3\n\n" in text
    assert "\n🤖 <b>analysis</b>\nanalyzed: 2 · failures: 1\ntokens" in text
    assert "\n📣 <b>posts</b>\nnew cards: 2 · updates: 1\n\n" in text
    assert "\n\n⏱ <b>timing</b>\ndiscovery 4.1s · text prefilter 60.0s" in text
    assert "\n\n❌ <b>errors</b>\n• 1 publication(s) failed" in text
    # 44k*5 + 2k*0.5 + 3.9k*25 + 4k*2 + 0.1k*10 = $0.3275
    assert (
        "tokens in/out: 50000/4000 · cache read 2.0k · opus-5 46.0k/3.9k · sonnet-5 4.0k/100 · "
        "≈ $0.33"
    ) in text

    druk = '<a href="https://www.sejm.gov.pl/Sejm10.nsf/PrzebiegProc.xsp?nr=2695">druk 2695</a>'
    assert f"<b>analysed, not published</b>\n• {druk} · triage · Rządowy projekt" in text
    assert "• druk 2411 · score 2 · Poselski projekt" in text  # no term stored: no link
    rcl = '<a href="https://legislacja.rcl.gov.pl/projekt/12414402">RCL/12414402</a>'
    assert f"• {rcl} · not relevant · Karta Nauczyciela" in text
    assert "…" in text and "<x>" not in text  # long title clipped, HTML escaped
    assert "<pre>" in text  # the warnings, trimmed to fit


def test_run_report_of_a_quiet_run_says_so_instead_of_listing_zeros() -> None:
    quiet = _report(
        discovered=0,
        prefilter_hits=0,
        analyzed=0,
        analysis_failures=0,
        published=0,
        updates=0,
        errors=[],
        llm_input_tokens=0,
        llm_output_tokens=0,
        llm_usage={},
        phase_seconds={"discovery": 0.2, "analysis": 0.0, "publishing": 0.04, "tracking": 4.6},
        rejected=[],
    )

    text = MessageFormatter("ru").run_report(quiet, []).text

    assert "🔎 <b>discovery</b>\nnothing new\n\n" in text
    assert "🤖 <b>analysis</b>\nnothing analyzed\n\n" in text
    assert "📣 <b>posts</b>\nnothing posted\n\n" in text
    assert "⏱ <b>timing</b>\ndiscovery 0.2s · tracking 4.6s" in text
    assert ": 0" not in text and "tokens" not in text


def test_run_report_shows_cache_reads_apart_from_the_uncached_input() -> None:
    usage = {"claude-opus-5": TokenUsage(input=1_000, cache_read=4_000, output=100)}

    text = MessageFormatter("ru").run_report(_report(llm_usage=usage), []).text

    assert "cache read 4.0k" in text


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
