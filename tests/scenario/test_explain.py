from dataclasses import replace

import pytest
from typer.testing import CliRunner

from lexinform import cli
from lexinform.errors import BatchNotSubmittedError, LlmContextExceededError
from lexinform.models import BillStatus, OutcomeStatus, RunMode, RunReport, Triage
from lexinform.services.analysis import Waiting
from tests.fakes import FakeTextExtractor, make_analysis
from tests.harness import RCL, RPW, TERM, World, print_url, submission, submission_url
from tests.scenario.test_analysis_overflow import OverflowBatch


def commands_only(w: World, *, dry_run: bool = False) -> RunReport:
    return w.run(
        mode=RunMode.COMMANDS,
        discover=False,
        track=False,
        max_analyze=0,
        max_publish=0,
        dry_run=dry_run,
    )


@pytest.mark.parametrize("model", ["gpt-5.1", "claude-opus-5"])
def test_explain_reads_a_triaged_rpw_without_changing_the_channel_analysis(model: str) -> None:
    w = World(
        analysis_model=model,
        triage=True,
        triage_script={
            RPW: Triage(
                affects_foreigners=False,
                confidence=0.99,
                rationale="Не влияет на положение иностранцев",
            )
        },
    )
    w.gateway.submissions.append(submission())
    w.gateway.files[submission_url()] = b"%PDF"
    w.command(f"/analyze {RPW}")
    commands_only(w)
    before = w.bill(RPW)
    assert before.analysis is not None and before.analysis.text_source == "excerpts"
    full = make_analysis(relevant=False, score=1).model_copy(
        update={
            "summary": "Проект регулирует доступ к данным устройств.",
            "key_changes": ["Пользователь получает доступ к данным"],
            "affected_groups": ["Пользователи устройств"],
            "practical_impact": "Можно получить данные своего устройства.",
        }
    )
    w.batch.secondary_script[(RPW, "explanation")] = full
    w.command(f"/explain {RPW}")
    w.command(f"/explain {RPW}")

    queued = commands_only(w)

    assert queued.commands_failed == 0
    assert len(w.batch.submitted) == 1
    assert w.batch.submitted[0].call_kind == "explanation"
    assert w.bill(RPW) == before
    assert len(w.llm.triage_contexts) == 1 and not w.llm.contexts
    w.repo.restore(w.repo.dump())
    w.batch.resolve()
    w.command(f"/explain {RPW}")
    collected = commands_only(w)
    outcome = w.replier.replies[-1][1]
    assert outcome.status is OutcomeStatus.SHOWN
    assert outcome.explanation is not None and outcome.explanation.analysis == full
    assert outcome.explanation.text_source != "excerpts"
    assert outcome.explanation.source_url is not None
    assert w.bill(RPW) == before and not w.publisher.new_bills
    assert [call.kind for call in collected.llm_calls] == ["explanation"]
    assert all(call.batched for call in collected.llm_calls)
    w.command(f"/explain {RPW}")
    repeated = commands_only(w)
    assert not repeated.llm_calls and len(w.batch.submitted) == 1
    text = w.formatter.command_reply(*w.replier.replies[-1]).text
    assert full.summary in text and full.practical_impact in text
    assert full.key_changes[0] in text and full.affected_groups[0] in text


@pytest.mark.parametrize("source", ["3039", RPW, RCL])
def test_explaining_an_unknown_bill_does_not_enrol_it_for_publication(source: str) -> None:
    w = World(triage=True)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.gateway.submissions.append(submission())
    w.gateway.files[submission_url()] = b"%PDF"
    w.add_rcl_project()
    w.command(f"/explain {source}")

    report = commands_only(w)

    assert report.commands_failed == 0
    assert w.repo.get(TERM, source) is None
    assert len(w.batch.submitted) == 1 and not w.llm.triage_contexts
    w.batch.resolve()
    w.command(f"/explain {source}")
    commands_only(w)
    assert w.replier.replies[-1][1].explanation is not None
    assert w.repo.get(TERM, source) is None and not w.publisher.new_bills


