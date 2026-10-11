from dataclasses import replace

import httpx2 as httpx

from lexinform.adapters.telegram import TelegramBotClient, TelegramPublisher
from lexinform.models import PublicationStatus
from tests.fakes import MonotonicClock
from tests.harness import CHANNEL, World

TITLE = "Projekt ustawy o cudzoziemcach"


def test_a_lost_send_response_stays_unknown_until_an_audited_retry() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    clock = MonotonicClock()
    payloads: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(request.content)
        if len(payloads) == 1:
            raise httpx.ReadTimeout("Telegram accepted the message but its response was lost")
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 77}})

    client = TelegramBotClient(
        "TOKEN",
        transport=httpx.MockTransport(handler),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    container = replace(
        w.container, publisher_override=TelegramPublisher(client, w.formatter, channel_id=CHANNEL)
    )
    w.pipeline = container.pipeline(dry_run=False)

    w.run()
    pub = w.publication("3039")
    assert pub is not None and pub.id is not None and pub.delivery is not None
    assert pub.status is PublicationStatus.UNKNOWN and pub.attempts == 0
    assert len(payloads) == 1

    w.repo.restore(w.repo.dump())
    w.pipeline = replace(container).pipeline(dry_run=False)
    w.run()
    assert len(payloads) == 1

    w.command(f"/delivery {pub.id} retry")
    w.run()
    resolved = w.publication("3039")
    assert resolved is not None and resolved.status is PublicationStatus.SENT
    assert resolved.message_id == 77
    assert len(payloads) == 2 and payloads[0] == payloads[1]
    assert len(w.repo.delivery_resolutions(pub.id)) == 1


def test_flood_rejection_does_not_spend_publication_attempts_and_can_retry() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    clock = MonotonicClock()
    payloads: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(request.content)
        if len(payloads) <= 4:
            return httpx.Response(
                429,
                json={"ok": False, "error_code": 429, "parameters": {"retry_after": 42}},
            )
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 77}})

    client = TelegramBotClient(
        "TOKEN",
        transport=httpx.MockTransport(handler),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    container = replace(
        w.container, publisher_override=TelegramPublisher(client, w.formatter, channel_id=CHANNEL)
    )
    w.pipeline = container.pipeline(dry_run=False)

    w.run()
    pub = w.publication("3039")
    assert pub is not None and pub.status is PublicationStatus.FAILED and pub.attempts == 0
    assert len(payloads) == 4

    w.run()
    retried = w.publication("3039")
    assert retried is not None and retried.status is PublicationStatus.SENT
    assert len(payloads) == 5 and len(set(payloads)) == 1
    assert clock.monotonic() >= 4 * 42
