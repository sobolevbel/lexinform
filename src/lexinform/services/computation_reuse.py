"""Locate an exact retained secondary answer without changing its paid record."""

from lexinform.models import AmendmentsRecord, JointRecord, NameRecord, SupplementRecord
from lexinform.models.batch import BatchKind
from lexinform.ports import BillRepository


def retained_secondary[Record: (AmendmentsRecord, SupplementRecord, JointRecord, NameRecord)](
    repo: BillRepository,
    term: int,
    number: str,
    purpose: BatchKind,
    request_key: str,
    record_model: type[Record],
) -> Record | None:
    for result in reversed(repo.list_computation_results(term, number)):
        if result.purpose != purpose or result.custom_id != request_key:
            continue
        if isinstance(result.record, record_model):
            return result.record
    return None