def test_explain_preserves_a_pending_main_analysis_and_uses_a_separate_batch() -> None:
    w = World(batch_only=True)
    w.add_bill("3039", "Projekt ustawy о cudzoziemcach")
    w.command("/analyze 3039")
    commands_only(w)
    before = w.bill("3039")
    w.command("/explain 3039")

    commands_only(w)

    assert w.bill("3039") == before
    assert [req.call_kind for req in w.batch.submitted] == ["analysis", "explanation"]
    w.batch.resolve("batch-2")
    w.command("/explain 3039")
    commands_only(w)
    assert w.replier.replies[-1][1].explanation is not None
    assert w.bill("3039") == before and not w.publisher.new_bills


def test_preview_shows_full_irrelevant_analysis_without_publishing() -> None:
    w = World(llm_script={"3039": make_analysis(relevant=False, score=1)})
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.command("/analyze 3039")
    commands_only(w)
    w.command("/preview 3039")

    report = commands_only(w)

    outcome = w.replier.replies[-1][1]
    assert report.commands_failed == 0 and outcome.status is OutcomeStatus.PREVIEWED
    text = w.formatter.command_reply(*w.replier.replies[-1]).text
    assert "нерелевантный проект" in text
    assert not w.publisher.new_bills and not w.batch.submitted


