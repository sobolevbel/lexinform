import pytest
from pydantic import ValidationError

from lexinform.models.extractions import ExtractionResult
from tests.unit.test_word_view_status import result


@pytest.mark.parametrize(
    "control", ["valid", "wrong_owner", "wrong_manifest", "outside", "duplicate", "overlap"]
)
def test_word_field_prefix_binds_ranges_to_the_word_summary(control: str) -> None:
    legacy = result()
    payload = legacy.model_dump(mode="json")
    (part,) = payload["selected_parts"]
    ranges: list[list[object]] = [[[1, 0], "after_separator", 1, 4, "a" * 64]]
    if control == "duplicate":
        ranges.append(ranges[0])
    elif control == "overlap":
        ranges.append([[2, 0], "after_separator", 3, 5, "b" * 64])
    elif control == "outside":
        ranges[0][3] = 11
    part["word_field_prefix"] = {
        "input_sha256": "a" * 64 if control == "wrong_owner" else part["sha256"],
        "manifest_sha256": "b" * 64 if control == "wrong_manifest" else part["manifest_sha256"],
        "paragraphs": [
            {"path": [0, 1], "text_sha256": "c" * 64, "text_chars": 10, "ranges": ranges}
        ],
    }

    if control == "valid":
        stored = ExtractionResult.model_validate(payload)
        assert ExtractionResult.model_validate_json(stored.model_dump_json()) == stored
        assert "word_field_prefix" not in legacy.model_dump_json()
        assert stored.result_id != legacy.result_id
    else:
        with pytest.raises(ValidationError):
            ExtractionResult.model_validate(payload)


def test_word_field_prefix_uses_a_shared_extraction_budget() -> None:
    payload = result().model_dump(mode="json")
    (part,) = payload["selected_parts"]
    part["word_field_prefix"] = {
        "input_sha256": part["sha256"],
        "manifest_sha256": part["manifest_sha256"],
        "paragraphs": [
            {
                "path": [0, 1],
                "text_sha256": "c" * 64,
                "text_chars": 24,
                "ranges": [
                    [[index], "after_separator", index, index + 1, "a" * 64] for index in range(24)
                ],
            }
        ],
    }
    ExtractionResult.model_validate(payload)
    payload["selected_parts"].append({**part, "physical_index": 1})
    payload.update(physical_part_count=2, selected_part_count=2)

    with pytest.raises(ValidationError, match="aggregate byte limit"):
        ExtractionResult.model_validate(payload)
