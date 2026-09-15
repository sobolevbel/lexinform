"""The relay: which posts become inbox files, what is acknowledged, what is confirmed."""

from collections.abc import Callable

import pytest

from lexinform.errors import ServiceUnavailableError
from lexinform.services.listener import CommandListener
from tests.fakes import FakeAcknowledger, FakeInboxWriter, FakeUpdates, channel_post
from tests.harness import World

CHANNEL = "-1001"


def _listener(
    updates: FakeUpdates,
    writer: FakeInboxWriter | None,
    ack: FakeAcknowledger | None = None,
    *,
    slept: list[float] | None = None,
    max_retry_delay: float = 600.0,
) -> CommandListener:
    record: Callable[[float], None] = slept.append if slept is not None else lambda _: None
    return CommandListener(
        updates,
        writer,
        ack,
        starter=writer,
        channel_id=CHANNEL,
        poll_timeout=5,
        max_retry_delay=max_retry_delay,
        sleep=record,
    )


def test_commands_from_the_technical_channel_are_filed_acknowledged_and_confirmed() -> None:
    updates = FakeUpdates(
        [
            channel_post(1, "/analyze 3039"),
            channel_post(2, "a note for the team"),  # not a command
            channel_post(3, "/show 3039", chat_id=-2002),  # another channel the bot is in
            channel_post(4, None),  # a photo
            channel_post(5, "/help"),
        ]
    )
    writer, ack = FakeInboxWriter(), FakeAcknowledger()
    listener = _listener(updates, writer, ack)

    filed = listener.poll_once()
    listener.confirm()

    assert filed == 2
    assert [c.text for c in writer.filed] == ["/analyze 3039", "/help"]
    assert writer.filed[0].chat_id == CHANNEL and writer.filed[0].message_id == 501
    assert ack.acknowledged == [1, 5]
    assert listener.offset == 6
    assert updates.offsets == [None, 6]  # the confirming poll names the next offset


def test_run_starts_the_workflow_instead_of_being_filed() -> None:
    """`/run` is the one command a run cannot execute, because it is the run: the relay asks
    GitHub to start the workflow with the inputs the operator named and says so under the post.
    """
    updates = FakeUpdates([channel_post(1, "/run since=2026-09-01 dry")])
    writer, ack = FakeInboxWriter(), FakeAcknowledger()

    filed = _listener(updates, writer, ack).poll_once()

    assert filed == 1
    assert writer.filed == []  # nothing in the inbox: there is nothing for a run to execute
    assert writer.started == [{"since": "2026-09-01", "dry_run": "true"}]
    assert ack.acknowledged == [1]
    assert "since=2026-09-01, dry_run=true" in ack.started_notes[0]


def test_every_phase_of_a_run_starts_the_workflow_and_is_not_filed() -> None:
    """`/scan`, `/track`, `/reprefilter` and `/index-rcl-numbers` are runs like `/run` is: each
    names the CLI command `daily.yml` executes, so none of them ever reaches the inbox."""
    posts = [
        channel_post(1, "/scan since=2026-09-01"),
        channel_post(2, "/track dry"),
        channel_post(3, "/reprefilter limit=200 text_skipped"),
        channel_post(4, "/index-rcl-numbers since=2023-11-01"),
    ]
    writer, ack = FakeInboxWriter(), FakeAcknowledger()

    filed = _listener(FakeUpdates(posts), writer, ack).poll_once()

    assert filed == 4 and writer.filed == []
    assert writer.started == [
        {"command": "scan", "options": "--since 2026-09-01"},
        {"command": "track", "dry_run": "true"},
        {"command": "reprefilter", "options": "--limit 200 --include-text-skipped"},
        {"command": "index-rcl-numbers", "options": "--since 2023-11-01"},
    ]
    assert ack.started_notes[0].startswith("scan started")


def test_a_misspelled_run_option_is_answered_and_starts_nothing() -> None:
    """Telegram redelivers an update until the offset moves past it, so a command that can never
    succeed has to be answered rather than retried."""
    updates = FakeUpdates([channel_post(1, "/run turbo")])
    writer, ack = FakeInboxWriter(), FakeAcknowledger()

    listener = _listener(updates, writer, ack)
    filed = listener.poll_once()

    assert (filed, writer.started, listener.offset) == (1, [], 2)
    assert "unknown option turbo" in ack.started_notes[0]


