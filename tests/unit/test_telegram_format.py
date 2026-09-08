from __future__ import annotations

import datetime as dt
from datetime import UTC, datetime
from html.parser import HTMLParser

import pytest

from lexinform.adapters.llm_prompts import PROMPT_VERSION
from lexinform.adapters.telegram_format import MESSAGE_LIMIT, MessageFormatter, _shrink_block, fit
from lexinform.models import (
    ActInfo,
    AnalysisRecord,
    AnalysisVerdict,
    Bill,
    BillStatus,
    ClubVotes,
    RunReport,
    Stage,
    StatusChange,
    TokenUsage,
    flatten_stages,
)
from tests.fakes import make_analysis


class _TagChecker(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.balanced = True
        self.tags: set[str] = set()

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[no-untyped-def]
        self.stack.append(tag)
        self.tags.add(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack or self.stack.pop() != tag:
            self.balanced = False


def _bill(process, analysis, *, truncated: bool = False) -> Bill:  # type: ignore[no-untyped-def]
    now = datetime(2026, 9, 7, tzinfo=UTC)
    record = AnalysisRecord(
        analysis=analysis,
        model="m",
        prompt_version=PROMPT_VERSION,
        input_chars=10,
        truncated=truncated,
        text_source="pdf",
        created_at=now,
    )
    return Bill(
        summary=process,
        status=BillStatus.ANALYZED,
        stages=process.stages,
        analysis=record,
        first_seen_at=now,
        last_checked_at=now,
    )


def _check_html(text: str) -> _TagChecker:
    checker = _TagChecker()
    checker.feed(text)
    assert checker.balanced, text
    assert checker.tags <= {"b", "i", "a", "code", "pre"}
    return checker


def test_new_bill_card_contains_all_sections(process_3039, print_3039) -> None:  # type: ignore[no-untyped-def]
    bill = _bill(process_3039, make_analysis())
    rendered = MessageFormatter("ru").new_bill(bill, print_3039)
    text = rendered.text
    _check_html(text)
    assert text.startswith("📜 <b>Новый законопроект — druk nr 3039</b>")
    assert "🔴 <b>Важность:</b> ●●●●● 5/5" in text
    assert "📝 <b>О чём проект</b>" in text
    assert "О чём проект" in text and "Ключевые изменения" in text
    assert "PrzebiegProc.xsp?nr=3039" in text
    assert "prints/3039/3039.pdf" in text
    assert "#важность5 #легализация #druk3039 #Sejm10" in text
    assert "Стадия:</b> Skierowanie" in text


def test_partial_text_note_and_escaping(process_3039, print_3039) -> None:  # type: ignore[no-untyped-def]
    analysis = make_analysis()
    analysis.summary = "Ссылка <b>жирный</b> & амперсанд"
    bill = _bill(process_3039, analysis, truncated=True)
    text = MessageFormatter("ru").new_bill(bill, print_3039).text
    assert "&lt;b&gt;жирный&lt;/b&gt; &amp; амперсанд" in text
    assert "неполном тексте" in text
    _check_html(text)


@pytest.mark.parametrize(
    "n_changes,change_len,summary_len", [(12, 400, 3000), (30, 200, 500), (1, 10, 20000)]
)
def test_card_never_exceeds_limit(
    process_3039, print_3039, n_changes, change_len, summary_len
) -> None:  # type: ignore[no-untyped-def]
    analysis = make_analysis()
    analysis.summary = "с" * summary_len
    analysis.key_changes = ["и" * change_len] * n_changes
    text = MessageFormatter("ru").new_bill(_bill(process_3039, analysis), print_3039).text
    assert len(text) <= MESSAGE_LIMIT
    _check_html(text)
    # fixed parts survive trimming
    assert "PrzebiegProc.xsp?nr=3039" in text and "#druk3039" in text


def test_status_update(process_1962) -> None:  # type: ignore[no-untyped-def]
    bill = _bill(process_1962, make_analysis(score=3))
    from lexinform.models import flatten_stages

    new_stages = flatten_stages(process_1962.stages)[-3:]
    change = StatusChange(
        term=10,
        number="1962",
        old_fingerprint="a",
        new_fingerprint="b",
        new_stages=new_stages,
        closure_detected=True,
        passed=True,
        detected_at=datetime(2026, 9, 7, tzinfo=UTC),
    )
    text = MessageFormatter("ru").status_update(bill, change).text
    _check_html(text)
    assert "Обновление — druk nr 1962" in text
    assert "Новые стадии" in text and "Uchwalono" in text
    assert "закон принят" in text
    assert "#обновление #druk1962" in text
    assert len(text) <= MESSAGE_LIMIT


def test_fit_trims_at_boundary() -> None:
    assert fit("abc", 10) == "abc"
    out = fit("word " * 100, 50)
    assert len(out) <= 50 and out.endswith("…")


def test_english_labels(process_3039, print_3039) -> None:  # type: ignore[no-untyped-def]
    text = MessageFormatter("en").new_bill(_bill(process_3039, make_analysis()), print_3039).text
    assert "New bill" in text and "#importance5" in text


def test_status_update_with_reanalysis_shows_summary_and_diff(process_3039) -> None:  # type: ignore[no-untyped-def]
    analysis = make_analysis(score=4)
    analysis.changes_since_previous = ["Срок подачи сокращён", "Добавлена категория студентов"]
    bill = _bill(process_3039, analysis)
    change = StatusChange(
        term=10,
        number="3039",
        old_fingerprint="a",
        new_fingerprint="b",
        new_stages=[],
        content_changed=True,
        detected_at=datetime(2026, 9, 7, tzinfo=UTC),
    )
    text = MessageFormatter("ru").status_update(bill, change).text
    _check_html(text)
    assert "Суть проекта" in text and "Что изменилось с прошлого раза" in text
    assert "Срок подачи сокращён" in text and "🟠 ●●●●○ 4/5" in text
    assert "🆕 <b>Что изменилось с прошлого раза</b>" in text
    assert "Новые стадии" not in text


def test_run_report_renders_and_fits() -> None:
    now = datetime(2026, 9, 7, 6, 0, tzinfo=UTC)
    report = RunReport(
        started_at=now,
        finished_at=now,
        since=now,
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
                title="Rządowy projekt ustawy o zmianie ustawy o jakości handlowej artykułów rolno-spożywczych oraz niektórych innych ustaw <x>",
                relevant=False,
                score=1,
                triaged=True,
            ),
            AnalysisVerdict(number="2411", title="Poselski projekt", relevant=True, score=2),
        ],
    )
    lines = [f"WARNING lexinform.x: line {i} " + "x" * 200 for i in range(100)]
    text = MessageFormatter("ru").run_report(report, lines).text
    _check_html(text)
    assert text.startswith("<b>❌") and "discovered: 77" in text and "<pre>" in text
    assert "timing: discovery 4.1s · text prefilter 60.0s" in text
    # 44k*5 + 2k*0.5 + 3.9k*25 + 4k*2 + 0.1k*10 = $0.3275
    assert "tokens in/out: 50000/4000 · opus-5 46.0k/3.9k · sonnet-5 4.0k/100 · ≈ $0.33" in text
    one_model = report.model_copy(
        update={"llm_usage": {"claude-sonnet-5": TokenUsage(input=1_000, output=100)}}
    )
    line = MessageFormatter("ru").run_report(one_model, []).text
    assert "tokens in/out: 50000/4000 · ≈ $0.003" in line and "sonnet-5 1.0k" not in line
    unknown = report.model_copy(update={"llm_usage": {"fake": TokenUsage(input=1)}})
    assert "$" not in MessageFormatter("ru").run_report(unknown, []).text
    assert "<b>analysed, not published</b>\n• druk 2695 · triage · Rządowy projekt" in text
    assert "• druk 2411 · score 2 · Poselski projekt" in text
    assert "…" in text and "<x>" not in text  # long title clipped, HTML escaped
    assert len(text) <= MESSAGE_LIMIT
    ok_text = MessageFormatter("ru").run_report(report.model_copy(update={"errors": []}), []).text
    assert ok_text.startswith("<b>✅") and "<pre>" not in ok_text


