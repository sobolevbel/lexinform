import datetime as dt

import pytest

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import (
    Bill,
    BillStatus,
    ProcessSummary,
    Stage,
    StatusChange,
    next_phase,
    update_event,
)

NOW = dt.datetime(2026, 9, 16, tzinfo=dt.UTC)
VETO = Stage(stage_type="Veto", stage_name="Wniosek Prezydenta (weto)")


def bill_with(stages: tuple[Stage, ...]) -> Bill:
    return Bill(
        summary=ProcessSummary(
            term=10,
            number="2111",
            title="t",
            document_type="projekt ustawy",
            change_date=NOW,
            passed=True,
        ),
        status=BillStatus.ANALYZED,
        stages=stages,
        first_seen_at=NOW,
        last_checked_at=NOW,
    )


@pytest.mark.parametrize("content_changed", [False, True])
def test_old_closure_with_a_pending_veto_never_claims_adoption(content_changed: bool) -> None:
    bill = bill_with((VETO,))
    change = StatusChange(
        term=10,
        number="2111",
        old_fingerprint="old",
        new_fingerprint="x",
        new_stages=[],
        detected_at=NOW,
        closure_detected=True,
        passed=True,
        content_changed=content_changed,
    )

    assert update_event(change, bill) == ("text_changed" if content_changed else "update")
    text = MessageFormatter("ru").status_update(bill, change, today=NOW.date()).text
    assert "Сейм принял закон" not in text
    assert "Что дальше" in text


@pytest.mark.parametrize("decision", [None, "", "odroczono rozpatrzenie"])
def test_a_motion_without_a_decision_proves_neither_override_nor_signature_deadline(
    decision: str | None,
) -> None:
    motion = Stage(
        stage_type="PresidentMotionConsideration",
        stage_name="Rozpatrywanie na forum Sejmu wniosku Prezydenta",
        date=NOW.date(),
        decision=decision,
    )
    bill = bill_with((VETO, motion))
    change = StatusChange(
        term=10,
        number="2111",
        old_fingerprint="old",
        new_fingerprint="x",
        detected_at=NOW,
        new_stages=[motion],
    )

    assert update_event(change, bill) == "veto"
    phase = next_phase(bill, today=NOW.date())
    assert phase is not None and phase.key == "veto" and phase.deadline is None


def test_a_committee_rejection_proposal_does_not_prove_the_sejm_rejected_the_bill() -> None:
    proposal = Stage(
        stage_type="CommitteeReport",
        stage_name="Sprawozdanie komisji",
        proposal="odrzucić projekt ustawy",
    )
    bill = bill_with((proposal,))
    change = StatusChange(
        term=10,
        number="2111",
        old_fingerprint="old",
        new_fingerprint="x",
        new_stages=[],
        detected_at=NOW,
        closure_detected=True,
        passed=False,
    )

    assert update_event(change, bill) == "not_enacted"
