from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pytest
from tools.replay_card_history import CHANNEL, NOW, table_rows

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.errors import DeliveryUncertainError
from lexinform.models import Bill, ContentVersion, Mnemonic, Publication, PublicationStatus
from lexinform.services.card_edits import edit_card
from lexinform.services.content_inspection import inspect_content
from lexinform.services.publications import complete_delivery
from lexinform.services.publishing import PublishingService
from tests.fakes import FakePublisher, FakeSejmGateway, FixedClock
from tests.unit.test_replay_card_history import state


def original(repo: SqliteBillRepository, publisher: FakePublisher) -> tuple[Bill, Publication]:
    bill = repo.get(10, "RCL/1")
    assert bill is not None, "the fixture restores an analysed source Bill"
    service = PublishingService(
        FakeSejmGateway(),
        repo,
        publisher,
        FixedClock(NOW),
        channel_id=CHANNEL,
        attention_assess=False,
    )
    assert service.publish_bill(bill)
    publication = service.card_of(bill)
    assert publication is not None and publication.id is not None, (
        "public publishing saves its outbox"
    )
    return bill, publication


def renamed(bill: Bill, language: str = "ru") -> Bill:
    marker = Mnemonic(
        name="Offline fixture",
        language=language,
        basis="Synthetic render-only control",
        selected_at=NOW,
        origin="offline_fixture",
    )
    return bill.model_copy(
        update={"editorial": bill.editorial.model_copy(update={"mnemonic": marker})}
    )


def metadata_counts(repo: SqliteBillRepository) -> tuple[int, int, int]:
    rows = table_rows(
        repo.dump(),
        "SELECT (SELECT count(*) FROM content_snapshots), "
        "(SELECT count(*) FROM content_versions), (SELECT count(*) FROM publication_content)",
    )
    return tuple(rows[0])


@pytest.mark.parametrize("language", ["ru", "en"])
def test_fresh_public_card_and_single_edit_retain_actual_language_and_compact_context(
    language: str,
) -> None:
    with closing(SqliteBillRepository(Path(":memory:"))) as repo:
        repo.migrate()
        repo.restore(state())
        publisher = FakePublisher(formatter=MessageFormatter(language, channel=CHANNEL))
        bill, root = original(repo, publisher)
        first = repo.get_publication_content(root.id or 0)
        assert first is not None and first.language == language
        assert first.reason == "original" and first.card_context_profile == "continuation-v1"
        assert root.delivery is not None, "public publishing froze its source and render"
        snapshot = repo.get_content_snapshot(first.bill_snapshot_id)
        assert snapshot is not None and '"analysis"' not in snapshot.payload_json
        assert first.rendered_text == root.delivery.rendered_text
        edit_card(
            repo,
            FixedClock(NOW),
            publisher,
            renamed(bill, language),
            root,
            None,
            channel_id=CHANNEL,
        )
        versions = repo.list_content_versions(root.id or 0)
        assert len(versions) == 2
        assert versions[1].previous_version_id == first.content_id
        assert versions[1].reason == "card_revision" and versions[1].language == language
        before = repo.dump()
        edit_card(
            repo,
            FixedClock(NOW),
            publisher,
            renamed(bill, language),
            root,
            None,
            channel_id=CHANNEL,
        )
        assert repo.dump() == before
        repo.restore(before)
        assert repo.dump() == before


def test_unknown_formatter_language_keeps_delivery_unbound_and_never_backfills() -> None:
    with closing(SqliteBillRepository(Path(":memory:"))) as repo:
        repo.migrate()
        repo.restore(state())
        bill, root = original(repo, FakePublisher(formatter=MessageFormatter("unsupported")))
        assert metadata_counts(repo) == (0, 0, 0)
        edit_card(
            repo, FixedClock(NOW), FakePublisher(), renamed(bill), root, None, channel_id=CHANNEL
        )
        assert metadata_counts(repo) == (0, 0, 0)
        assert inspect_content(repo, root) is None


def test_uncertain_edit_keeps_its_version_without_confirming_current() -> None:
    with closing(SqliteBillRepository(Path(":memory:"))) as repo:
        repo.migrate()
        repo.restore(state())
        publisher = FakePublisher()
        bill, root = original(repo, publisher)
        publisher.unknown_on_edit.add(bill.number)
        with pytest.raises(DeliveryUncertainError):
            edit_card(
                repo, FixedClock(NOW), publisher, renamed(bill), root, None, channel_id=CHANNEL
            )
        assert len(repo.list_content_versions(root.id or 0)) == 2
        history = inspect_content(repo, root)
        assert history is not None and history.current_id is None and history.integrity == "valid"
        before = repo.dump()
        edit_card(repo, FixedClock(NOW), publisher, renamed(bill), root, None, channel_id=CHANNEL)
        assert repo.dump() == before


class CancelledHistoryRepository(SqliteBillRepository):
    def save_content_version(self, version: ContentVersion) -> None:
        raise KeyboardInterrupt


