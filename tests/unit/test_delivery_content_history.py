import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Never

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import (
    CommandOutcome,
    ContentDecisionSummary,
    ContentDeliverySummary,
    ContentHistoryView,
    IncomingCommand,
    OutcomeStatus,
    Publication,
    PublicationStatus,
    parse_command,
)
from lexinform.ports import BillRepository
from lexinform.services import content_inspection
from lexinform.services.content_inspection import inspect_content_page
from tests.unit.test_content_history import NOW, publication, record, version


@pytest.fixture
def history_repo(tmp_path: Path) -> Iterator[SqliteBillRepository]:
    repo = SqliteBillRepository(tmp_path / "history.db")
    repo.migrate()
    yield repo
    repo.close()


@pytest.mark.parametrize(
    "suffix,page", [("history", 1), ("history 2", 2), ("history 2147483647", 2147483647)]
)
def test_history_command_has_an_explicit_read_only_action(suffix: str, page: int) -> None:
    command = parse_command("/delivery 12 " + suffix)
    assert command is not None, "a slash command is parsed"
    assert command.error is None and command.delivery_action == "history"
    assert command.delivery_history_page == page


@pytest.mark.parametrize(
    "suffix", ["history 0", "history -1", "history ٢", "history 2147483648", "history 1 extra"]
)
def test_history_page_grammar_rejects_invalid_or_unbounded_pages(suffix: str) -> None:
    command = parse_command("/delivery 12 " + suffix)
    assert command is not None and command.error is not None, "invalid pagination is answered"


