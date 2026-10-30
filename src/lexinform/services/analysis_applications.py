"""Apply primary analyses and their receipts through one transaction boundary."""

from datetime import datetime

from lexinform.models import AnalysisRecord, Bill, ResultApplicabilityObservation, ResultApplication
from lexinform.models.computations import ProjectionReason
from lexinform.ports import BillRepository
from lexinform.services.result_applications import record_selected_name_application


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
        if before is not None:
            selected = repo.get(term, number)
            if selected is not None:
                record_selected_name_application(repo, before, selected, now=now)
            record_analysis_application(repo, before, record, now=now, reason=reason)


def record_analysis_application(
    repo: BillRepository,
    target: Bill,
    record: AnalysisRecord,
    *,
    now: datetime,
    reason: ProjectionReason | None = None,
) -> None:
    """Record known primary provenance inside the caller's projection transaction."""
    if record.computation_id is None:
        return
    observation = ResultApplicabilityObservation.from_record(
        record, term=target.term, number=target.number, now=now
    )
    if observation is not None:
        repo.save_applicability_observation(observation)
    previous = target.analysis.computation_id if target.analysis is not None else None
    if reason is None and previous == record.computation_id:
        return
    repo.save_result_application(
        ResultApplication(
            result_id=record.computation_id,
            term=target.term,
            number=target.number,
            generation=target.analysis_generation,
            phase="projection",
            outcome="applied",
            reason=reason or "retained_result",
            observed_generation=target.analysis_generation,
            observed_status=target.status,
            created_at=now,
        )
    )
