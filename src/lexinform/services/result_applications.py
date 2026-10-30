from datetime import datetime

from lexinform.models import Bill, ResultApplication, ResultApplicationTarget
from lexinform.name_binding import mnemonic_matches_result
from lexinform.ports import BillRepository


def record_secondary_application(
    repo: BillRepository,
    term: int,
    number: str,
    result_id: str | None,
    *,
    target: ResultApplicationTarget,
    now: datetime,
) -> None:
    """Record a known result inside the caller's projection transaction."""
    if result_id is None:
        return
    repo.save_result_application(
        ResultApplication(
            result_id=result_id,
            term=term,
            number=number,
            generation=None,
            target=target,
            phase="projection",
            outcome="applied",
            reason="retained_result",
            observed_generation=None,
            observed_status=None,
            created_at=now,
        )
    )


def record_retained_review_applications(
    repo: BillRepository, original: Bill, target: Bill, *, now: datetime
) -> None:
    """Carry known current review receipts inside the government-row transfer transaction."""
    retained: set[tuple[str, str]] = set()
    for record in (
        target.consultation_review,
        target.commission_review,
        target.act_application_review,
    ):
        if record is None or record.computation_id is None:
            continue
        key = record.source_key or record.context.memo_key(original.term)
        retained.add((record.computation_id, f"{record.review.purpose}/{key}"))
    if not retained:
        return
    for receipt in repo.list_result_applications(original.term, original.number):
        address = receipt.target
        if (
            address is not None
            and address.kind == "document_review"
            and (receipt.result_id, address.ref) in retained
        ):
            record_secondary_application(
                repo, target.term, target.number, receipt.result_id, target=address, now=now
            )


def record_selected_name_application(
    repo: BillRepository, original: Bill, target: Bill, *, now: datetime
) -> None:
    """Record only the paid name that this transaction actually selected."""
    selected = target.editorial.mnemonic
    if (
        selected is None
        or selected == original.editorial.mnemonic
        or selected.computation_id is None
        or not mnemonic_matches_result(
            selected, repo.get_computation_result(selected.computation_id)
        )
    ):
        return
    record_secondary_application(
        repo,
        target.term,
        target.number,
        selected.computation_id,
        target=ResultApplicationTarget(kind="editorial", ref="mnemonic"),
        now=now,
    )


def record_retained_name_application(
    repo: BillRepository, original: Bill, target: Bill, *, now: datetime
) -> None:
    """Carry only the current bound name and its exact source projection receipt."""
    selected = target.editorial.mnemonic
    if (
        selected is None
        or selected != original.editorial.mnemonic
        or selected.computation_id is None
        or not mnemonic_matches_result(
            selected, repo.get_computation_result(selected.computation_id)
        )
    ):
        return
    address = ResultApplicationTarget(kind="editorial", ref="mnemonic")
    if any(
        receipt.result_id == selected.computation_id
        and receipt.target == address
        and receipt.phase == "projection"
        and receipt.outcome == "applied"
        for receipt in repo.list_result_applications(original.term, original.number)
    ):
        record_secondary_application(
            repo, target.term, target.number, selected.computation_id, target=address, now=now
        )
