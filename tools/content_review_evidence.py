"""Resolve recorded review application evidence without altering editorial history."""

import sqlite3

from lexinform.adapters.computation_history import read_result_application
from lexinform.adapters.content_source_refs import ContentSourceRows
from lexinform.adapters.document_source_rows import DocumentSourceRows
from lexinform.models import Bill, ContentSnapshot, ContentVersion, PublicationKind
from lexinform.models.computations import ReviewInputReference, ReviewResultRecord
from lexinform.models.content import validate_review_notice
from lexinform.models.original_delivery_content import REVIEW_NOTICE_KINDS


def review_application_evidence(
    db: sqlite3.Connection, version: ContentVersion, snapshot: ContentSnapshot
) -> dict[str, object]:
    evidence = version.original_delivery_evidence
    if evidence is None or evidence.kind not in REVIEW_NOTICE_KINDS:
        raise ValueError("Review evidence requires a direct review version")
    if snapshot.kind != "bill" or snapshot.snapshot_id != version.bill_context_id:
        raise ValueError("Review evidence requires its exact semantic snapshot")
    bill = Bill.model_validate_json(snapshot.payload_json)
    frozen = validate_review_notice(bill, evidence.kind, evidence.ref)
    if frozen.computation_id is None:
        return {"status": "unavailable", "reason": "paid_identity_unrecorded"}
    result = ContentSourceRows(db).get_computation_result(frozen.computation_id)
    if (
        result is None
        or result.review_input is None
        or result.input_binding is None
        or result.schema_version != 3
    ):
        return {"status": "unavailable", "reason": "paid_input_unavailable"}
    paid = result.record
    reference = ReviewInputReference.from_context(frozen.context, term=result.term)
    expected = result.review_input.without_source_observations()
    if evidence.kind is PublicationKind.CONSULTATION_REVIEW:
        expected = expected.model_copy(
            update={
                "documents": tuple(
                    doc.model_copy(update={"body": ""}) for doc in expected.documents
                )
            }
        )
    if (
        result.validation != "validated"
        or result.purpose != evidence.kind.value
        or result.number != bill.number
        or not isinstance(paid, ReviewResultRecord)
        or paid.computation_id != frozen.computation_id
        or result.request_sha256 is None
        or paid.request_sha256 != result.request_sha256
        or frozen.request_sha256 != result.request_sha256
        or (frozen.model, frozen.prompt_version, frozen.created_at)
        != (paid.model, paid.prompt_version, paid.created_at)
        or reference != expected
    ):
        return {"status": "invalid", "reason": "paid_review_conflict"}
    rendered_review = frozen.review
    if evidence.kind is PublicationKind.COMMISSION_REVIEW:
        findings = iter(paid.review.commission_findings)
        if not all(
            any(item == selected for item in findings)
            for selected in rendered_review.commission_findings
        ):
            return {"status": "invalid", "reason": "paid_review_conflict"}
        rendered_review = rendered_review.model_copy(
            update={"commission_findings": paid.review.commission_findings}
        )
    if rendered_review != paid.review:
        return {"status": "invalid", "reason": "paid_review_conflict"}
    target = f"{evidence.kind.value}/{frozen.source_key or reference.key}"
    rows = db.execute(
        "SELECT * FROM result_applications WHERE term=? AND number=? AND result_id=?"
        " AND phase='projection' AND generation IS NULL"
        " AND json_extract(application_json,'$.target.kind')='document_review'"
        " AND json_extract(application_json,'$.target.ref')=?",
        (bill.term, bill.number, result.result_id, target),
    ).fetchall()
    if not rows:
        return {"status": "unavailable", "reason": "projection_unavailable"}
    if len(rows) != 1:
        return {"status": "invalid", "reason": "projection_ambiguous"}
    application = read_result_application(rows[0])
    if application.created_at.utcoffset() is None or application.created_at > version.recorded_at:
        return {"status": "invalid", "reason": "projection_time_conflict"}
    try:
        observations = DocumentSourceRows(db).review_source_observations(result)
    except ValueError:
        return {"status": "invalid", "reason": "review_source_observation_conflict"}
    return {
        "status": "recorded",
        "application_id": rows[0]["id"],
        "result_id": result.result_id,
        "request_sha256": result.request_sha256,
        "recorded_source_count": len(reference.documents),
        **(
            {
                "source_observations": [
                    {"document_index": index, "observation_id": identity}
                    for index, identity in observations
                ]
            }
            if observations
            else {}
        ),
    }