def test_new_bill_details_are_separated_by_blank_lines(process_3039, print_3039) -> None:  # type: ignore[no-untyped-def]
    text = MessageFormatter("ru").new_bill(_bill(process_3039, make_analysis()), print_3039).text
    practical = text.index("Что это значит на практике")
    affected = text.index("Кого касается")
    effective = text.index("Вступление в силу")
    stage = text.index("Стадия:")
    applicant = text.index("Инициатор:")
    assert "\n\n" in text[practical:affected]
    assert "\n\n" in text[affected:effective]
    assert "\n\n" in text[effective:stage]
    # short facts (stage, applicant/date) stay grouped on adjacent lines
    assert "\n\n" not in text[stage:applicant]


def test_headers_are_followed_by_blank_line(process_3039, print_3039) -> None:  # type: ignore[no-untyped-def]
    text = MessageFormatter("ru").new_bill(_bill(process_3039, make_analysis()), print_3039).text
    assert text.startswith(
        f"📜 <b>Новый законопроект — druk nr 3039</b>\n\n<b>{process_3039.title}"
    )


@pytest.mark.parametrize("budget", range(41, 120, 7))
def test_shrink_block_keeps_html_well_formed_at_tight_budgets(budget: int) -> None:
    block = "🔑 <b>Ключевые изменения</b>\n• Tom &amp; Jerry &lt;x&gt;\n• " + "и" * 200
    out = _shrink_block(block, budget)
    assert len(out) <= budget
    if out:
        _check_html(out)
        assert out.startswith("🔑 <b>Ключевые изменения</b>\n")
        assert "&" not in out.replace("&amp;", "").replace("&lt;", "").replace("&gt;", "")


