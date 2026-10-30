from datetime import UTC, datetime

from lexinform.models import BillStatus, ComputationResult, ResultApplication
from lexinform.models.primary_projections import PrimaryProjectionReference
from lexinform.primary_projections import ProjectionOwner, retained_record_sha256


def reference(result: ComputationResult) -> PrimaryProjectionReference:
    assert result.request_sha256 is not None, "the paid fixture retains its exact request hash"
    assert result.schema_version in (1, 2), "the primary fixture uses an admitted schema"
    return PrimaryProjectionReference(
        modern_primary_ref=result.result_id,
        request_sha256=result.request_sha256,
        result_schema_version=result.schema_version,
        retained_record_sha256=retained_record_sha256(result.model_dump_json()),
    )


def receipt(result: ComputationResult, owner: ProjectionOwner) -> ResultApplication:
    return ResultApplication(
        result_id=result.result_id,
        term=owner.term,
        number=owner.number,
        generation=1,
        phase="projection",
        outcome="applied",
        reason="retained_result",
        observed_generation=1,
        observed_status=BillStatus.ANALYZED,
        created_at=datetime(2026, 10, 30, tzinfo=UTC),
    )
