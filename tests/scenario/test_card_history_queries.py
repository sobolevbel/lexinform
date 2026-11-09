import sqlite3
from pathlib import Path
from typing import Literal

import pytest

from lexinform.services.content_inspection import inspect_content
from lexinform.services.tracking.cards import CardRefresher
from lexinform.services.tracking_result import TrackingResult
from tests.harness import World
from tests.scenario.test_card_remote_checkpoints import ready_world
from tests.scenario.test_single_card_revision_producer import card, change, refresh


@pytest.fixture
def saved_card(monkeypatch: pytest.MonkeyPatch) -> tuple[World, sqlite3.Connection]:
    connections: list[sqlite3.Connection] = []
    connect = sqlite3.connect

    def capture(
        database: str | Path,
        *,
        isolation_level: Literal["DEFERRED", "EXCLUSIVE", "IMMEDIATE"] | None = None,
    ) -> sqlite3.Connection:
        connection = connect(database, isolation_level=isolation_level)
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", capture)
    world = ready_world("off")
    world.run()
    return world, connections[0]


def test_card_history_query_count_is_bounded_as_revisions_grow(
    saved_card: tuple[World, sqlite3.Connection],
) -> None:
    world, database = saved_card
    counts: list[int] = []
    for revision in range(7):
        change(world, f"Разовая помощь в размере {300 + revision} PLN.")
        assert refresh(world) == 1
        if revision not in {0, 6}:
            continue
        owner = card(world)
        statements: list[str] = []
        database.set_trace_callback(statements.append)
        try:
            view = inspect_content(world.repo, owner)
        finally:
            database.set_trace_callback(None)
        assert view is not None and view.integrity == "valid"
        assert view.current_id is not None and view.version_count == revision + 2
        counts.append(len(statements))

    assert counts[0] == counts[1] <= 16


def test_unchanged_card_refresh_loads_the_owner_once(
    saved_card: tuple[World, sqlite3.Connection],
) -> None:
    world, database = saved_card
    refresher = CardRefresher(
        world.gateway,
        world.repo,
        world.publisher,
        world.clock,
        channel_id=card(world).channel_id,
        max_edits=1,
    )
    bill = world.bill("3039")
    statements: list[str] = []
    result = TrackingResult()

    database.set_trace_callback(statements.append)
    try:
        refresher.refresh([bill], result, publish=True)
    finally:
        database.set_trace_callback(None)

    assert result.cards_refreshed == 0
    assert sum(sql.startswith("SELECT * FROM publications") for sql in statements) == 1


def test_card_history_rejects_a_binding_outside_its_discovered_operations(
    saved_card: tuple[World, sqlite3.Connection],
) -> None:
    world, database = saved_card
    change(world)
    assert refresh(world) == 1
    root = card(world)
    assert root.id is not None, "the original has a stored physical identity"
    (revision,) = world.repo.card_refresh_parts(root.id)
    assert revision.id is not None, "the refresh stores its physical operation"
    foreign = world.repo.create_publication(root.model_copy(update={"channel_id": "foreign"}))
    database.execute(
        "INSERT INTO publication_content(publication_id,content_id)"
        " SELECT ?,content_id FROM publication_content WHERE publication_id=?",
        (foreign, revision.id),
    )

    view = inspect_content(world.repo, root)

    assert view is not None and view.integrity == "unavailable"
    assert view.current_id is None
