"""Apply primary analyses and their receipts through one transaction boundary."""

from datetime import datetime

from lexinform.models import AnalysisRecord, Bill, ResultApplicabilityObservation, ResultApplication
from lexinform.models.computations import ProjectionReason
from lexinform.ports import BillRepository
from lexinform.primary_projections import (
    ProjectionOwner,
    has_primary_application,
    primary_record_matches,
)
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


def record_moved_primary_application(
    repo: BillRepository, original: Bill, target_term: int, *, now: datetime
) -> None:
    """The caller confirms the row move before a compact target can be hydrated."""
    record = original.analysis
    if record is None or record.computation_id is None:
        return
    if not primary_record_matches(record, repo.get_computation_result(record.computation_id)):
        return
    if not has_primary_application(
        tuple(repo.list_result_applications(original.term, original.number)),
        record.computation_id,
        ProjectionOwner(original.term, original.number),
    ):
        return
    carried = original.model_copy(
        update={"summary": original.summary.model_copy(update={"term": target_term})}
    )
    record_analysis_application(repo, carried, record, now=now, reason="retained_result")
