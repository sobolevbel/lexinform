from datetime import datetime

from lexinform.models import ResultApplication, ResultApplicationTarget
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
