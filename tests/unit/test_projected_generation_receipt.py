from typing import Literal

import pytest
from pydantic import ValidationError

from lexinform.models import ResultApplication

LEGACY = (
    '{"result_id":"result-v1:dc0885540fbe6a2ac97e684e35b67087498b5e0da1261f50ecff9808e331f06c",'
    '"term":10,"number":"3039","generation":0,"phase":"projection","outcome":"applied",'
    '"reason":"batch_result","observed_generation":0,"observed_status":null,'
    '"created_at":"2026-10-27T00:00:00Z"}'
)


def test_legacy_and_explicit_none_keep_exact_receipt_json_and_identity() -> None:
    original = ResultApplication.model_validate_json(LEGACY)
    explicit = ResultApplication.model_validate(
        {**original.model_dump(), "projected_generation": None}
    )
    assert original.projected_generation is None
    assert original.model_dump_json() == explicit.model_dump_json() == LEGACY
    assert original == explicit
    assert "projected_generation" not in original.model_dump()


@pytest.mark.parametrize("projected", [0, 7])
def test_primary_projection_reader_keeps_witness_separate_from_receipt_identity(
    projected: int,
) -> None:
    original = ResultApplication.model_validate_json(LEGACY)
    witnessed = ResultApplication.model_validate(
        {**original.model_dump(), "projected_generation": projected}
    )
    assert witnessed.projected_generation == projected
    assert witnessed.generation == original.generation == 0
    assert witnessed.observed_generation == original.observed_generation == 0
    assert witnessed.model_dump(exclude={"projected_generation"}) == original.model_dump()
    assert ResultApplication.model_validate_json(witnessed.model_dump_json()) == witnessed


@pytest.mark.parametrize("value", [-1, True, "1", 1.5])
def test_projected_generation_requires_a_nonnegative_integer(value: object) -> None:
    original = ResultApplication.model_validate_json(LEGACY)
    with pytest.raises(ValidationError):
        ResultApplication.model_validate({**original.model_dump(), "projected_generation": value})


@pytest.mark.parametrize("fault", ["collection", "target", "generation", "outcome"])
def test_projected_generation_cannot_claim_a_nonprimary_or_unapplied_receipt(
    fault: Literal["collection", "target", "generation", "outcome"],
) -> None:
    raw = ResultApplication.model_validate_json(LEGACY).model_dump()
    raw["projected_generation"] = 1
    if fault == "collection":
        raw.update(phase="collection", outcome="ready", reason="current_generation")
    elif fault == "target":
        raw.update(generation=None, target={"kind": "status_change", "ref": "1"})
    elif fault == "generation":
        raw["generation"] = None
    else:
        raw.update(outcome="superseded", reason="generation_changed")
    with pytest.raises(ValidationError, match="applied primary projection"):
        ResultApplication.model_validate(raw)
