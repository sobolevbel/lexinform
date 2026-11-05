import datetime as dt
import hashlib
from typing import Literal
from zoneinfo import ZoneInfo

import pytest
from tools.work_doctor import work_ages

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.intent_ages import intent_ages
from lexinform.models import (
    BatchIntent,
    BatchRequest,
    CommandOutcome,
    IncomingCommand,
    NameContext,
    NameQuestion,
    OutcomeStatus,
    StatusSnapshot,
)
from lexinform.saved_clock import saved_clock_age
from tests.formatting import assert_telegram_html
from tests.unit.test_input_receipts import meta, request

NOW = dt.datetime(2026, 11, 5, tzinfo=dt.UTC)


def intent(
    created: dt.datetime,
    state: Literal["queued", "submitting"] = "queued",
    *,
    name: bool = False,
    number: str = "3039",
) -> BatchIntent:
    req = (
        BatchRequest(
            custom_id="private-id",
            term=10,
            number=number,
            question=NameQuestion(ctx=NameContext(number=number, title="SECRET", summary="BODY")),
        )
        if name
        else request()
    )
    return BatchIntent(request=req, meta=meta(), provider="openai", created_at=created, state=state)


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (None, "missing_timestamp"),
        (42, "missing_timestamp"),
        ("", "missing_timestamp"),
        ("invalid", "invalid_timestamp"),
        (NOW.replace(tzinfo=None), "timezone_unknown"),
        (NOW + dt.timedelta(seconds=1), "timestamp_after_observation"),
    ],
)
def test_saved_clock_unknowns(value: object, reason: str) -> None:
    age = saved_clock_age(value, NOW)
    assert age.seconds is None and age.unknown == reason


def test_clock_whole_seconds_offsets_and_naive_observation() -> None:
    assert saved_clock_age("2026-11-05T00:59:58.500+01:00", NOW).seconds == 1
    with pytest.raises(ValueError, match="Observed time must include a timezone"):
        saved_clock_age(None, NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="Observed time must include a timezone"):
        intent_ages((), NOW.replace(tzinfo=None))


def test_intent_rows_group_by_purpose_and_state_without_bill_deduplication() -> None:
    old = NOW - dt.timedelta(days=2)
    groups = intent_ages(
        (
            intent(old),
            intent(NOW),
            intent(old, "submitting"),
            intent(NOW, name=True),
            intent(NOW.replace(tzinfo=None), name=True),
            intent(NOW + dt.timedelta(days=1), name=True),
        ),
        NOW,
    )
    assert [(g.purpose, g.state, g.count, g.known_ages, g.oldest_seconds) for g in groups] == [
        ("analysis", "queued", 2, 2, 172800),
        ("analysis", "submitting", 1, 1, 172800),
        ("name", "queued", 3, 1, 0),
    ]
    assert groups[2].unknown_ages == {"timestamp_after_observation": 1, "timezone_unknown": 1}
    assert groups[0].oldest_number == "3039" and groups[0].oldest_term == 10


def render(snapshot: StatusSnapshot) -> str:
    command = IncomingCommand(
        update_id=1, chat_id="tech", message_id=2, text="/status", received_at=NOW
    )
    return (
        MessageFormatter("ru")
        .command_reply(command, CommandOutcome(status=OutcomeStatus.REPORTED, snapshot=snapshot))
        .text
    )


def test_status_render_is_bounded_escaped_and_labels_creation_age_and_uncertainty() -> None:
    groups = intent_ages(
        (
            intent(NOW - dt.timedelta(hours=25), "submitting", name=True, number="<x>" * 100),
            intent(NOW.replace(tzinfo=None), name=True),
        ),
        NOW,
    )
    text = render(StatusSnapshot(intent_ages=groups, uncertain_intents=1))
    assert_telegram_html(text)
    assert "since intent creation" in text and "submission uncertain" in text
    assert "25.0 h" in text and "timezone_unknown=1" in text
    assert "&lt;x&gt;" in text and "<x>" not in text
    assert "SECRET" not in text and "BODY" not in text and "private-id" not in text
    many = render(StatusSnapshot(intent_ages=groups * 10))
    assert "14 more group(s)" in many and len(many) < 2500


def test_quiet_status_and_snapshot_bytes_are_unchanged() -> None:
    assert hashlib.sha256(StatusSnapshot().model_dump_json().encode()).hexdigest() == (
        "7e3fdd0ea1e4a6607b581de18bf98aed6e51724cc7d4a5117da825118a5bdc01"
    )
    assert hashlib.sha256(render(StatusSnapshot()).encode()).hexdigest() == (
        "9703e5fd3ba3a11afefcbed13525dcaf6f34ae73950ed471669a50e4e1253065"
    )


def test_same_timezone_dst_fold_counts_elapsed_seconds() -> None:
    zone = ZoneInfo("Europe/Warsaw")
    started = dt.datetime(2026, 10, 25, 2, 30, tzinfo=zone, fold=0)
    observed = started.replace(fold=1)
    assert saved_clock_age(started, observed).seconds == 3600


def test_all_unknown_group_has_no_oldest_owner() -> None:
    (group,) = intent_ages((intent(NOW.replace(tzinfo=None), name=True),), NOW)
    assert group.known_ages == 0 and group.oldest_seconds is None
    assert group.oldest_number is None and group.oldest_term is None
    assert group.unknown_ages == {"timezone_unknown": 1}


def test_doctor_keeps_nonstring_saved_sql_clock_unknown() -> None:
    result = work_ages(
        [{"kind": "llm_intent", "state": "queued", "ref": "intent", "created_at": NOW}],
        NOW,
        complete=True,
    )
    assert result["groups"][0]["unknown_ages"] == {"missing_timestamp": 1}


@pytest.mark.parametrize(
    ("value", "seconds", "unknown"),
    [
        ("0001-01-01T00:00:00+14:00", 63_929_484_000, None),
        ("9999-12-31T23:59:00-14:00", None, "timestamp_after_observation"),
    ],
)
def test_supported_date_edges_do_not_overflow_when_offsets_cross_the_datetime_range(
    value: str, seconds: int | None, unknown: str | None
) -> None:
    age = saved_clock_age(value, NOW)
    assert age.seconds == seconds and age.unknown == unknown
    result = work_ages(
        [{"kind": "llm_intent", "state": "queued", "ref": "intent", "created_at": value}],
        NOW,
        complete=True,
    )
    assert result["groups"][0]["oldest_seconds"] == seconds
    assert result["groups"][0]["unknown_ages"] == ({unknown: 1} if unknown else {})