def test_history_cancellation_rolls_back_frozen_root_before_any_send() -> None:
    with closing(CancelledHistoryRepository(Path(":memory:"))) as repo:
        repo.migrate()
        repo.restore(state())
        publisher = FakePublisher()
        before = repo.dump()
        with pytest.raises(KeyboardInterrupt):
            original(repo, publisher)
        assert repo.dump() == before
        assert publisher.sent == []
        assert metadata_counts(repo) == (0, 0, 0)


class MultipartPublisher(FakePublisher):
    def split_message(self, text: str) -> tuple[str, ...]:
        return text, "Explicit synthetic continuation"


def test_multipart_original_is_delivered_without_orphan_metadata() -> None:
    with closing(SqliteBillRepository(Path(":memory:"))) as repo:
        repo.migrate()
        repo.restore(state())
        publisher = MultipartPublisher(publication_by_id=repo.publication_by_id)
        original(repo, publisher)
        assert len(publisher.sent) == 2
        assert metadata_counts(repo) == (0, 0, 0)


class InterruptibleRepository(SqliteBillRepository):
    cancel_history: bool = False

    def save_content_version(self, version: ContentVersion) -> None:
        if self.cancel_history:
            raise KeyboardInterrupt
        super().save_content_version(version)


def test_cancelled_edit_restores_entire_before_state_and_never_sends() -> None:
    with closing(InterruptibleRepository(Path(":memory:"))) as repo:
        repo.migrate()
        repo.restore(state())
        publisher = FakePublisher()
        bill, root = original(repo, publisher)
        before = repo.dump()
        repo.cancel_history = True
        with pytest.raises(KeyboardInterrupt):
            edit_card(
                repo, FixedClock(NOW), publisher, renamed(bill), root, None, channel_id=CHANNEL
            )
        assert repo.dump() == before
        assert publisher.edited == []


def test_inherited_alias_does_not_fabricate_an_edit_version() -> None:
    with closing(SqliteBillRepository(Path(":memory:"))) as repo:
        repo.migrate()
        repo.restore(state())
        publisher = FakePublisher()
        bill, root = original(repo, publisher)
        repo.create_publication(root.model_copy(update={"id": None, "number": "RCL/alias"}))
        before = metadata_counts(repo)
        edit_card(repo, FixedClock(NOW), publisher, renamed(bill), root, None, channel_id=CHANNEL)
        assert metadata_counts(repo) == before
        history = inspect_content(repo, root)
        assert history is not None and history.current_id is None


class FailedHistoryRepository(SqliteBillRepository):
    def save_content_version(self, version: ContentVersion) -> None:
        raise ValueError("Synthetic metadata write failure")


def test_record_failure_leaves_no_partial_root_or_metadata_and_spends_no_attempt() -> None:
    with closing(FailedHistoryRepository(Path(":memory:"))) as repo:
        repo.migrate()
        repo.restore(state())
        publisher = FakePublisher()
        before = repo.dump()
        with pytest.raises(ValueError, match="Synthetic metadata write failure"):
            original(repo, publisher)
        assert repo.dump() == before
        assert publisher.sent == []


def test_unknown_edit_operator_confirmation_reuses_exact_retained_version() -> None:
    with closing(SqliteBillRepository(Path(":memory:"))) as repo:
        repo.migrate()
        repo.restore(state())
        publisher = FakePublisher()
        bill, root = original(repo, publisher)
        publisher.unknown_on_edit.add(bill.number)
        with pytest.raises(DeliveryUncertainError):
            edit_card(
                repo, FixedClock(NOW), publisher, renamed(bill), root, None, channel_id=CHANNEL
            )
        versions = repo.list_content_versions(root.id or 0)
        physical = repo.list_content_publications(root.id or 0)[-1]
        assert physical.id is not None and root.message_id is not None, (
            "the public fixture persisted its uncertain edit and known original target"
        )
        attempts = physical.attempts
        counts = metadata_counts(repo)
        with repo.atomic():
            repo.mark_publication(
                physical.id,
                PublicationStatus.SENT,
                message_id=root.message_id,
                sent_at=NOW,
                count_attempt=False,
            )
            confirmed = repo.publication_by_id(physical.id)
            assert confirmed is not None, "confirmation updates the existing physical operation"
            complete_delivery(repo, confirmed, message_id=root.message_id, sent_at=NOW)
        history = inspect_content(repo, root)
        assert history is not None and history.current_id == versions[-1].content_id
        assert repo.list_content_versions(root.id or 0) == versions
        assert metadata_counts(repo) == counts
        assert confirmed.attempts == attempts


def test_unavailable_optional_history_keeps_edit_delivery_and_metadata_unchanged() -> None:
    with closing(SqliteBillRepository(Path(":memory:"))) as repo:
        repo.migrate()
        repo.restore(state())
        publisher = FakePublisher()
        bill, root = original(repo, publisher)
        before = metadata_counts(repo)
        with patch.object(
            repo, "get_publication_content", side_effect=ValueError("Unreadable history")
        ):
            result = edit_card(
                repo, FixedClock(NOW), publisher, renamed(bill), root, None, channel_id=CHANNEL
            )
        assert result.completed and publisher.edited
        assert metadata_counts(repo) == before
        history = inspect_content(repo, root)
        assert history is not None and history.current_id is None
