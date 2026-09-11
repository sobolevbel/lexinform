"""The relay: which posts become inbox files, what is acknowledged, what is confirmed."""

from lexinform.services.listener import CommandListener
from tests.fakes import FakeAcknowledger, FakeInboxWriter, FakeUpdates, channel_post

CHANNEL = "-1001"


def _listener(
    updates: FakeUpdates, writer: FakeInboxWriter | None, ack: FakeAcknowledger | None = None
) -> CommandListener:
    return CommandListener(
        updates, writer, ack, channel_id=CHANNEL, poll_timeout=5, sleep=lambda _: None
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


def test_a_command_that_could_not_be_filed_is_not_confirmed() -> None:
    updates = FakeUpdates([channel_post(1, "/help"), channel_post(2, "/analyze 3039")])
    writer = FakeInboxWriter(fail=True)
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
    listener = _listener(updates, FakeInboxWriter())

    listener.run_forever(stop=lambda: next(checks) >= 3)

    assert len(updates.offsets) == 3  # three polls, each an outage, none fatal
