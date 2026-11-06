"""Resolve only the root-original amendment result's retained question without fetching sources."""

from lexinform.json_storage import read_json
from lexinform.models import AmendmentsRecord, Publication, StatusChange
from lexinform.models.amendment_inputs import AmendmentInputAvailability
from lexinform.ports import BillRepository


def original_amendment_input(
    repo: BillRepository, publication: Publication
) -> AmendmentInputAvailability:
    if publication.id is None:
        return AmendmentInputAvailability(state="unavailable", reason="root_history_unavailable")
    selected = repo.get_publication_content(publication.id)
    root = (
        repo.get_publication_content(selected.root_publication_id) if selected is not None else None
    )
    if root is None:
        return AmendmentInputAvailability(state="unavailable", reason="root_history_unavailable")
    if root.previous_version_id is not None or root.change_snapshot_id is None:
        return AmendmentInputAvailability(
            state="unavailable",
            reason="root_amendment_unavailable",
            root_content_id=root.content_id,
        )
    snapshot = repo.get_content_snapshot(root.change_snapshot_id)
    if snapshot is None or snapshot.kind != "change":
        raise ValueError("invalid_amendment_input")
    change = StatusChange.model_validate(read_json(snapshot.payload_json))
    record = change.amendments
    if record is None or record.computation_id is None:
        return AmendmentInputAvailability(
            state="unavailable",
            reason="root_amendment_unavailable",
            root_content_id=root.content_id,
        )
    result = repo.get_computation_result(record.computation_id)
    if (
        result is None
        or result.purpose != "amendments"
        or not isinstance(result.record, AmendmentsRecord)
    ):
        raise ValueError("invalid_amendment_input")
    witness = result.amendment_scan_input
    if witness is not None:
        owner = (publication.term, publication.number)
        if (
            result.result_id not in root.computation_ids
            or (result.term, result.number) != owner
            or (change.term, change.number) != owner
            or record.request_sha256 != witness.request_sha256
            or record.source_kind != witness.context_without_scan.source_kind
            or record.source_url != witness.source_url
            or record.scan_input != witness.scan_input
        ):
            raise ValueError("invalid_amendment_input")
        return AmendmentInputAvailability(
            state="requires_exact_scan",
            root_content_id=root.content_id,
            result_id=result.result_id,
            scan_witness=witness,
        )
    if result.amendment_input_ref is None:
        return AmendmentInputAvailability(
            state="unavailable",
            reason="legacy_input_unavailable",
            root_content_id=root.content_id,
            result_id=result.result_id,
        )
    if result.result_id not in root.computation_ids:
        raise ValueError("invalid_amendment_input")
    reference = result.amendment_input_ref
    if record.request_sha256 != reference.request_sha256:
        raise ValueError("invalid_amendment_input")
    retained = repo.get_amendment_input(reference.snapshot_id)
    if retained is None or retained.question_sha256 != reference.question_sha256:
        raise ValueError("invalid_amendment_input")
    return AmendmentInputAvailability(
        state="available",
        root_content_id=root.content_id,
        result_id=result.result_id,
        snapshot=retained,
        reference=reference,
    )