def test_senate_position_is_rendered_from_position_field(process_1962) -> None:  # type: ignore[no-untyped-def]
    senate = next(
        s for s in flatten_stages(process_1962.stages) if s.stage_type == "SenatePosition"
    )
    assert senate.position  # the fixture carries the Senate outcome in `position`
    line = MessageFormatter("ru")._stage_line(senate)
    assert "Сенат внёс поправки" in line and "(druk 2994)" in line
    odd = senate.model_copy(update={"position": "odroczył rozpatrywanie"})
    assert "odroczył rozpatrywanie" in MessageFormatter("ru")._stage_line(odd)  # unknown: raw


def test_voting_stage_renders_totals_clubs_and_pdf_link(process_1962) -> None:  # type: ignore[no-untyped-def]
    voting_stage = next(s for s in flatten_stages(process_1962.stages) if s.stage_type == "Voting")
    assert voting_stage.voting is not None and voting_stage.voting.yes == 239
    fmt = MessageFormatter("ru")
    plain = fmt._stage_line(voting_stage)
    assert "239 за, 1 против, 199 воздержались" in plain and "votings/62/108/pdf" in plain
    assert "\n" not in plain  # no club line without club data
    clubs = (
        ClubVotes(club="KO", yes=152),
        ClubVotes(club="PSL-TD", yes=31),
        ClubVotes(club="Lewica", yes=21),
        ClubVotes(club="Polska2050", yes=13),
        ClubVotes(club="Centrum", yes=12),
        ClubVotes(club="PiS", abstain=178, absent=6),
        ClubVotes(club="Konfederacja", no=1),
    )
    enriched = voting_stage.model_copy(
        update={"voting": voting_stage.voting.model_copy(update={"clubs": clubs})}
    )
    line = fmt._stage_line(enriched)
    assert "За: KO 152, PSL-TD 31, Lewica 21, Polska2050 13, …" in line
    assert "Против: Konfederacja 1" in line and "Воздержались: PiS 178" in line
    _check_html(line)


def test_president_stages_and_committee_referral_have_labels() -> None:
    fmt = MessageFormatter("ru")
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
    assert fmt._stage_line(signed) == "13.08.2026: ✍️ Президент подписал закон"
    assert fmt._stage_line(veto) == "⛔ Президент наложил вето (druk 2863)"
    assert (
        "Направлен в комиссию: Komisja Administracji i Spraw Wewnętrznych (ASW)"
        in fmt._stage_line(referral)
    )
    assert fmt._stage_line(bare) == "Skierowanie [ASW]"


def test_dates_use_the_language_format(process_3039, print_3039) -> None:  # type: ignore[no-untyped-def]
    ru = MessageFormatter("ru").new_bill(_bill(process_3039, make_analysis()), print_3039).text
    en = MessageFormatter("en").new_bill(_bill(process_3039, make_analysis()), print_3039).text
    assert "Дата druku:</b> " in ru and ".2026" in ru.split("Дата druku:</b> ")[1][:10]
    assert "2026-" in en.split("Print date:</b> ")[1][:10]


def test_act_messages_render_in_both_languages(process_3039) -> None:  # type: ignore[no-untyped-def]
    act = ActInfo(
        eli="DU/2026/1099",
        display_address="Dz.U. 2026 poz. 1099",
        title="Ustawa z dnia 17 lipca 2026 r. o zmianie ustawy",
        promulgation_date=dt.date(2026, 8, 18),
        entry_into_force=dt.date(2026, 11, 19),
        text_pdf_url="https://api.sejm.gov.pl/eli/acts/DU/2026/1099/text.pdf",
        isap_url="https://isap.sejm.gov.pl/isap.nsf/DocDetails.xsp?id=WDU20260001099",
        fetched_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    bill = _bill(process_3039, make_analysis()).model_copy(update={"act": act})
    for lang in ("ru", "en"):
        for text in (
            MessageFormatter(lang).act_published(bill).text,
            MessageFormatter(lang).in_force(bill).text,
        ):
            _check_html(text)
            assert len(text) <= MESSAGE_LIMIT
            assert "text.pdf" in text and "isap.sejm.gov.pl" in text
    ru = MessageFormatter("ru").act_published(bill).text
    assert "📖 <b>Опубликован в Dziennik Ustaw — druk nr 3039</b>" in ru
    assert "Отдельные положения могут вступать в силу" in ru
    no_date = bill.model_copy(update={"act": act.model_copy(update={"entry_into_force": None})})
    assert (
        "дата вступления в силу пока не указана"
        in MessageFormatter("ru").act_published(no_date).text
    )
    with pytest.raises(ValueError):
        MessageFormatter("ru").in_force(no_date)
