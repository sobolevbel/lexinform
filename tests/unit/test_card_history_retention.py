from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest
from tools.content_history_doctor import audit
from tools.replay_card_history import CHANNEL, NOW

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import ProcessDetail, PublicationKind, PublicationStatus
from lexinform.services.card_edits import edit_card
from lexinform.services.content_history import record_publication_content
from lexinform.services.content_inspection import inspect_content
from lexinform.services.publishing import PublishingService
from tests.fakes import FakePublisher, FakeSejmGateway, FixedClock
from tests.unit.test_card_content_reader import physical, retain
from tests.unit.test_card_content_writer import metadata_counts, original, renamed
from tests.unit.test_replay_card_history import state


@pytest.fixture
def repository() -> Iterator[SqliteBillRepository]:
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        repo.restore(state())
        yield repo


@pytest.mark.parametrize("republish", [False, True])
def test_public_forget_preserves_history_and_double_restore(
    repository: SqliteBillRepository, tmp_path: Path, republish: bool
) -> None:
    repo = repository
    publisher = FakePublisher()
    bill, root = original(repo, publisher)
    assert root.id is not None, "public publishing persists the original identity"
    first = repo.get_publication_content(root.id)
    assert first is not None, "the public writer retains the original content"
    edit_card(repo, FixedClock(NOW), publisher, renamed(bill), root, None, channel_id=CHANNEL)
    versions = repo.list_content_versions(root.id)
    counts = metadata_counts(repo)
    service = PublishingService(
        FakeSejmGateway(),
        repo,
        publisher,
        FixedClock(NOW),
        channel_id=CHANNEL,
        attention_assess=False,
    )
    service.forget_card(bill)
    assert repo.publication_by_id(root.id) is None
    assert repo.get_content_version(first.content_id) == first
    assert repo.get_publication_content(root.id) == first
    assert repo.list_content_versions(root.id) == versions
    assert metadata_counts(repo) == counts
    history = inspect_content(repo, root)
    assert history is not None and history.current_id is None
    if republish:
        assert service.publish_bill(bill)
        replacement = service.card_of(bill)
        assert replacement is not None and replacement.id is not None, (
            "republish creates a new physical identity"
        )
        assert replacement.id > root.id
        assert repo.list_content_versions(root.id) == versions
    before = repo.dump()
    repo.restore(before)
    assert repo.dump() == before
    repo.restore(before)
    assert repo.dump() == before
    with closing(SqliteBillRepository(tmp_path / "history.db")) as copy:
        copy.migrate()
        copy.restore(before)
    report = audit(tmp_path / "history.db", publication_id=root.id)
    assert report["status"] == "recorded"
    assert report["summary"]["errors"] == 0
    assert any(item["level"] == "warning" for item in report["findings"])
    assert report["histories"][0]["current_content_id"] is None
    with pytest.raises(ValueError):
        repo.save_content_version(first)
    with pytest.raises(ValueError):
        repo.bind_publication_content(root.id, first.content_id)


def test_deleted_edit_is_unknown_without_discarding_its_binding(
    repository: SqliteBillRepository, tmp_path: Path
) -> None:
    repo = repository
    publisher = FakePublisher()
    bill, root = original(repo, publisher)
    assert root.id is not None, "public publishing persists the root identity"
    edit_card(repo, FixedClock(NOW), publisher, renamed(bill), root, None, channel_id=CHANNEL)
    versions = repo.list_content_versions(root.id)
    operation = repo.list_content_publications(root.id)[-1]
    assert operation.id is not None, "the edit writer persists the separate operation"
    assert (
        repo.delete_publication(bill.term, bill.number, PublicationKind.MESSAGE_PART, CHANNEL) == 1
    )
    assert repo.get_publication_content(operation.id) == versions[-1]
    with pytest.raises(ValueError):
        repo.save_content_version(versions[-1])
    with pytest.raises(ValueError):
        repo.bind_publication_content(operation.id, versions[-1].content_id)
    before = repo.dump()
    repo.restore(before)
    assert repo.dump() == before
    history = inspect_content(repo, root)
    assert history is not None and history.current_id is None
    with closing(SqliteBillRepository(tmp_path / "history.db")) as copy:
        copy.migrate()
        copy.restore(before)
    result = audit(tmp_path / "history.db")
    assert result["summary"]["errors"] == 0
    assert result["histories"][0]["current_content_id"] is None


@pytest.mark.parametrize(
    "corruption", ["snapshot", "predecessor", "binding", "ref", "reply", "target"]
)
def test_missing_root_does_not_hide_intrinsic_or_live_binding_corruption(
    repository: SqliteBillRepository, corruption: str
) -> None:
    repo = repository
    publisher = FakePublisher()
    bill, root = original(repo, publisher)
    assert root.id is not None, "public publishing persists the root identity"
    edit_card(repo, FixedClock(NOW), publisher, renamed(bill), root, None, channel_id=CHANNEL)
    assert repo.delete_publication(bill.term, bill.number, PublicationKind.NEW_BILL, CHANNEL) == 1
    before = repo.dump()
    statement = {
        "snapshot": "DELETE FROM content_snapshots;",
        "predecessor": "UPDATE content_versions SET previous_version_id='absent' WHERE previous_version_id IS NOT NULL;",
        "binding": "UPDATE publications SET kind='status_update' WHERE kind='message_part';",
        "ref": "UPDATE publications SET ref='card/999/"
        + "a" * 64
        + "-999/1' WHERE kind='message_part';",
        "reply": "UPDATE publications SET delivery_json=json_set(delivery_json,'$.reply_to',999) WHERE kind='message_part';",
        "target": "UPDATE publications SET message_id=999 WHERE kind='message_part';",
    }[corruption]
    with pytest.raises(ValueError):
        repo.restore(before + "\n" + statement)
    assert repo.dump() == before


