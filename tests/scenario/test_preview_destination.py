import json

import httpx2 as httpx
import pytest

from lexinform.adapters.telegram import TelegramBotClient
from lexinform.models import OutcomeStatus, PublicationKind
from tests.fakes import MonotonicClock
from tests.harness import CHANNEL, World
from tests.scenario.test_commands import _commands_only


@pytest.mark.parametrize("allowed", [False, True])
def test_command_preview_checks_aliases_and_keeps_test_cards_unrecorded(
    monkeypatch: pytest.MonkeyPatch, allowed: bool
) -> None:
    world = World()
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run()
    before = tuple(line for line in world.repo.dump().splitlines() if 'INTO "publications"' in line)
    original = world.publication("3039", PublicationKind.NEW_BILL)
    expected = world.formatter.new_bill(
        world.bill("3039"), world.gateway.get_print(10, "3039")
    ).text
    requests: list[tuple[str, dict[str, object]]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        payload: dict[str, object] = json.loads(request.content)
        method = request.url.path.rsplit("/", 1)[-1]
        requests.append((method, payload))
        if method == "getChat":
            identity = -1001 if payload["chat_id"] == CHANNEL or not allowed else -1002
            return httpx.Response(200, json={"ok": True, "result": {"id": identity}})
        assert allowed and method == "sendMessage", "a reader alias must never receive a preview"
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 777}})

    clock = MonotonicClock()
    client = TelegramBotClient(
        "TOKEN", transport=httpx.MockTransport(handle), sleep=clock.sleep, monotonic=clock.monotonic
    )
    world.container.publisher_override = None
    monkeypatch.setattr(world.container, "telegram_client", lambda: client)
    world.command("/preview 3039 to=@test_alias")

    _commands_only(world)

    _, outcome = world.replier.replies[-1]
    sends = [payload for method, payload in requests if method == "sendMessage"]
    assert outcome.status is (OutcomeStatus.PREVIEWED if allowed else OutcomeStatus.ERROR)
    if allowed:
        assert len(sends) == 1
        assert sends[0]["chat_id"] == "-1002" and sends[0]["text"] == expected
        assert "reply_parameters" not in sends[0] and "reply_markup" not in sends[0]
        assert "nothing was recorded" in outcome.note
    else:
        assert not sends and "reader channel" in outcome.note
    assert world.publication("3039", PublicationKind.NEW_BILL) == original
    assert (
        tuple(line for line in world.repo.dump().splitlines() if 'INTO "publications"' in line)
        == before
    )
