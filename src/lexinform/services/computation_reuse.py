"""Match recorded logical input scopes without changing paid results."""

from collections.abc import Sequence

from pydantic import BaseModel

from lexinform.models import (
    AmendmentsRecord,
    ComputationResult,
    JointRecord,
    NameRecord,
    SupplementRecord,
)
from lexinform.models.batch import BatchKind
from lexinform.ports import BillRepository


def matching_record[Record: BaseModel](
    results: Sequence[ComputationResult],
    term: int,
    number: str,
    purpose: BatchKind,
    request_key: str,
    record_model: type[Record],
) -> Record | None:
    for result in reversed(results):
        if result.purpose != purpose:
            continue
        binding = result.input_binding
        if binding is not None:
            matches = (binding.term, binding.number, binding.reuse_key) == (
                term,
                number,
                request_key,
            )
        else:
            matches = purpose in ("amendments", "supplement", "joint", "name") and (
                result.term,
                result.number,
                result.custom_id,
            ) == (term, number, request_key)
        if matches and isinstance(result.record, record_model):
            return result.record
    return None


def retained_secondary[Record: (AmendmentsRecord, SupplementRecord, JointRecord, NameRecord)](
    repo: BillRepository,
    term: int,
    number: str,
    purpose: BatchKind,
    request_key: str,
    record_model: type[Record],
) -> Record | None:
    return matching_record(
        repo.list_computation_results(term, number, purpose=purpose),
        term,
        number,
        purpose,
        request_key,
        record_model,
    )
