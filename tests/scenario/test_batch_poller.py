"""Only unconsumed work in the pushed state can wake the batch collector."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from lexinform.adapters.state_snapshot import write_state_snapshot
from lexinform.cli import app
from lexinform.models import PendingBatch
from lexinform.settings import Settings
from tests.harness import World


def test_poller_stops_waking_runner_after_collection_is_persisted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    w = World(batch=True)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    write_state_snapshot(tmp_path / "state.sql", w.repo.dump(), w.repo.list_open_llm_batches())
    checked: list[list[str]] = []
    dispatched: list[str] = []
    settings = w.container.settings.model_copy(
        update={"github_repo": "owner/repo", "github_token": "TOKEN"}
    )

    def finished(settings: Settings, batches: list[PendingBatch]) -> bool:
        checked.append([batch.batch_id for batch in batches])
        return True

    monkeypatch.setattr("lexinform.cli._settings", lambda: settings)
    monkeypatch.setattr(
        "lexinform.cli.GitHubInboxWriter.read_pending_batches",
        lambda self, branch: (tmp_path / "pending-batches.json").read_text(),
    )
    monkeypatch.setattr("lexinform.cli._a_batch_is_done", finished)
    monkeypatch.setattr(
        "lexinform.cli.GitHubInboxWriter.dispatch",
        lambda self, event_type: dispatched.append(event_type),
    )
    runner = CliRunner()

    first = runner.invoke(app, ["poll-batches"])
    w.batch.resolve()
    w.run()
    write_state_snapshot(tmp_path / "state.sql", w.repo.dump(), w.repo.list_open_llm_batches())
    second = runner.invoke(app, ["poll-batches"])
    third = runner.invoke(app, ["poll-batches"])

    assert first.exit_code == second.exit_code == third.exit_code == 0
    assert checked == [["batch-1"]]
    assert dispatched == ["batch-ready"]
    assert w.batch.forgotten == []
    assert "no pending batches in persisted state" in second.output