def test_a_run_that_github_would_not_start_is_tried_again() -> None:
    updates = FakeUpdates([channel_post(1, "/run")])
    writer = FakeInboxWriter(error=ServiceUnavailableError("GitHub API is down"))

    listener = _listener(updates, writer)
    filed = listener.poll_once()

    assert (filed, listener.offset) == (0, None)  # the update is delivered again


def test_a_command_that_could_not_be_filed_is_not_confirmed() -> None:
    updates = FakeUpdates([channel_post(1, "/help"), channel_post(2, "/analyze 3039")])
    writer = FakeInboxWriter(error=ServiceUnavailableError("PUT inbox: HTTP 503"))
    listener = _listener(updates, writer)

    filed = listener.poll_once()
    listener.confirm()

    assert filed == 0 and writer.filed == []
    assert listener.offset is None  # Telegram delivers update 1 again next time
    assert updates.offsets == [None]  # nothing to confirm, no extra call


def test_a_channel_named_by_username_matches_case_insensitively() -> None:
    post = channel_post(1, "/help").model_copy(update={"chat_username": "LexinformLog"})
    updates = FakeUpdates([post])
    writer = FakeInboxWriter()
    listener = CommandListener(updates, writer, None, channel_id="@lexinformlog", poll_timeout=5)

    listener.poll_once()

    assert [c.update_id for c in writer.filed] == [1]


def test_a_dry_run_reads_and_reports_without_filing_or_confirming() -> None:
    updates = FakeUpdates([channel_post(1, "/analyze 3039")])
    listener = _listener(updates, None)

    filed = listener.poll_once()
    listener.confirm()

    assert filed == 1 and [p.text for p in listener.filed] == ["/analyze 3039"]
    assert updates.offsets == [None]  # no confirming poll


def test_run_forever_survives_an_outage_and_stops_when_told() -> None:
    updates = FakeUpdates(outage=True)
    checks = iter(range(10))
    slept: list[float] = []
    listener = _listener(updates, FakeInboxWriter(), slept=slept)

    listener.run_forever(stop=lambda: next(checks) >= 3)

    assert len(updates.offsets) == 3  # three polls, each an outage, none fatal
    assert slept == [15.0, 30.0, 60.0]


def test_run_forever_waits_when_a_command_cannot_be_filed_at_all() -> None:
    """A refusal no retry fixes (a revoked token) must not turn the relay into a hot loop:
    `poll_once` returns without raising, and Telegram answers at once with the same update."""
    posts = [channel_post(1, "/analyze 3039")]
    updates = FakeUpdates(posts, posts, posts, posts)
    checks = iter(range(10))
    slept: list[float] = []
    writer = FakeInboxWriter(error=RuntimeError("PUT inbox: HTTP 401: Bad credentials"))
    listener = _listener(updates, writer, slept=slept)

    listener.run_forever(stop=lambda: next(checks) >= 4)

    assert updates.offsets == [None, None, None, None]  # the offset never moves
    assert slept == [15.0, 30.0, 60.0, 120.0]


def test_the_back_off_is_capped_and_forgotten_once_a_poll_gets_through() -> None:
    posts = [channel_post(1, "/analyze 3039")]
    updates = FakeUpdates(*([posts] * 9), [], posts)
    checks = iter(range(20))
    slept: list[float] = []
    writer = FakeInboxWriter(error=RuntimeError("PUT inbox: HTTP 401"))
    listener = _listener(updates, writer, slept=slept, max_retry_delay=240.0)

    listener.run_forever(stop=lambda: next(checks) >= 11)

    assert slept[:5] == [15.0, 30.0, 60.0, 120.0, 240.0]
    assert slept[5:9] == [240.0, 240.0, 240.0, 240.0]  # capped
    assert slept[9:] == [15.0]  # the empty poll cleared the count, the next one stalled again


def test_the_container_builds_the_relay_from_the_bot_token_and_the_log_channel_alone() -> None:
    """The relay lives in the log channel: the reader channel id (needed to post) is not."""
    w = World()
    w.container.settings.telegram_bot_token = "TOKEN"
    w.container.settings.telegram_channel_id = ""
    w.container.settings.telegram_log_channel_id = "-1001"

    listener = w.container.command_listener(dry_run=True)

    assert listener.offset is None
    w.container.settings.telegram_log_channel_id = ""
    with pytest.raises(ValueError, match="LEXINFORM_TELEGRAM_LOG_CHANNEL_ID"):
        w.container.command_listener(dry_run=True)
