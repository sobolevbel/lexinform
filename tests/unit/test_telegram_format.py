from __future__ import annotations

from datetime import UTC, datetime
from html.parser import HTMLParser

import pytest

from lexinform.adapters.llm_prompts import PROMPT_VERSION
from lexinform.adapters.telegram_format import MESSAGE_LIMIT, MessageFormatter, fit
from lexinform.models import AnalysisRecord, Bill, BillStatus, RunReport, StatusChange
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
    )
    lines = [f"WARNING lexinform.x: line {i} " + "x" * 200 for i in range(100)]
    text = MessageFormatter("ru").run_report(report, lines).text
    _check_html(text)
    assert text.startswith("<b>❌") and "discovered: 77" in text and "<pre>" in text
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