def test_preview_of_triage_shows_the_reason_and_how_to_get_an_explanation() -> None:
    w = World(
        triage=True,
        triage_script={
            "3039": Triage(
                affects_foreigners=False,
                confidence=0.99,
                rationale="Изменяет правила доступа к данным",
            )
        },
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.command("/analyze 3039")
    commands_only(w)
    w.command("/preview 3039")

    report = commands_only(w)

    text = w.formatter.command_reply(*w.replier.replies[-1]).text
    assert report.commands_failed == 0
    assert "Изменяет правила доступа к данным" in text and "/explain 3039" in text
    assert "предварительная оценка" in text and not w.batch.submitted


def test_explain_refuses_metadata_instead_of_claiming_to_read_a_missing_text() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", with_pdf=False)
    w.command("/explain 3039")

    commands_only(w)

    assert w.replier.replies[-1][1].status is OutcomeStatus.ERROR
    assert "Текст проекта пока недоступен" in w.replier.replies[-1][1].note
    assert not w.batch.submitted and not w.llm.contexts


def test_explain_dry_run_never_submits_a_batch() -> None:
    w = World(triage=True)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.command("/explain 3039")

    commands_only(w, dry_run=True)

    assert not w.batch.submitted and not w.llm.contexts and not w.llm.triage_contexts
    assert not w.repo.list_queued_batch_intents()


def test_explain_reuses_completed_memo_after_a_service_restart() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.command("/explain 3039")
    commands_only(w)
    bill = w.replier.replies[-1][1].bill
    assert bill is not None
    w.batch.resolve()
    w.analysis.collect_batches()
    w.repo.restore(w.repo.dump())

    result = replace(w.container).analysis_service().explain_bill(bill)

    assert not isinstance(result, Waiting)
    assert len(w.batch.submitted) == 1 and result.input_tokens == 0


def test_uncertain_explanation_submission_is_never_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.command("/explain 3039")

    def uncertain(requests: object) -> str:
        raise RuntimeError("connection lost after submission")

    with monkeypatch.context() as patch:
        patch.setattr(w.batch, "submit", uncertain)
        commands_only(w)
    assert len(w.repo.list_submitting_batch_intents()) == 1
    w.command("/explain 3039")
    commands_only(w)
    assert w.replier.replies[-1][1].status is OutcomeStatus.QUEUED
    assert not w.batch.submitted


def test_confirmed_non_submission_keeps_the_explanation_queued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.command("/explain 3039")

    def not_submitted(requests: object) -> str:
        raise BatchNotSubmittedError("not sent")

    with monkeypatch.context() as patch:
        patch.setattr(w.batch, "submit", not_submitted)
        commands_only(w)
    assert len(w.repo.list_queued_batch_intents()) == 1
    commands_only(w)
    assert len(w.batch.submitted) == 1


def test_failed_explanation_does_not_change_the_bill_or_fall_back_to_sync() -> None:
    w = World(batch_script={"3039": RuntimeError("batch failed")})
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run(max_analyze=0, publish=False, track=False)
    before = w.bill("3039")
    w.command("/explain 3039")
    commands_only(w)
    w.batch.resolve()
    w.command("/explain 3039")

    commands_only(w)

    assert w.replier.replies[-1][1].status is OutcomeStatus.ERROR
    assert w.bill("3039") == before
    assert len(w.batch.submitted) == 1 and not w.llm.contexts


def test_explanation_context_rejection_uses_overflow_without_changing_bill_status() -> None:
    overflow = OverflowBatch()
    w = World(
        analysis_model="gpt-5.1",
        overflow_batch=overflow,
        batch_script={"3039": LlmContextExceededError("context_length_exceeded")},
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run(max_analyze=0, publish=False, track=False)
    before = w.bill("3039")
    w.command("/explain 3039")
    commands_only(w)
    w.batch.resolve()
    w.command("/explain 3039")

    commands_only(w)

    assert len(overflow.submitted) == 1
    assert overflow.submitted[0].call_kind == "explanation"
    overflow.resolve()
    w.command("/explain 3039")
    commands_only(w)
    assert w.replier.replies[-1][1].explanation is not None
    assert w.bill("3039") == before and before.status is BillStatus.ANALYSIS_PENDING


def test_changed_explanation_text_gets_a_new_request() -> None:
    extractor = FakeTextExtractor("Art. 1. Dostęp do danych urządzenia. " * 30)
    w = World(extractor=extractor)
    w.add_bill("3039", "Projekt ustawy o danych")
    w.command("/explain 3039")
    commands_only(w)
    w.batch.resolve()
    w.command("/explain 3039")
    commands_only(w)
    extractor.text = "Art. 1. Zakaz dostępu do danych urządzenia. " * 30
    w.gateway.files[print_url("3039")] = b"%PDF NEW"
    bill = w.replier.replies[-1][1].bill
    assert bill is not None

    result = replace(w.container).analysis_service().explain_bill(bill)

    assert len(w.batch.submitted) == 2 and isinstance(result, Waiting)


def test_explain_follows_a_linked_entry_without_changing_either_row() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.gateway.submissions.append(submission(print_number="3039"))
    w.command(f"/analyze {RPW}")
    commands_only(w)
    source, printed = w.bill(RPW), w.bill("3039")
    w.command(f"/explain {RPW}")

    commands_only(w)

    assert w.batch.submitted[-1].number == "3039"
    assert w.bill(RPW) == source and w.bill("3039") == printed


def test_reset_of_the_channel_analysis_does_not_duplicate_an_explanation() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run(max_analyze=0, publish=False, track=False)
    w.command("/explain 3039")
    commands_only(w)
    w.repo.reset_bill(TERM, "3039", BillStatus.ANALYSIS_PENDING)
    w.command("/explain 3039")

    commands_only(w)

    assert len(w.batch.submitted) == 1


def test_explain_keeps_the_per_run_cost_guard_without_changing_bill_status() -> None:
    w = World(max_run_cost_usd=0.01)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.command("/explain 3039")

    commands_only(w)

    assert w.replier.replies[-1][1].status is OutcomeStatus.ERROR
    assert "бюджет" in w.replier.replies[-1][1].note
    assert not w.batch.submitted and w.repo.get(TERM, "3039") is None


@pytest.mark.parametrize("ready", [False, True])
def test_cli_explain_queues_or_prints_the_result_without_reader_delivery(
    monkeypatch: pytest.MonkeyPatch,
    ready: bool,
) -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o danych")
    if ready:
        w.command("/explain 3039")
        commands_only(w)
        w.batch.resolve()
        commands_only(w)
    monkeypatch.setattr(cli, "_container", lambda: w.container)

    result = CliRunner().invoke(cli.app, ["explain", "3039"])

    assert result.exit_code == 0, result.output
    assert len(w.batch.submitted) == 1 and not w.publisher.new_bills
    if ready:
        assert "Что это значит на практике" in result.output
        assert "Проект меняет правила" in result.output
    else:
        assert "ожидает batch" in result.output
