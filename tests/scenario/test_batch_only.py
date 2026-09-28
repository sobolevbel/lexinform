from dataclasses import replace

import pytest

from lexinform.models import OutcomeStatus, TextDocument
from lexinform.services.analysis import Waiting
from tests.harness import World
from tests.scenario.test_analysis_overflow import OverflowBatch


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("model", ["gpt-5.1", "claude-opus-5-5"])
def test_manual_commands_queue_all_models_and_only_triage_is_synchronous(
    force: bool,
    model: str,
) -> None:
    w = World(analysis_model=model, batch_only=True, triage=True)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.command("/analyze 3039" + (" force" if force else ""))

    report = w.run()

    assert report.commands_failed == 0
    assert w.replier.replies[0][1].status is OutcomeStatus.QUEUED
    assert len(w.batch.submitted) == 1 and not w.llm.contexts
    assert len(w.llm.triage_contexts) == 1
    assert not w.publisher.new_bills
    w.batch.resolve()
    w.run()
    assert w.bill("3039").analysis is not None
    assert len(w.publisher.new_bills) == 1


@pytest.mark.parametrize("kind", ["joint", "supplement", "amendments"])
def test_secondary_models_use_their_provider_and_never_expire_to_sync(kind: str) -> None:
    overflow = OverflowBatch()
    w = World(analysis_model="gpt-5.1", overflow_batch=overflow)
    for number in ("3039", "3040"):
        w.add_bill(number, "Projekt ustawy o cudzoziemcach")
    w.run()
    options = replace(w.container.analysis_options(), require_batch=True)
    container = replace(w.container, analysis_options_override=options)
    analysis = container.analysis_service()
    document = TextDocument(
        url="https://api.test/filed.pdf",
        kind=("senate_amendments" if kind == "amendments" else "government_position"),
    )
    w.gateway.files[document.url] = b"%PDF"

    def ask() -> object:
        if kind == "joint":
            return analysis.compare_joint(w.bill("3039"), [w.bill("3040")], may_wait=False)
        if kind == "supplement":
            return analysis.digest_supplement(
                w.bill("3039"), document, number="3039", title="Opinia"
            )
        return analysis.summarize_amendments(w.bill("3039"), document, may_wait=False)

    first = ask()
    assert isinstance(first, Waiting)
    analysis.submit_queued_batches()
    w.clock.advance(hours=48)
    assert ask() == first
    assert len(overflow.submitted) == 1
    assert not w.batch.submitted
    assert not w.llm.joint_contexts and not w.llm.supplement_contexts
    assert not w.llm.amendment_contexts
    overflow.resolve()
    analysis.collect_batches()
    assert not isinstance(ask(), Waiting)


def test_dry_run_prepares_without_submitting_or_calling_analysis() -> None:
    w = World(batch_only=True, triage=True)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    w.run(dry_run=True)

    assert not w.batch.submitted and not w.llm.contexts
    assert w.llm.triage_contexts
