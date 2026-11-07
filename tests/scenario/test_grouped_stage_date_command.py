import hashlib
import json
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import httpx2 as httpx
import pytest

from lexinform.adapters.telegram import TelegramBotClient
from lexinform.container import Container
from lexinform.models import OutcomeStatus, PublicationStatus, parse_command
from tests.checkpoint_state import saved_state
from tests.scenario.test_card_remote_checkpoints import connect, git
from tests.scenario.test_group_remote_checkpoints import flush, queued_world
from tests.scenario.test_grouped_telegram_receipt import CHAT_ID, MESSAGE_ID, telegram_send


@pytest.mark.parametrize("mode", ["disabled", "no-checkpoint", "missing-credentials", "active"])
def test_groupedit_operator_uses_recorded_exact_approval(
    mode: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    world, ids = queued_world("updates")
    with telegram_send(world, monkeypatch, {"id": CHAT_ID}):
        flush(world, "updates", ids)
    root_id = ids[0]
    base = world.repo.get_publication_content(root_id)
    assert base is not None, "the original stage group is retained"
    evidence = base.grouped_stage_evidence
    assert evidence is not None and evidence.render_witness is not None and base.rendered_text, (
        "the exact grouped witness exists"
    )
    selected = evidence.members[0]
    today = evidence.render_witness.today
    old, new = today - timedelta(days=4), today - timedelta(days=2)
    base_sha = hashlib.sha256(base.rendered_text.encode()).hexdigest()
    args = (
        f"{root_id} {selected.publication_id} {selected.status_change_id}"
        f" {base.content_id} {base_sha} {old} {new}"
    )
    world.container = replace(
        world.container,
        settings=world.container.settings.model_copy(
            update={
                "grouped_stage_date_edits_enabled": mode != "disabled",
                "telegram_bot_token": "fixture" if mode != "missing-credentials" else "",
            }
        ),
    )
    calls: list[dict[str, object]] = []
    remote: Path | None = None
    approve_text = ""
    approve_id = 0

    def telegram(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/editMessageText")
        body = json.loads(request.content)
        calls.append(body)
        assert remote is not None
        with saved_state(git(remote, "show", "state:state.sql")) as restored:
            command = restored.repo.command_state(approve_id)
            assert command is not None and command.received_at is not None
            assert command.executed_at is None
            assert approve_text in restored.repo.dump()
            physical = restored.repo.list_content_publications(root_id)
            assert any(row.status is PublicationStatus.PENDING for row in physical)
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": MESSAGE_ID, "chat": {"id": CHAT_ID}}},
        )

    with closing(TelegramBotClient("fixture", transport=httpx.MockTransport(telegram))) as client:

        def client_for(_container: Container, *, for_channel: bool = True) -> TelegramBotClient:
            assert mode not in ("disabled", "missing-credentials")
            return client

        monkeypatch.setattr(Container, "telegram_client", client_for)
        if mode in ("active", "missing-credentials"):
            remote = connect(world, tmp_path)
        service = world.container.command_service(dry_run=False)
        assert service is not None, "the fake inbox is wired through the production container"

        def handle(*, publish: bool = True, dry_run: bool = False) -> None:
            service.handle_pending(
                min_score=3, run_started_at=world.clock.now(), publish=publish, dry_run=dry_run
            )

        world.command(f"/groupedit preview {args}")
        handle()
        preview = world.replier.replies[-1][1]
        if mode == "disabled":
            assert preview.note == "grouped stage edits disabled"
            world.command(f"/groupedit approve {args} {'0' * 64} {CHAT_ID} {MESSAGE_ID}")
            handle()
            assert world.replier.replies[-1][1].note == "grouped stage edits disabled"
            assert not calls
            return
        assert preview.status is OutcomeStatus.SHOWN
        before, after, approve_text = preview.note.splitlines()
        assert before.startswith("− • ") and after.startswith("+ • ")
        assert world.formatter.fmt_date(old) in before and world.formatter.fmt_date(new) in after
        parsed = parse_command(approve_text)
        assert parsed is not None and parsed.grouped_stage_date is not None, "preview is copyable"
        approval = parsed.grouped_stage_date.approval
        assert approval is not None, "the copyable command carries explicit approval"
        assert (approval.chat_id, approval.message_id) == (CHAT_ID, MESSAGE_ID)
        dry = world.command(approve_text)
        world.repo.begin()
        try:
            handle(dry_run=True)
        finally:
            world.repo.rollback()
        assert world.repo.command_state(dry.update_id) is None
        assert "requires a publishing run" in world.replier.replies[-1][1].note
        world.inbox.done(dry)
        world.command(approve_text)
        handle(publish=False)
        assert "requires a publishing run" in world.replier.replies[-1][1].note
        assert not calls
        if mode in ("no-checkpoint", "missing-credentials"):
            world.command(approve_text)
            handle()
            assert "durable checkpoint" in world.replier.replies[-1][1].note
            assert not calls
            return
        world.command(approve_text.replace(approval.candidate_sha256, "0" * 64))
        handle()
        assert "approval refused" in world.replier.replies[-1][1].note
        assert not calls
        approve_id = world.command(approve_text).update_id
        handle()
        assert world.replier.replies[-1][1].status is OutcomeStatus.DELIVERY
        (body,) = calls
        assert (body["chat_id"], body["message_id"]) == (CHAT_ID, MESSAGE_ID)
        assert isinstance(body["text"], str)
        assert hashlib.sha256(body["text"].encode()).hexdigest() == approval.candidate_sha256
        world.command(approve_text, update_id=approve_id)
        world.command(approve_text)
        handle()
        assert len(calls) == 1
        assert world.replier.replies[-1][1].note == "grouped stage approval refused"


@pytest.mark.parametrize(
    "part",
    ["0", "01", "9223372036854775808", "２０", "20260903", "2026-02-30", "<b>3</b>"],
)
def test_groupedit_preview_rejects_malformed_ids_dates_and_html(part: str) -> None:
    identity = "content-v1:" + "a" * 64
    rest = f"2 3 {identity} {'a' * 64} 2026-09-03 2026-09-05"
    command = parse_command(f"/groupedit preview {part} {rest}")
    if part in ("20260903", "2026-02-30"):
        command = parse_command(f"/groupedit preview 1 2 3 {identity} {'a' * 64} {part} 2026-09-05")
    assert command is not None and command.error is not None
    command = parse_command(f"/groupedit preview 1 {rest} {part}")
    assert command is not None and command.error is not None
