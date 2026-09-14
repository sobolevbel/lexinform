"""Every language carries the same set of labels, so the formatter never falls back silently."""

import dataclasses

import pytest

from lexinform.i18n import EN, LABELS, RU, Labels, labels_for

TRANSLATED_MAPPINGS = tuple(
    field.name for field in dataclasses.fields(Labels) if isinstance(getattr(RU, field.name), dict)
)
"""Every `dict[str, str]` field of `Labels`, found rather than listed.

A hand-written list is a list that falls behind: seven of the eighteen were missing from it,
`update_headers` — which names every status update — among them, and each new mapping was one
more chance to forget a line here."""


@pytest.mark.parametrize("mapping", TRANSLATED_MAPPINGS)
def test_translated_mappings_have_the_same_keys(mapping: str) -> None:
    assert set(getattr(RU, mapping)) == set(getattr(EN, mapping)), mapping


def test_every_translated_mapping_is_checked() -> None:
    """The parametrisation finds them, so this only guards the finding itself: a `Labels` with no
    mappings at all would make the test above pass by running nothing."""
    assert len(TRANSLATED_MAPPINGS) >= 18
    assert "update_headers" in TRANSLATED_MAPPINGS


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