def test_live_root_target_contradiction_remains_invalid(repository: SqliteBillRepository) -> None:
    repo = repository
    publisher = FakePublisher()
    bill, root = original(repo, publisher)
    assert root.id is not None, "public publishing persists the root identity"
    edit_card(repo, FixedClock(NOW), publisher, renamed(bill), root, None, channel_id=CHANNEL)
    repo.mark_publication(root.id, PublicationStatus.SENT, message_id=999)
    with pytest.raises(ValueError):
        repo.list_content_versions(root.id)


def test_live_root_target_mismatch_is_not_hidden_by_absent_edit_binding(
    repository: SqliteBillRepository,
) -> None:
    repo = repository
    publisher = FakePublisher()
    bill, root = original(repo, publisher)
    assert root.id is not None, "public publishing persists the root identity"
    edit_card(repo, FixedClock(NOW), publisher, renamed(bill), root, None, channel_id=CHANNEL)
    before = repo.dump()
    repo.restore(before + f"\nDELETE FROM publication_content WHERE publication_id!={root.id};")
    repo.mark_publication(root.id, PublicationStatus.SENT, message_id=999)
    with pytest.raises(ValueError):
        repo.list_content_versions(root.id)


@pytest.mark.parametrize("predecessor_state", ["deleted", "unknown", "dismissed"])
def test_new_card_lineage_requires_a_confirmed_available_predecessor(
    repository: SqliteBillRepository, process_3039: ProcessDetail, predecessor_state: str
) -> None:
    repo = repository
    root = physical(repo, process_3039)
    first = retain(repo, root)
    previous = physical(repo, process_3039, root=root.id)
    prior = retain(repo, previous, previous=first)
    assert root.id is not None and previous.id is not None and previous.delivery is not None, (
        "the fixture persists both original and predecessor with their actual frozen plans"
    )
    if predecessor_state == "deleted":
        repo.delete_publication(
            root.term, root.number, PublicationKind.MESSAGE_PART, root.channel_id
        )
    else:
        repo.mark_publication(previous.id, PublicationStatus(predecessor_state))
    assert repo.get_content_version(prior.content_id) == prior
    identity = repo.create_publication(
        previous.model_copy(
            update={
                "id": None,
                "status": PublicationStatus.QUEUED,
                "message_id": None,
                "ref": f"card/{root.id}/{'b' * 64}-{previous.id}/1",
                "delivery": previous.delivery.model_copy(update={"rendered_text": "third edition"}),
            }
        )
    )
    candidate = repo.publication_by_id(identity)
    assert candidate is not None, "the public repository stores the prospective physical operation"
    with pytest.raises(ValueError):
        retain(repo, candidate, previous=prior)


def test_existing_frozen_card_record_retry_survives_deleted_predecessor(
    repository: SqliteBillRepository, process_3039: ProcessDetail
) -> None:
    repo = repository
    root = physical(repo, process_3039)
    first = retain(repo, root)
    previous = physical(repo, process_3039, root=root.id)
    prior = retain(repo, previous, previous=first)
    assert root.id is not None and previous.id is not None and previous.delivery is not None, (
        "the fixture persists the predecessor and its original physical plan"
    )
    identity = repo.create_publication(
        previous.model_copy(
            update={
                "id": None,
                "status": PublicationStatus.QUEUED,
                "message_id": None,
                "ref": f"card/{root.id}/{'b' * 64}-{previous.id}/1",
                "delivery": previous.delivery.model_copy(update={"rendered_text": "third edition"}),
            }
        )
    )
    candidate = repo.publication_by_id(identity)
    assert candidate is not None, "the public repository stores the frozen pending operation"
    latest = retain(repo, candidate, previous=prior)
    repo.restore(repo.dump() + f"\nDELETE FROM publications WHERE id={previous.id};")
    before = repo.dump()
    assert record_publication_content(repo, FixedClock(NOW), candidate) == latest
    assert repo.dump() == before
    repo.restore(before)
    assert repo.dump() == before


@pytest.mark.parametrize("operation", ["save", "bind"])
def test_new_binding_cannot_use_a_retained_deleted_predecessor(
    repository: SqliteBillRepository, process_3039: ProcessDetail, operation: str
) -> None:
    repo = repository
    root = physical(repo, process_3039)
    first = retain(repo, root)
    previous = physical(repo, process_3039, root=root.id)
    prior = retain(repo, previous, previous=first)
    assert root.id is not None and previous.id is not None and previous.delivery is not None, (
        "the fixture stores the confirmed predecessor before constructing a later edition"
    )
    identity = repo.create_publication(
        previous.model_copy(
            update={
                "id": None,
                "status": PublicationStatus.QUEUED,
                "message_id": None,
                "ref": f"card/{root.id}/{'b' * 64}-{previous.id}/1",
                "delivery": previous.delivery.model_copy(update={"rendered_text": "third edition"}),
            }
        )
    )
    candidate = repo.publication_by_id(identity)
    assert candidate is not None, "the public repository stores the prospective operation"
    latest = retain(repo, candidate, previous=prior)
    repo.restore(
        repo.dump()
        + f"\nDELETE FROM publications WHERE id={previous.id};\nDELETE FROM publication_content WHERE publication_id={identity};"
    )
    assert repo.get_content_version(latest.content_id) == latest
    before = repo.dump()
    with pytest.raises(ValueError, match="predecessor_unconfirmed"):
        if operation == "bind":
            repo.bind_publication_content(identity, latest.content_id)
        else:
            repo.save_content_version(latest)
    assert repo.dump() == before