def test_history_pages_include_intermediate_versions_without_a_current_bill(
    history_repo: SqliteBillRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = history_repo
    root = publication(repo)
    original = version(root=root)
    record(repo, original)
    repo.bind_publication_content(root, original.content_id)
    revision_id = publication(repo, original=root, text="BODY_SECRET_REVISION")
    revision = version(root=root, previous=original.content_id, text="BODY_SECRET_REVISION")
    record(repo, revision)
    repo.bind_publication_content(revision_id, revision.content_id)
    physical = repo.publication_by_id(revision_id)
    assert physical is not None and physical.delivery is not None, "the revision is frozen"
    later_id = repo.create_publication(
        physical.model_copy(
            update={
                "id": None,
                "ref": "later",
                "status": PublicationStatus.UNKNOWN,
                "delivery": physical.delivery.model_copy(
                    update={"rendered_text": "BODY_SECRET_LATER"}
                ),
            }
        )
    )
    later = version(root=root, previous=revision.content_id, text="BODY_SECRET_LATER")
    record(repo, later)
    repo.bind_publication_content(later_id, later.content_id)
    selected = repo.publication_by_id(root)
    assert selected is not None, "the original publication is retained"
    before = repo.dump()

    def no_bill(*args: object, **kwargs: object) -> Never:
        raise AssertionError("history must use immutable snapshots, never current Bill")

    monkeypatch.setattr(repo, "get", no_bill)
    pages = [inspect_content_page(repo, selected, page=page) for page in (1, 2, 3)]
    assert [page.version.content_id for page in pages if page.version] == [
        original.content_id,
        revision.content_id,
        later.content_id,
    ]
    assert all(page.history and page.history.current_id == revision.content_id for page in pages)
    assert pages[2].deliveries[0].status is PublicationStatus.UNKNOWN
    assert inspect_content_page(repo, selected, page=4).status == "out_of_range"
    assert repo.dump() == before
    rendered = (
        MessageFormatter("en")
        .command_reply(
            IncomingCommand(
                update_id=1,
                chat_id="tech",
                message_id=1,
                text=f"/delivery {root} history 2",
                received_at=NOW,
            ),
            CommandOutcome(status=OutcomeStatus.DELIVERY, content_history_page=pages[1]),
        )
        .text
    )
    assert revision.content_id in rendered and f"/delivery {revision_id}" in rendered
    assert "BODY_SECRET" not in pages[1].model_dump_json()
    assert "Saved snapshot preview" not in rendered and len(rendered) < 4096


def test_missing_history_remains_unavailable(history_repo: SqliteBillRepository) -> None:
    identity = publication(history_repo)
    selected = history_repo.publication_by_id(identity)
    assert selected is not None, "the fixture creates a physical publication"
    page = inspect_content_page(history_repo, selected, page=1)
    assert page.status == "unavailable" and page.version is None


def test_legacy_outcomes_do_not_gain_an_empty_history_page() -> None:
    outcome = CommandOutcome.model_validate_json('{"status":"delivery"}')
    assert "content_history_page" not in outcome.model_dump(mode="json")


def test_ambiguous_history_does_not_offer_a_version_or_infer_confirmation(
    history_repo: SqliteBillRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    from_history = publication(history_repo)
    selected = history_repo.publication_by_id(from_history)
    assert selected is not None, "the fixture creates a physical publication"
    unavailable = ContentHistoryView(integrity="unavailable", ambiguous=True)

    def inspect(repo: BillRepository, publication: Publication) -> ContentHistoryView:
        return unavailable

    monkeypatch.setattr(content_inspection, "inspect_content", inspect)
    page = inspect_content_page(history_repo, selected, page=1)
    assert page.status == "unavailable" and page.version is None and not page.deliveries
    assert page.history is unavailable and page.history.current_id is None


def test_corrupt_original_snapshot_is_unavailable_and_read_only(
    history_repo: SqliteBillRepository, tmp_path: Path
) -> None:
    root = publication(history_repo)
    original = version(root=root)
    record(history_repo, original)
    history_repo.bind_publication_content(root, original.content_id)
    selected = history_repo.publication_by_id(root)
    assert selected is not None, "the original publication is retained"
    with closing(sqlite3.connect(tmp_path / "history.db")) as connection, connection:
        connection.execute("UPDATE content_snapshots SET payload_json='not-json'")
    before = (tmp_path / "history.db").read_bytes()
    page = inspect_content_page(history_repo, selected, page=1)
    assert page.status == "unavailable" and page.version is None
    assert (tmp_path / "history.db").read_bytes() == before


def test_history_renderer_bounds_refs_and_decisions_without_rendering_saved_bodies(
    history_repo: SqliteBillRepository,
) -> None:
    root = publication(history_repo)
    original = version(root=root)
    record(history_repo, original)
    history_repo.bind_publication_content(root, original.content_id)
    selected = history_repo.publication_by_id(root)
    assert selected is not None, "the original publication is retained"
    page = inspect_content_page(history_repo, selected, page=1)
    assert page.version is not None, "the verified page supplies metadata for this original"
    summary = page.version.model_copy(
        update={
            "source_observation_ids": ("source-observation-v1:" + "a" * 64,) * 10,
            "computation_ids": ("result-v1:" + "b" * 64,) * 10,
        }
    )
    decisions = tuple(
        ContentDecisionSummary(action="retry", resolved_at=NOW, command_update_id=i)
        for i in range(3)
    )
    deliveries = tuple(
        ContentDeliverySummary(
            publication_id=i,
            status=PublicationStatus.UNKNOWN,
            message_id=None,
            attempts=12,
            decisions=decisions,
            omitted_decisions=20,
        )
        for i in range(1, 4)
    )
    page = page.model_copy(
        update={"version": summary, "deliveries": deliveries, "omitted_deliveries": 3}
    )
    outcome = CommandOutcome(status=OutcomeStatus.DELIVERY, content_history_page=page)
    rendered = (
        MessageFormatter("en")
        .command_reply(
            IncomingCommand(
                update_id=1,
                chat_id="tech",
                message_id=1,
                text=f"/delivery {root} history",
                received_at=NOW,
            ),
            outcome,
        )
        .text
    )
    assert "additional/opaque refs: 8" in rendered
    assert "Additional physical publications: 3" in rendered
    assert "Earlier operator decisions: 20" in rendered and len(rendered) < 4096
    assert "Saved snapshot preview" not in rendered
