import json
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import httpx2 as httpx
import pytest

from lexinform.adapters.telegram import TelegramBotClient, TelegramPublisher
from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.errors import StateCheckpointError
from lexinform.models import PublicationStatus
from lexinform.services import posting
from tests.checkpoint_state import saved_state
from tests.harness import CHANNEL, World
from tests.scenario.test_card_remote_checkpoints import connect, git
from tests.scenario.test_group_remote_checkpoints import flush, queued_world, restored, rows

CHAT_ID = -1001234567890
MESSAGE_ID = 735


@contextmanager
def telegram_send(
    world: World, monkeypatch: pytest.MonkeyPatch, chat: object
) -> Iterator[list[str]]:
    methods: list[str] = []

    def transport(request: httpx.Request) -> httpx.Response:
        methods.append(request.url.path.rsplit("/", 1)[-1])
        body = json.loads(request.content)
        assert body["chat_id"] == CHANNEL
        return httpx.Response(
            200, json={"ok": True, "result": {"message_id": MESSAGE_ID, "chat": chat}}
        )

    with closing(TelegramBotClient("fixture", transport=httpx.MockTransport(transport))) as client:
        publisher = TelegramPublisher(client, MessageFormatter(), channel_id=CHANNEL)
        monkeypatch.setattr(world.publisher, "publish_updates", publisher.publish_updates)
        yield methods


@pytest.mark.parametrize(
    ("chat", "history", "expected"),
    [
        ({"id": CHAT_ID}, True, CHAT_ID),
        (None, True, None),
        ({"id": 123}, True, None),
        ({"id": CHAT_ID}, False, None),
    ],
)
def test_grouped_send_pins_only_the_verified_same_response_chat(
    monkeypatch: pytest.MonkeyPatch, chat: object, history: bool, expected: int | None
) -> None:
    world, identities = queued_world("updates")
    if not history:
        monkeypatch.setattr(posting, "record_grouped_stage_content", lambda *args, **kwargs: None)
    with telegram_send(world, monkeypatch, chat) as methods:
        flush(world, "updates", identities)
    assert methods == ["sendMessage"]

    before = world.clock.now() + timedelta(days=1)
    world.repo.prune_history(before=before, memo_before=before)
    dumped = world.repo.dump()
    for _ in range(2):
        with saved_state(dumped) as fresh:
            members = rows(fresh, identities)
            assert all(
                member.status is PublicationStatus.SENT
                and member.telegram_chat_id == expected
                and member.message_id == MESSAGE_ID
                and member.channel_id == CHANNEL
                and member.delivery is None
                for member in members
            )
            assert members[0].sent_at == members[1].sent_at
            assert fresh.repo.dump() == dumped


def test_partial_cas_after_accepted_send_leaves_no_retryable_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, identities = queued_world("updates")
    remote = connect(world, tmp_path)
    pin = world.repo.pin_grouped_telegram_receipt

    def reject_second(
        publication_ids: tuple[int, int],
        *,
        content_id: str,
        chat_id: int,
        message_id: int,
        sent_at: datetime,
    ) -> None:
        pin(
            (publication_ids[0], -1),
            content_id=content_id,
            chat_id=chat_id,
            message_id=message_id,
            sent_at=sent_at,
        )

    monkeypatch.setattr(world.repo, "pin_grouped_telegram_receipt", reject_second)
    with (
        telegram_send(world, monkeypatch, {"id": CHAT_ID}) as methods,
        pytest.raises(StateCheckpointError, match="no longer owns both"),
    ):
        flush(world, "updates", identities)
    assert methods == ["sendMessage"]
    assert all(
        row.status is PublicationStatus.PENDING
        and row.telegram_chat_id is None
        and not row.attempts
        for row in rows(world, identities)
    )
    with saved_state(git(remote, "show", "state:state.sql")) as durable:
        assert all(row.status is PublicationStatus.PENDING for row in rows(durable, identities))
    fresh = restored(remote, world, tmp_path)
    fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())
    assert all(row.status is PublicationStatus.UNKNOWN for row in rows(fresh, identities))
    fresh.container.delivery_service(dry_run=False).run()
    assert not fresh.publisher.sent
