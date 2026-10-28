import datetime as dt
from typing import Any

import pytest
from tools.work_doctor import work_ages


def owner(kind: str, state: str, ref: str, timestamp: str | None) -> dict[str, Any]:
    field = "created_at" if kind == "document" else "since"
    return {"kind": kind, "state": state, "ref": ref, field: timestamp}


def test_active_work_uses_saved_creation_time_and_excludes_retained_terminal_rows() -> None:
    owners = [
        owner("document", "waiting", "doc", "2026-10-27T00:00:00+00:00"),
        owner("review", "context", "ready", "2026-10-28T00:00:00+01:00"),
        owner("review", "snapshot", "input", "2026-10-28T00:00:00+00:00"),
        owner("document", "complete", "complete", "2020-01-01T00:00:00Z"),
        owner("document", "unsupported", "unsupported", "2020-01-01T00:00:00Z"),
        owner("review", "abandoned", "abandoned", "2020-01-01T00:00:00Z"),
    ]
    observed = dt.datetime(2026, 10, 28, tzinfo=dt.UTC)

    result = work_ages(owners, observed, complete=True)

    assert result["nonterminal_owners"] == 3 and result["coverage"] == "recorded"
    assert result["observed_at"] == observed.isoformat()
    assert [(row["kind"], row["state"], row["oldest_seconds"]) for row in result["groups"]] == [
        ("document", "waiting", 86400),
        ("review", "context", 3600),
        ("review", "snapshot", 0),
    ]
    assert result["excluded_terminal_owners"] == {
        "document/complete": 1,
        "document/unsupported": 1,
        "review/abandoned": 1,
    }


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (None, "missing_timestamp"),
        ("", "missing_timestamp"),
        ("invalid", "invalid_timestamp"),
        ("2026-10-27T00:00:00", "timezone_unknown"),
        ("2026-10-29T00:00:00Z", "timestamp_after_observation"),
    ],
)
def test_unknown_and_future_clocks_do_not_become_zero_age(value: str | None, reason: str) -> None:
    observed = dt.datetime(2026, 10, 28, tzinfo=dt.UTC)

    result = work_ages([owner("document", "waiting", "unknown", value)], observed, complete=True)

    assert result["nonterminal_owners"] == 1
    assert result["groups"][0]["known_ages"] == 0
    assert result["groups"][0]["unknown_ages"] == {reason: 1}
    assert result["groups"][0]["oldest_seconds"] is None
    assert result["groups"][0]["oldest_ref"] is None


def test_oldest_owner_is_selected_only_from_known_timestamps() -> None:
    owners = [
        owner("review", "snapshot", "future", "2099-01-01T00:00:00Z"),
        owner("review", "snapshot", "old", "2026-10-27T00:00:00Z"),
        owner("review", "snapshot", "new", "2026-10-27T23:00:00Z"),
    ]

    group = work_ages(owners, dt.datetime(2026, 10, 28, tzinfo=dt.UTC), complete=True)["groups"][0]

    assert group["oldest_ref"] == "old" and group["oldest_seconds"] == 86400
    assert group["count"] == 3 and group["known_ages"] == 2
    assert group["unknown_ages"] == {"timestamp_after_observation": 1}


def test_offset_change_uses_elapsed_time_and_explicit_observation_timezone() -> None:
    owners = [owner("review", "context", "dst", "2026-10-25T02:30:00+02:00")]
    observed = dt.datetime.fromisoformat("2026-10-25T02:30:00+01:00")

    result = work_ages(owners, observed, complete=True)

    assert result["groups"][0]["oldest_seconds"] == 3600
    assert result["observed_at"] == "2026-10-25T01:30:00+00:00"


def test_partial_inventory_keeps_its_coverage_limit_even_with_no_nonterminal_owners() -> None:
    observed = dt.datetime(2026, 10, 28, tzinfo=dt.UTC)

    assert work_ages([], observed, complete=False)["coverage"] == "partial"
    assert work_ages([], observed, complete=True)["coverage"] == "recorded"


def test_observation_without_timezone_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone"):
        work_ages([], dt.datetime(2026, 10, 28), complete=True)
