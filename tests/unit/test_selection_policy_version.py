import pytest
from pydantic import ValidationError

from lexinform.models import DocumentPart, DocumentSelection


def test_legacy_selection_keeps_unknown_policy_and_identical_json_shape() -> None:
    raw = DocumentSelection().model_dump(mode="json")
    assert "policy_version" not in raw
    restored = DocumentSelection.model_validate(raw)
    assert restored.policy_version is None and restored.model_dump(mode="json") == raw


def test_future_policy_identifier_survives_roundtrip_and_text_removal() -> None:
    selection = DocumentSelection(
        policy_version="future-policy-v42:opaque",
        parts=(DocumentPart(text="Art. 1. Cudzoziemiec składa wniosek."),),
        observed_parts=(DocumentPart(text="Załącznik"),),
    )
    restored = DocumentSelection.model_validate_json(selection.model_dump_json())
    stripped = restored.without_text()
    assert stripped.policy_version == "future-policy-v42:opaque"
    assert stripped.parts[0].text == stripped.observed_parts[0].text == ""
    assert restored.parts[0].text == selection.parts[0].text


@pytest.mark.parametrize("state", ["unverified", "waiting", "ambiguous", "unsupported", "selected"])
def test_policy_identifier_does_not_invent_selected_evidence(state: str) -> None:
    selection = DocumentSelection.model_validate(
        {"state": state, "policy_version": "future-policy-v42"}
    )
    assert selection.state == ("unverified" if state == "selected" else state)
    assert selection.policy_version == "future-policy-v42"


@pytest.mark.parametrize("version", ["", " ", "two versions", "x" * 129])
def test_policy_identifier_is_bounded_metadata(version: str) -> None:
    with pytest.raises(ValidationError):
        DocumentSelection(policy_version=version)
