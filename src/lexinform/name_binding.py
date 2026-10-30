"""Bind a newly selected editorial name to its known immutable paid source."""

from lexinform.models import AnalysisRecord, ComputationResult, Mnemonic, NameRecord, valid_mnemonic


def mnemonic_matches_result(name: Mnemonic, result: ComputationResult | None) -> bool:
    if (
        result is None
        or result.validation != "validated"
        or name.computation_id != result.result_id
        or name.request_sha256 is None
        or name.request_sha256 != result.request_sha256
    ):
        return False
    record = result.record
    if (
        record is None
        or record.computation_id != result.result_id
        or record.request_sha256 != result.request_sha256
    ):
        return False
    if isinstance(record, NameRecord) and result.purpose == "name" and name.origin == "backfill":
        candidates = (record.proposal.name, record.proposal.fallback)
        basis = record.proposal.basis
    elif (
        isinstance(record, AnalysisRecord)
        and result.purpose in ("analysis", "reanalysis")
        and name.origin == "analysis"
    ):
        candidates = (record.analysis.mnemonic_candidate, record.analysis.mnemonic_fallback)
        basis = record.analysis.mnemonic_basis
        if name.language != record.analysis.mnemonic_language:
            return False
    else:
        return False
    selected = next((candidate for candidate in candidates if valid_mnemonic(candidate)), "")
    return bool(selected) and name.name == selected and name.basis == basis


def bind_mnemonic(
    candidate: Mnemonic,
    record: AnalysisRecord | NameRecord,
    result: ComputationResult | None,
) -> Mnemonic:
    bound = candidate.model_copy(
        update={"computation_id": record.computation_id, "request_sha256": record.request_sha256}
    )
    return bound if mnemonic_matches_result(bound, result) else candidate
