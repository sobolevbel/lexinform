"""Apply primary analyses and their receipts through one transaction boundary."""

from datetime import datetime

from lexinform.models import AnalysisRecord, ResultApplication
from lexinform.models.computations import ProjectionReason
from lexinform.ports import BillRepository


def apply_analysis(
    repo: BillRepository,
    term: int,
    number: str,
    record: AnalysisRecord,
    *,
    now: datetime,
    reason: ProjectionReason | None = None,
) -> None:
    with repo.atomic():
        before = repo.get(term, number)
        repo.save_analysis(term, number, record)
        if before is None or record.computation_id is None:
            return
        previous = before.analysis.computation_id if before.analysis is not None else None
        if reason is None and previous == record.computation_id:
            return
        repo.save_result_application(
            ResultApplication(
                result_id=record.computation_id,
                term=term,
                number=number,
                generation=before.analysis_generation,
                phase="projection",
                outcome="applied",
                reason=reason or "retained_result",
                observed_generation=before.analysis_generation,
                observed_status=before.status,
                created_at=now,
            )
        )
