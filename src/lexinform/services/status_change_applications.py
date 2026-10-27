from datetime import datetime

from lexinform.models import (
    AmendmentsRecord,
    ResultApplicationTarget,
    StatusChange,
    SupplementRecord,
)
from lexinform.ports import BillRepository
from lexinform.services.result_applications import record_secondary_application


def record_status_change(
    repo: BillRepository, change: StatusChange, *, now: datetime
) -> StatusChange | None:
    with repo.atomic():
        change_id = repo.add_status_change(change)
        if change_id is None:
            return None
        stored = repo.get_status_change(change_id)
        assert stored is not None, "add_status_change stores the event before returning its ID"
        records: list[AmendmentsRecord | SupplementRecord] = list(stored.supplements)
        if stored.amendments is not None:
            records.append(stored.amendments)
        for record in records:
            _record_application(repo, stored, record, now=now)
        change.id = change_id
        return change


def apply_status_change_amendments(
    repo: BillRepository, change_id: int, record: AmendmentsRecord, *, now: datetime
) -> None:
    with repo.atomic():
        repo.save_status_change_amendments(change_id, record)
        stored = repo.get_status_change(change_id)
        if stored is not None and stored.amendments is not None:
            _record_application(repo, stored, stored.amendments, now=now)


def _record_application(
    repo: BillRepository,
    change: StatusChange,
    record: AmendmentsRecord | SupplementRecord,
    *,
    now: datetime,
) -> None:
    if record.computation_id is None:
        return
    assert change.id is not None, "the repository read supplies the persisted status-change ID"
    record_secondary_application(
        repo,
        change.term,
        change.number,
        record.computation_id,
        target=ResultApplicationTarget(kind="status_change", ref=str(change.id)),
        now=now,
    )
