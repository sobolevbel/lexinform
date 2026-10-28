import datetime as dt
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import CommandOutcome, OutcomeStatus, PublicationStatus
from tests.scenario.test_amendments_command import published_world


@pytest.mark.parametrize("retained", [False, True])
def test_delivery_shows_body_free_history_without_changing_the_publication(retained: bool) -> None:
    world, original = published_world()
    assert original.id is not None, "published_world stores the original publication"
    content = world.repo.get_publication_content(original.id)
    assert content is not None, "posting records the original editorial version"
    if retained:
        future = world.clock.now() + dt.timedelta(days=181)
        world.repo.prune_history(before=future, memo_before=future)
    before = world.repo.publication_by_id(original.id)
    requests = len(world.batch.submitted)
    sent = len(world.publisher.sent)
    edited = len(world.publisher.edited)

    world.command(f"/delivery {original.id}")
    world.run(track=False)

    outcome = world.replier.replies[-1][1]
    assert outcome.status is OutcomeStatus.DELIVERY
    history = outcome.content_history
    assert history is not None and history.current is not None, (
        "a sent original has a confirmed history"
    )
    assert history.current.content_id == content.content_id
    assert history.original == history.inspected == history.current
    assert len(history.summaries) == 1
    assert history.integrity == "valid" and not history.ambiguous
    assert content.rendered_text is not None, "the fixture freezes the original HTML before send"
    assert content.rendered_text not in history.model_dump_json()
    assert world.repo.publication_by_id(original.id) == before
    assert (len(world.batch.submitted), len(world.publisher.sent), len(world.publisher.edited)) == (
        requests,
        sent,
        edited,
    )
    restored = CommandOutcome.model_validate_json(outcome.model_dump_json()).content_history
    assert restored is not None, "a command outcome preserves its body-free editorial references"
    assert history == restored
    rendered = MessageFormatter("en").command_reply(world.replier.replies[-1][0], outcome).text
    assert "original content:" in rendered and "current content:" in rendered
    assert content.content_id in rendered
    assert outcome.delivery is not None, (
        "delivery inspection includes the saved physical publication"
    )
    assert f"physical attempts: {outcome.delivery.attempts}" in rendered


def test_unknown_revision_does_not_replace_the_current_original_in_delivery() -> None:
    world, original = published_world()
    assert original.id is not None and original.message_id is not None, (
        "the fixture sends the original message"
    )
    world.command("/amendments 1929")
    world.run(track=False)
    world.batch.resolve()
    world.publisher.unknown_on_edit.add("1929")
    world.run()
    revision = world.repo.list_amendment_revisions(original.id)[0]
    assert revision.id is not None and revision.status is PublicationStatus.UNKNOWN, (
        "the simulated edit has an uncertain outcome"
    )

    world.command(f"/delivery {revision.id}")
    world.run(track=False)

    history = world.replier.replies[-1][1].content_history
    assert history is not None and history.current is not None and history.inspected is not None, (
        "the view keeps known current and inspected versions distinct"
    )
    assert history.current == history.original
    assert history.current.content_id != history.inspected.content_id
    world.command(f"/delivery {revision.id} confirm {original.message_id}")
    world.run(track=False)
    confirmed = world.replier.replies[-1][1]
    assert confirmed.content_history is not None, (
        "operator confirmation preserves the editorial history"
    )
    assert confirmed.content_history.current == confirmed.content_history.inspected
    assert confirmed.delivery is not None and confirmed.delivery.status is PublicationStatus.SENT, (
        "only the audited operator decision confirms this edit"
    )


def test_legacy_outcome_does_not_grow_an_empty_history_field() -> None:
    old = '{"status":"delivery"}'
    outcome = CommandOutcome.model_validate_json(old)

    assert outcome.content_history is None
    assert "content_history" not in outcome.model_dump(mode="json")


def test_corrupt_optional_history_does_not_resolve_an_unknown_delivery(
    tmp_path: Path,
) -> None:
    world, original = published_world()
    assert original.id is not None, "the fixture stores the original publication"
    with closing(sqlite3.connect(tmp_path / "damaged.db")) as db:
        db.executescript(world.repo.dump())
        db.execute("UPDATE content_snapshots SET payload_json='bad'")
        db.commit()
        dump = "\n".join(db.iterdump()) + "\nPRAGMA user_version = 55;\n"
    world.repo.restore(dump)

    world.command(f"/delivery {original.id}")
    world.run(track=False)

    outcome = world.replier.replies[-1][1]
    assert outcome.status is OutcomeStatus.DELIVERY
    assert outcome.content_history is not None, (
        "the inspector reports the unavailable optional history"
    )
    assert (
        outcome.content_history.integrity == "unavailable"
        and outcome.content_history.current is None
    )
    assert world.repo.publication_by_id(original.id) == original


@pytest.mark.parametrize("damage", ["removed_payloads", "wrong_owner", "wrong_kind"])
def test_history_bindings_are_independent_of_retry_plans_and_check_physical_owners(
    damage: str,
) -> None:
    world, original = published_world()
    assert original.id is not None, "the fixture stores the original publication"
    world.command("/amendments 1929")
    world.run(track=False)
    world.batch.resolve()
    world.run()
    revision = world.repo.list_amendment_revisions(original.id)[0]
    assert revision.id is not None, "the collected amendment has a separate physical edition"
    expected = world.repo.get_publication_content(revision.id)
    assert expected is not None, "posting binds the confirmed revision to its editorial version"
    with closing(sqlite3.connect(":memory:")) as snapshot:
        snapshot.executescript(world.repo.dump())
        if damage == "removed_payloads":
            snapshot.execute(
                "UPDATE publications SET delivery_json=NULL WHERE id IN (?, ?)",
                (original.id, revision.id),
            )
        elif damage == "wrong_owner":
            snapshot.execute("UPDATE publications SET number='99999' WHERE id=?", (revision.id,))
        else:
            snapshot.execute("UPDATE publications SET kind='joint_bill' WHERE id=?", (revision.id,))
        snapshot.commit()
        dump = "\n".join(snapshot.iterdump()) + "\nPRAGMA user_version = 55;\n"
    world.repo.restore(dump)

    world.command(f"/delivery {revision.id}")
    world.run(track=False)

    outcome = world.replier.replies[-1][1]
    history = outcome.content_history
    assert history is not None, "the immutable binding preserves the inspected history"
    if damage == "removed_payloads":
        assert history.current is not None, "confirmed bindings survive operational payload cleanup"
        assert history.integrity == "valid" and history.current.content_id == expected.content_id
    else:
        assert history.integrity == "unavailable" and history.current is None
    assert outcome.delivery is not None and outcome.delivery.status is PublicationStatus.SENT, (
        "history inspection never changes physical delivery state"
    )
