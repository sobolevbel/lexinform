"""Every language carries the same set of labels, so the formatter never falls back silently."""

import pytest

from lexinform.i18n import EN, LABELS, RU, labels_for


@pytest.mark.parametrize(
    "mapping",
    [
        "event_tags",
        "next_step_labels",
        "typical_durations",
        "urgent_step_labels",
        "urgent_durations",
        "no_action_labels",
        "path_steps",
        "stage_labels",
        "rcl_stage_labels",
        "stage_type_labels",
        "senate_position_labels",
        "score_labels",
        "category_labels",
        "category_tags",
        "applicant_labels",
    ],
)
def test_translated_mappings_have_the_same_keys(mapping: str) -> None:
    assert set(getattr(RU, mapping)) == set(getattr(EN, mapping)), mapping


def test_tags_contain_no_spaces() -> None:
    for labels in LABELS.values():
        tags = [
            labels.tag_importance,
            labels.tag_consultations,
            labels.tag_term,
            labels.tag_ukraine,
            labels.tag_published,
            labels.tag_in_force,
            labels.tag_committee_sitting,
            labels.tag_sejm_sitting,
            *labels.event_tags.values(),
            *labels.category_tags.values(),
        ]
        assert all(" " not in tag for tag in tags), tags


def test_unknown_language_falls_back_to_english() -> None:
    assert labels_for("xx") is EN
    assert labels_for("RU") is RU
