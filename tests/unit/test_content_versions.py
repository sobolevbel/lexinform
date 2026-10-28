import pytest

from lexinform.content_versions import confirmed_content_version


@pytest.mark.parametrize(
    ("parents", "confirmed", "expected", "ambiguous"),
    [
        (
            {"original": None, "first": "original", "second": "first"},
            {"original", "first", "second"},
            "second",
            False,
        ),
        (
            {"original": None, "first": "original", "second": "first"},
            {"original", "first"},
            "first",
            False,
        ),
        ({"original": None, "first": "original"}, {"first"}, None, False),
        (
            {"original": None, "left": "original", "right": "original"},
            {"original", "left", "right"},
            None,
            True,
        ),
        ({"first": "missing"}, {"first"}, None, False),
        ({"first": "second", "second": "first"}, {"first", "second"}, None, False),
        ({"original": None, "other": None}, {"original", "other"}, None, True),
        ({"original": None}, set(), None, False),
    ],
)
def test_confirmation(
    parents: dict[str, str | None], confirmed: set[str], expected: str | None, ambiguous: bool
) -> None:
    result = confirmed_content_version(parents, confirmed)
    assert result.current_id == expected and result.ambiguous == ambiguous
