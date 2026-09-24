from datetime import timedelta

import pytest

from lexinform.models import JointRecord
from lexinform.services.analysis import Waiting
from tests.harness import World


@pytest.mark.parametrize("workers", [1, 4])
def test_pipeline_cleans_history_without_republishing_or_reanalyzing(workers: int) -> None:
    w = World(workers=workers)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    before = w.bill("3039").analysis
    card = w.card_id("3039")
    w.repo.save_analysis_memo("expired", "{}", term=10, number="3039", used_at=w.clock.now())
    w.clock.advance(days=200)

    report = w.run()

    assert "expired" not in w.repo.load_analysis_memo()
    assert report.analyzed == report.published == report.reanalyzed == 0
    assert report.llm_input_tokens == report.llm_output_tokens == 0
    assert w.bill("3039").analysis == before
    assert w.card_id("3039") == card
    publication = w.publication("3039")
    assert publication is not None and publication.delivery is None


def test_reusing_paid_secondary_result_refreshes_retention_without_another_call() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.add_bill("3040", "Projekt ustawy o obywatelstwie")
    w.run()
    first = w.analysis.compare_joint(w.bill("3040"), [w.bill("3039")])
    assert isinstance(first, JointRecord)
    w.clock.advance(days=179)

    reused = w.analysis.compare_joint(w.bill("3040"), [w.bill("3039")])
    w.clock.advance(days=2)
    w.repo.prune_history(
        before=w.clock.now() - timedelta(days=90), memo_before=w.clock.now() - timedelta(days=180)
    )
    w.analysis.start_run()
    again = w.analysis.compare_joint(w.bill("3040"), [w.bill("3039")])

    assert isinstance(reused, JointRecord) and isinstance(again, JointRecord)
    assert reused.input_tokens == again.input_tokens == 0
    assert first.comparison == reused.comparison == again.comparison
    assert len(w.llm.joint_contexts) == 1


@pytest.mark.parametrize("submitting", [False, True])
def test_old_queued_and_uncertain_intents_protect_their_bill_memos(submitting: bool) -> None:
    w = World(batch=True, batch_kinds=frozenset({"joint"}))
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.add_bill("3040", "Projekt ustawy o obywatelstwie")
    w.run()
    w.repo.save_analysis_memo(
        "old-paid-context", "{}", term=10, number="3040", used_at=w.clock.now()
    )
    assert isinstance(
        w.analysis.compare_joint(w.bill("3040"), [w.bill("3039")], may_wait=True), Waiting
    )
    if submitting:
        w.repo.mark_batch_intents_submitting(
            [intent.request.custom_id for intent in w.repo.list_queued_batch_intents()]
        )
    w.clock.advance(days=200)

    w.repo.prune_history(
        before=w.clock.now() - timedelta(days=90), memo_before=w.clock.now() - timedelta(days=180)
    )

    assert "old-paid-context" in w.repo.load_analysis_memo()
