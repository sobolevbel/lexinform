"""What the operator reads: the run report in the log channel, the answer to a command, and the
two primitives that keep both inside the message limit (`fit`, `shrink_block`)."""

import datetime as dt
from typing import Any

import pytest

from lexinform.adapters.telegram_format import MessageFormatter, fit, length, shrink_block
from lexinform.models import (
    AnalysisVerdict,
    BillStatus,
    CommandName,
    CommandOutcome,
    IncomingCommand,
    OutcomeStatus,
    ProcessDetail,
    RunMode,
    RunReport,
    SpendSnapshot,
    StatusSnapshot,
    TokenUsage,
)
from tests.formatting import NOW, assert_telegram_html, bill_of


def _report(**overrides: Any) -> RunReport:
    fields: dict[str, Any] = dict(
        started_at=NOW,
        finished_at=NOW,
        since=NOW,
        mode=RunMode.RUN,
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
    assert "\n🤖 <b>analysis</b>\nanalyzed: 2 · failures: 1\nTotal" in text
    assert "\n📣 <b>posts</b>\nnew cards: 2 · updates: 1\n\n" in text
    assert "\n\n⏱ <b>timing</b>\ndiscovery 4.1s · text prefilter 60.0s" in text
    assert "\n\n❌ <b>errors</b>\n• 1 publication(s) failed" in text
    # 44k*5 + 2k*0.5 + 3.9k*25 = $0.3185 opus, 4k*2 + 0.1k*10 = $0.009 sonnet, total $0.3275
    assert "Total $0.33 · opus-5 $0.32 · sonnet-5 $0.009" in text
    assert "tokens in/out: 50000/4000 · cache read 2.0k" in text

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

    assert "Total $0.003\ntokens in/out: 50000/4000" in one_model
    assert "sonnet-5 $0.003" not in one_model  # single model: no per-model breakdown
    assert "$" not in unknown_model
    assert clean.startswith("<b>✅") and "<pre>" not in clean


def test_fit_trims_at_a_word_boundary_and_marks_the_cut() -> None:
    assert fit("abc", 10) == "abc"
    trimmed = fit("word " * 100, 50)
    assert length(trimmed) <= 50 and trimmed.endswith("…")
    emoji = fit("🙂" * 100, 50)
    assert length(emoji) <= 50 and len(emoji) < 50  # Telegram counts an emoji twice


@pytest.mark.parametrize("budget", range(41, 120, 7))
def test_shrink_block_keeps_the_header_and_well_formed_html(budget: int) -> None:
    block = "🔑 <b>Ключевые изменения</b>\n• Tom &amp; Jerry &lt;x&gt;\n• " + "и" * 200

    out = shrink_block(block, budget)

    assert length(out) <= budget
    if out:
        assert_telegram_html(out)
        assert out.startswith("🔑 <b>Ключевые изменения</b>\n")
        assert "&" not in out.replace("&amp;", "").replace("&lt;", "").replace("&gt;", "")


@pytest.mark.parametrize("budget", range(60, 900, 37))
def test_shrink_block_cuts_a_body_that_carries_markup_between_its_lines(budget: int) -> None:
    """A rendered card, a list of matches, a heading per filed document: the body has tags of
    its own, and a cut at the nearest space would leave Telegram a dangling `<a` and answer the
    operator with a 400 instead of the preview."""
    lines = [
        f'• <b>druk nr {n}</b> · <a href="https://sejm.gov.pl/druk/{n}">o cudzoziemcach {n}</a>'
        for n in range(12)
    ]
    block = "📋 <b>Найдено</b>\n" + "\n".join(lines)

    out = shrink_block(block, budget)

    assert length(out) <= budget
    if out:
        assert_telegram_html(out)
        assert all(line.endswith(("</a>", "</b>", "…")) for line in out.split("\n"))


def _incoming(text: str) -> IncomingCommand:
    return IncomingCommand(update_id=7, chat_id="-1001", message_id=42, text=text, received_at=NOW)


def test_command_reply_names_the_bill_the_verdict_and_the_card(
    process_3039: ProcessDetail,
) -> None:
    formatter = MessageFormatter("ru")
    outcome = CommandOutcome(
        status=OutcomeStatus.ANALYSED, bill=bill_of(process_3039), message_id=101
    )

    text = formatter.command_reply(_incoming("/analyze 3039 <force>"), outcome).text

    assert_telegram_html(text)
    assert text.startswith("🤖 <b>analysed</b> · <code>/analyze 3039 &lt;force&gt;</code>")
    assert "<b>druk nr 3039</b>" in text and "PrzebiegProc.xsp?nr=3039" in text
    assert "🔴 relevant · importance 5/5 · legal_stay · m · pdf" in text
    assert "<i>Проект меняет правила легализации пребывания.</i>" in text
    assert "📣 card posted: message 101" in text


def test_show_reply_adds_status_stage_and_the_last_error(process_3039: ProcessDetail) -> None:
    formatter = MessageFormatter("ru")
    bill = bill_of(
        process_3039, prefilter_hits=["cudzoziemcy"], last_error="text prefilter: no hits"
    ).model_copy(update={"status": BillStatus.SKIPPED_TEXT_PREFILTER, "analysis": None})
    outcome = CommandOutcome(status=OutcomeStatus.SHOWN, bill=bill)

    text = formatter.command_reply(_incoming("/show 3039"), outcome).text

    assert_telegram_html(text)
    assert "status: skipped_text_prefilter · prefilter hits: cudzoziemcy" in text
    assert "last error: text prefilter: no hits" in text
    assert "last stage: " in text and "not analysed" in text
    assert "Что дальше:</b>" in text and "Что можно сделать сейчас:</b>" in text


def test_preview_reply_carries_the_card_itself(process_3039: ProcessDetail) -> None:
    formatter = MessageFormatter("ru")
    outcome = CommandOutcome(
        status=OutcomeStatus.PREVIEWED,
        bill=bill_of(process_3039),
        note="not posted to the channel",
    )

    text = formatter.command_reply(_incoming("/preview 3039"), outcome).text

    assert_telegram_html(text)
    assert text.startswith("👁 <b>preview</b>")
    assert "📜 <b>Новый законопроект — druk nr 3039</b>" in text  # the card, as the channel gets it


def test_status_reply_shows_the_queues_the_posts_and_the_runs(process_3039: ProcessDetail) -> None:
    formatter = MessageFormatter("ru")
    waiting = bill_of(process_3039).model_copy(
        update={"status": BillStatus.ANALYSIS_PENDING, "analysis": None}
    )
    outcome = CommandOutcome(
        status=OutcomeStatus.REPORTED,
        snapshot=StatusSnapshot(
            bills={"analyzed": 43, "analysis_pending": 1},
            publications={"sent": 120, "failed": 1},
            followed=17,
            waiting=(waiting,),
            runs=(
                RunReport(
                    started_at=NOW,
                    finished_at=NOW,
                    since=NOW,
                    mode=RunMode.RUN,
                    published=2,
                    updates=3,
                ),
            ),
            days=7,
        ),
    )

    text = formatter.command_reply(_incoming("/status"), outcome).text

    assert_telegram_html(text)
    assert "📥 <b>bills</b>: analyzed 43 · analysis_pending 1" in text
    assert "📣 <b>posts</b>: sent 120 · failed 1" in text
    assert "👁 <b>followed</b>: 17" in text
    assert "🏃 <b>runs</b>: 1 in 7 days · 2 card(s) · 3 update(s) · last " in text
    assert "⏳ <b>waiting</b>" in text and "analysis_pending" in text


def test_forget_reply_names_the_message_that_was_dropped(process_3039: ProcessDetail) -> None:
    formatter = MessageFormatter("ru")
    outcome = CommandOutcome(
        status=OutcomeStatus.FORGOTTEN,
        bill=bill_of(process_3039),
        note="forgotten: message 21; it will not be posted again (skipped_prefilter)",
    )

    text = formatter.command_reply(_incoming("/forget 3039"), outcome).text

    assert_telegram_html(text)
    assert "🗑 <b>forgotten</b>" in text
    assert "message 21" in text
    assert "card posted" not in text  # nothing went out in its place


def test_runs_reply_gives_each_run_its_own_row() -> None:
    formatter = MessageFormatter("ru")
    outcome = CommandOutcome(status=OutcomeStatus.LISTED, runs=(_report(),), note="1 run(s)")

    text = formatter.command_reply(_incoming("/runs days=7"), outcome).text

    assert_telegram_html(text)
    assert text.startswith("🏃 <b>runs</b>")
    assert "77/2/2/1 disc·anal·publ·upd" in text and "1 error(s)" in text


def test_cost_reply_breaks_the_spend_down_by_model_and_by_bill(
    process_3039: ProcessDetail,
) -> None:
    report = _report()
    snapshot = SpendSnapshot(
        days=7, runs=1, usage=report.llm_usage, dearest=report, priciest=(bill_of(process_3039),)
    )
    outcome = CommandOutcome(status=OutcomeStatus.SPENT, spend=snapshot)

    text = MessageFormatter("ru").command_reply(_incoming("/cost days=7"), outcome).text

    assert_telegram_html(text)
    assert "over 1 run(s) in 7 days" in text
    assert "<b>opus-5</b>" in text and "<b>sonnet-5</b>" in text
    assert "dearest run" in text and "<b>druk nr 3039</b>" in text


def test_help_reply_lists_the_commands_after_the_complaint() -> None:
    formatter = MessageFormatter("ru")
    outcome = CommandOutcome(status=OutcomeStatus.HELP, note="unknown command /delete")

    text = formatter.command_reply(_incoming("/delete 1"), outcome).text

    assert_telegram_html(text)
    assert "unknown command /delete" in text
    assert "<code>/help</code>" in text
    for name in CommandName:
        assert f"/{name}" in text, f"the help does not name /{name}"


def test_run_report_lists_the_commands_handled() -> None:
    report = RunReport(
        started_at=NOW,
        finished_at=NOW,
        since=NOW,
        mode=RunMode.COMMANDS,
        commands_handled=1,
        commands=["/analyze 3039 → 3039 analysed (message 101)"],
    )

    text = MessageFormatter("ru").run_report(report, []).text

    assert "🛠 <b>commands</b>\n• /analyze 3039 → 3039 analysed (message 101)" in text


def test_command_reply_reports_when_the_run_started_how_long_it_took_and_what_it_cost(
    process_3039: ProcessDetail,
) -> None:
    outcome = CommandOutcome(
        status=OutcomeStatus.ANALYSED,
        bill=bill_of(process_3039),
        message_id=101,
        run_started_at=dt.datetime(2026, 9, 11, 17, 7, tzinfo=dt.UTC),
        seconds=41.2,
        usage={
            "claude-opus-5": TokenUsage(input=95_300, output=1_100),
            "claude-sonnet-5": TokenUsage(input=7_600, output=200),
        },
    )

    text = MessageFormatter("ru").command_reply(_incoming("/analyze 3039"), outcome).text

    assert_telegram_html(text)
    assert "⏱ run 11.09.2026 17:07 UTC · 41.2s · tokens 102.9k/1.3k" in text
    assert "opus-5 95.3k · sonnet-5 7.6k" in text  # the triage is part of the bill
    assert "≈ $0.52" in text  # 95.3k in + 1.1k out on Opus, 7.6k + 0.2k on Sonnet


def test_a_command_that_never_called_the_model_reports_only_the_time(
    process_3039: ProcessDetail,
) -> None:
    outcome = CommandOutcome(
        status=OutcomeStatus.SHOWN,
        bill=bill_of(process_3039),
        run_started_at=dt.datetime(2026, 9, 11, 17, 7, tzinfo=dt.UTC),
        seconds=0.3,
    )

    text = MessageFormatter("ru").command_reply(_incoming("/show 3039"), outcome).text

    assert "⏱ run 11.09.2026 17:07 UTC · 0.3s" in text
    assert "tokens" not in text and "$" not in text


@pytest.mark.parametrize(
    "status", [OutcomeStatus.HELP, OutcomeStatus.SKIPPED, OutcomeStatus.EXECUTED_EARLIER]
)
def test_a_reply_fits_however_long_the_command_the_operator_sent_was(
    status: OutcomeStatus,
) -> None:
    """Telegram accepts 4096 characters in a command, and the reply echoes them back."""
    outcome = CommandOutcome(status=status, note="nothing to do")

    text = MessageFormatter("ru").command_reply(_incoming("/analyze " + "x" * 4090), outcome).text

    assert_telegram_html(text)


def test_one_endless_error_does_not_take_the_rest_of_the_report_with_it() -> None:
    """The report is the only place the operator learns why a run failed."""
    report = _report(errors=["unexpected failure: ValueError: " + "B" * 6000])

    text = MessageFormatter("ru").run_report(report, []).text

    assert_telegram_html(text)
    assert "\n\n❌ <b>errors</b>\n• unexpected failure: ValueError: BBB" in text
    assert "\n🤖 <b>analysis</b>\nanalyzed: 2 · failures: 1\n" in text  # the counters are still


def test_a_command_reply_escapes_a_title_once(process_3039: ProcessDetail) -> None:
    ampersand = process_3039.model_copy(update={"title": "Projekt R&D <x>"})
    outcome = CommandOutcome(status=OutcomeStatus.SHOWN, bill=bill_of(ampersand))

    text = MessageFormatter("ru").command_reply(_incoming("/show 3039"), outcome).text

    assert_telegram_html(text)
    assert ">Projekt R&amp;D &lt;x&gt;</a>" in text
