import datetime as dt
import json
import sqlite3
from collections.abc import Iterator
from typing import Any

import pytest
from tools.work_doctor import llm_work_ages, work_ages

from lexinform.models import BatchRequest, NameContext, NameQuestion


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


@pytest.fixture
def queue_db() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        "CREATE TABLE llm_batch_intents (custom_id, provider, request_json, state, created_at);"
        "CREATE TABLE llm_batch_items (batch_id, custom_id, call_kind, term, number, consumed_at);"
        "CREATE TABLE llm_batches (batch_id, provider, status, submitted_at);"
    )
    try:
        yield connection
    finally:
        connection.close()


@pytest.mark.parametrize("legacy", [False, True])
def test_queue_phases_count_physical_retry_items_and_only_exact_intent_overlap(
    queue_db: sqlite3.Connection,
    legacy: bool,
) -> None:
    created = "2026-10-27T00:00:00Z"
    request_model = BatchRequest(
        custom_id="same",
        term=10,
        number="123",
        question=NameQuestion(ctx=NameContext(number="123", title="Title", summary="Body marker")),
    )
    request = request_model.model_dump_json()
    if legacy:
        payload = request_model.model_dump(mode="json")
        payload.pop("question")
        payload.update(
            call_kind=request_model.call_kind, ctx=request_model.ctx.model_dump(mode="json")
        )
        request = json.dumps(payload)
    queue_db.executemany(
        "INSERT INTO llm_batches VALUES (?, 'openai', ?, ?)",
        [("first", "submitted", created), ("retry", "ended", created), ("old", "failed", created)],
    )
    queue_db.executemany(
        "INSERT INTO llm_batch_items VALUES (?, ?, 'name', 10, '123', ?)",
        [("first", "same", None), ("retry", "same", None), ("old", "new", created)],
    )
    queue_db.executemany(
        "INSERT INTO llm_batch_intents VALUES (?, 'openai', ?, ?, ?)",
        [("same", request, "submitting", created), ("new", request, "queued", created)],
    )

    report = llm_work_ages(queue_db, dt.datetime(2026, 10, 28, tzinfo=dt.UTC))

    assert report["nonterminal_work_items"] == 3
    assert report["suppressed_submitted_intents"] == 1
    assert report["coverage"] == "recorded"
    assert [(row["phase"], row["count"], row["oldest_seconds"]) for row in report["groups"]] == [
        ("waiting_submission", 1, 86400),
        ("waiting_local_consumption", 1, 86400),
        ("waiting_provider", 1, 86400),
    ]
    assert report["groups"][1]["timestamp_field"] == "submitted_at"
    assert "Body marker" not in json.dumps(report)


@pytest.mark.parametrize("malformed", [False, True])
def test_conflicting_intent_owner_is_not_hidden(
    queue_db: sqlite3.Connection, malformed: bool
) -> None:
    request = BatchRequest(
        custom_id="same",
        term=10,
        number="2",
        question=NameQuestion(ctx=NameContext(number="2", title="Title", summary="Summary")),
    ).model_dump_json()
    if malformed:
        request = "{}"
    stamp = "2026-10-27T00:00:00Z"
    queue_db.execute("INSERT INTO llm_batches VALUES ('batch', 'openai', 'failed', ?)", (stamp,))
    queue_db.execute("INSERT INTO llm_batch_items VALUES ('batch', 'same', 'name', 10, '1', NULL)")
    queue_db.execute(
        "INSERT INTO llm_batch_intents VALUES ('same', 'openai', ?, 'submitting', ?)",
        (request, stamp),
    )

    report = llm_work_ages(queue_db, dt.datetime(2026, 10, 28, tzinfo=dt.UTC))

    assert report["nonterminal_work_items"] == 2
    assert report["coverage"] == "partial"
    assert report["conflicting_or_unknown_intent_owners"] == ["llm_batch_intents/same"]


@pytest.mark.parametrize(
    ("stamp", "reason"),
    [
        (None, "missing_timestamp"),
        ("2026-10-27", "timezone_unknown"),
        ("2026-10-29T00:00:00Z", "timestamp_after_observation"),
    ],
)
def test_llm_queue_unknown_clocks_remain_unknown(
    queue_db: sqlite3.Connection, stamp: str | None, reason: str
) -> None:
    queue_db.execute("INSERT INTO llm_batches VALUES ('b', 'openai', 'ended', ?)", (stamp,))
    queue_db.execute("INSERT INTO llm_batch_items VALUES ('b', 'item', 'analysis', 10, '1', NULL)")
    queue_db.execute(
        "INSERT INTO llm_batch_intents VALUES ('intent', 'openai', '{}', 'queued', ?)", (stamp,)
    )

    report = llm_work_ages(queue_db, dt.datetime(2026, 10, 28, tzinfo=dt.UTC))

    assert report["nonterminal_work_items"] == 2
    assert all(group["unknown_ages"] == {reason: 1} for group in report["groups"])
    assert all(group["oldest_seconds"] is None for group in report["groups"])


def test_missing_llm_table_keeps_available_intents_and_partial_coverage(
    queue_db: sqlite3.Connection,
) -> None:
    queue_db.execute("DROP TABLE llm_batches")
    queue_db.execute(
        "INSERT INTO llm_batch_intents VALUES ('i', 'openai', '{}', 'queued', '2026-10-27T00:00:00Z')"
    )

    report = llm_work_ages(queue_db, dt.datetime(2026, 10, 28, tzinfo=dt.UTC))

    assert report["coverage"] == "partial"
    assert report["nonterminal_work_items"] == 1
    assert "llm_batches.submitted_at" in report["missing_schema"]


def test_orphan_provider_item_has_unknown_phase_without_mutating_snapshot(
    queue_db: sqlite3.Connection,
) -> None:
    queue_db.execute(
        "INSERT INTO llm_batch_items VALUES ('missing', 'item', 'analysis', 10, '1', NULL)"
    )
    before = tuple(queue_db.iterdump())

    report = llm_work_ages(queue_db, dt.datetime(2026, 10, 28, tzinfo=dt.UTC))

    assert report["coverage"] == "partial"
    assert report["unknown_provider_items"] == ["llm_batch_items/missing/item"]
    assert report["groups"][0]["phase"] == "unknown"
    assert report["groups"][0]["unknown_ages"] == {"missing_timestamp": 1}
    assert tuple(queue_db.iterdump()) == before


def test_llm_queue_requires_explicit_observation_timezone(queue_db: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="timezone"):
        llm_work_ages(queue_db, dt.datetime(2026, 10, 28))
