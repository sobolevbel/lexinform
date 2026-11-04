from contextlib import closing
from dataclasses import dataclass
from typing import Literal

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.errors import (
    DeliveryUncertainError,
    ServiceUnavailableError,
    StateCheckpointError,
    TelegramUnavailableError,
)
from lexinform.models import (
    AnalysisRecord,
    Bill,
    DeliveryPlan,
    ProcessDetail,
    Publication,
    PublicationKind,
    PublicationStatus,
)
from lexinform.ports import PublishResult
from lexinform.services.card_edits import CardEditResult, edit_card
from lexinform.services.delivery import DeliveryService
from lexinform.services.posting import Poster
from tests.checkpoint_state import saved_state
from tests.database import empty_repository
from tests.fakes import FakePublisher, FakeSejmGateway, FixedClock, make_analysis

Mode = Literal["off", "shadow", "on"]
Shape = Literal["single_many", "many_same", "many_grow", "many_shrink"]
CHANNEL = "@test"


@dataclass
class SnapshotCheckpoint:
    repo: SqliteBillRepository
    durable: str = ""
    calls: int = 0
    fail_at: int | None = None

    def __call__(self) -> None:
        self.calls += 1
        if self.calls == self.fail_at:
            raise StateCheckpointError("injected reader checkpoint failure")
        self.durable = self.repo.dump()


@dataclass(frozen=True)
class EditCase:
    repo: SqliteBillRepository
    clock: FixedClock
    publisher: FakePublisher
    checkpoint: SnapshotCheckpoint
    baseline: Publication
    previous: tuple[Publication, ...]
    texts: tuple[str, ...]

    def card(self) -> Publication:
        stored = self.repo.publication_by_id(self.baseline.id or 0)
        assert stored is not None, "the fixture retains its delivered logical card"
        return stored

    def parts(self) -> list[Publication]:
        return self.repo.card_refresh_parts(self.baseline.id or 0)

    def edit(self) -> CardEditResult:
        bill = self.repo.get(10, "3039")
        assert bill is not None, "the fixture persists the replacement analysis"
        return edit_card(
            self.repo,
            self.clock,
            self.publisher,
            bill,
            self.card(),
            None,
            channel_id=CHANNEL,
            checkpoint=self.checkpoint,
        )

    def retry(self) -> int:
        poster = Poster(
            self.repo,
            self.publisher,
            self.clock,
            gateway=FakeSejmGateway(),
            channel_id=CHANNEL,
            max_attempts=3,
            checkpoint=self.checkpoint,
        )
        report = DeliveryService(
            self.repo, self.clock, poster, channel_id=CHANNEL, max_attempts=3
        ).run()
        assert not report.errors
        return report.cards_refreshed


def prepare(
    repo: SqliteBillRepository, process: ProcessDetail, mode: Mode, shape: Shape
) -> EditCase:
    clock = FixedClock()
    publisher = FakePublisher(
        formatter=MessageFormatter(
            "ru",
            today=lambda: clock.now().date(),
            channel=CHANNEL,
            attention_enabled=mode == "on",
            single_message_cards=False,
        ),
        publication_by_id=repo.publication_by_id,
    )
    repo.upsert_summary(process, now=clock.now())
    analysis = make_analysis()
    if shape != "single_many":
        analysis.practical_impact = "Старые заявления не подавать заново; 200 PLN. " * 220
    record = AnalysisRecord(
        analysis=analysis,
        model="fixture",
        prompt_version="fixture",
        input_chars=100,
        truncated=False,
        text_source="pdf",
        created_at=clock.now(),
    )
    repo.save_analysis(10, "3039", record)
    bill = repo.get(10, "3039")
    assert bill is not None, "the fixture persists its original analysis"
    original = publisher.render_card_parts(bill, None, minimum=1)
    identities: list[int] = []
    for index, text in enumerate(original):
        identity = repo.create_publication(
            Publication(
                term=10,
                number="3039",
                kind=PublicationKind.NEW_BILL if not index else PublicationKind.MESSAGE_PART,
                status=PublicationStatus.QUEUED,
                channel_id=CHANNEL,
                created_at=clock.now(),
                ref=f"message/{identities[0]}/{index + 1}" if identities else None,
                delivery=DeliveryPlan(
                    bill_json=bill.model_dump_json(),
                    rendered_text=text,
                    continuation_root=identities[0] if identities else None,
                    continuation_parent=identities[-1] if identities else None,
                    continuation_index=index + 1,
                ),
            )
        )
        identities.append(identity)
    root = repo.publication_by_id(identities[0])
    assert root is not None and root.delivery is not None, (
        "the fixture records every physical target"
    )
    assert repo.replace_unattempted_delivery(
        identities[0], root.delivery.model_copy(update={"continuation_ids": tuple(identities[1:])})
    )
    for index, identity in enumerate(identities):
        repo.mark_publication(
            identity, PublicationStatus.SENT, message_id=42 + index, count_attempt=False
        )
    repo.set_card_digest(identities[0], publisher.card_digest(bill, today=clock.now().date()))
    baseline = repo.publication_by_id(identities[0])
    assert baseline is not None, "the original logical projection is stored"
    previous = tuple(repo.card_refresh_parts(identities[0]))
    if shape == "many_same":
        text = analysis.practical_impact.replace("200 PLN", "400 PLN")
    elif shape == "many_shrink":
        text = "Новая сумма 400 PLN; старые заявления повторно не подавать."
    else:
        text = "Новая сумма 400 PLN; старые заявления повторно не подавать. " * (
            600 if shape == "many_grow" else 220
        )
    repo.save_analysis(
        10,
        "3039",
        record.model_copy(
            update={"analysis": analysis.model_copy(update={"practical_impact": text})}
        ),
    )
    updated = repo.get(10, "3039")
    assert updated is not None, "the fixture stores the desired new edition"
    texts = publisher.render_card_parts(updated, None, minimum=len(previous))
    assert (len(previous) == 1) is (shape == "single_many")
    assert (len(texts) > len(previous)) is (shape in ("single_many", "many_grow"))
    checkpoint = SnapshotCheckpoint(repo, durable=repo.dump())
    return EditCase(repo, clock, publisher, checkpoint, baseline, previous, texts)


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("shape", ["single_many", "many_same", "many_grow", "many_shrink"])
def test_every_multipart_edit_part_is_checkpointed_before_api_and_final_receipt_alone_advances_card(
    repo: SqliteBillRepository,
    process_3039: ProcessDetail,
    monkeypatch: pytest.MonkeyPatch,
    mode: Mode,
    shape: Shape,
) -> None:
    case = prepare(repo, process_3039, mode, shape)
    original = case.publisher.publish_prepared
    observed: list[int] = []

    def inspect(saved: Publication) -> PublishResult:
        with saved_state(case.checkpoint.durable) as durable:
            parts = durable.repo.card_refresh_parts(case.baseline.id or 0)
            assert (
                tuple(part.delivery.rendered_text for part in parts if part.delivery) == case.texts
            )
            assert all(part.delivery is not None for part in parts)
            index = len(observed)
            current = parts[index]
            assert saved.id == current.id and saved.delivery == current.delivery
            assert current.id is not None and current.delivery is not None, (
                "the physical intent precedes the API"
            )
            assert current.status is PublicationStatus.PENDING
            assert current.channel_id == CHANNEL
            assert current.delivery.today == case.clock.now().date()
            assert Bill.model_validate_json(current.delivery.bill_json).analysis is None
            assert durable.repo.publication_by_id(case.baseline.id or 0) == case.baseline
            root = parts[0]
            assert root.delivery is not None, "the edition freezes its complete membership"
            assert root.delivery.revision_of == case.baseline.id
            assert root.delivery.continuation_ids == tuple(part.id for part in parts[1:])
            if index:
                assert parts[index - 1].status is PublicationStatus.SENT
                assert current.delivery.reply_to == parts[index - 1].message_id
                assert current.delivery.continuation_root == root.id
            else:
                assert all(part.status is PublicationStatus.QUEUED for part in parts[1:])
            assert current.delivery.edit_message_id == (
                case.previous[index].message_id if index < len(case.previous) else None
            )
            observed.append(current.id)
        return original(saved)

    monkeypatch.setattr(case.publisher, "publish_prepared", inspect)
    result = case.edit()

    added = len(case.texts) - len(case.previous)
    assert result.completed and result.new_messages == added
    assert len(observed) == len(case.texts)
    assert len(case.publisher.sent) == added
    assert len(case.publisher.edited) == len(case.previous)
    with saved_state(case.checkpoint.durable) as durable:
        current = durable.repo.card_refresh_parts(case.baseline.id or 0)
        assert all(part.status is PublicationStatus.SENT for part in current)
        logical = durable.repo.publication_by_id(case.baseline.id or 0)
        assert logical is not None and logical.rendered_sha256 == current[0].rendered_sha256
        assert [part.message_id for part in current[: len(case.previous)]] == [
            part.message_id for part in case.previous
        ]
    assert not case.edit().completed


@pytest.mark.parametrize("shape", ["single_many", "many_shrink"])
@pytest.mark.parametrize("position", ["root", "final"])
@pytest.mark.parametrize("outcome", ["definite", "outage", "uncertain"])
def test_multipart_transport_outcomes_preserve_attempts_projection_and_confirmed_message_counts(
    repo: SqliteBillRepository,
    process_3039: ProcessDetail,
    monkeypatch: pytest.MonkeyPatch,
    shape: Shape,
    position: str,
    outcome: str,
) -> None:
    case = prepare(repo, process_3039, "on", shape)
    original = case.publisher.publish_prepared
    index = 0 if position == "root" else len(case.texts) - 1
    called: list[int] = []

    def transport(saved: Publication) -> PublishResult:
        assert saved.id is not None, "each physical operation is stored"
        called.append(saved.id)
        if len(called) != index + 1:
            return original(saved)
        if outcome == "uncertain":
            original(saved)
            raise DeliveryUncertainError("accepted before lost response")
        if outcome == "outage":
            raise TelegramUnavailableError("test channel unavailable")
        raise RuntimeError("definite edit refusal")

    monkeypatch.setattr(case.publisher, "publish_prepared", transport)
    if position == "root" and outcome != "definite":
        with pytest.raises(ServiceUnavailableError):
            case.edit()
    else:
        result = case.edit()
        assert not result.completed
        assert result.new_messages == max(0, index - len(case.previous))
        assert bool(result.errors) is (position == "final" and outcome != "definite")
    current = case.parts()
    affected = current[index]
    assert len(called) == index + 1
    assert affected.status is (
        PublicationStatus.UNKNOWN if outcome == "uncertain" else PublicationStatus.FAILED
    )
    assert affected.attempts == int(outcome == "definite")
    assert case.card() == case.baseline
    if position == "root":
        assert all(part.status is PublicationStatus.QUEUED for part in current[1:])
    with closing(empty_repository()) as restored:
        restored.restore(case.checkpoint.durable)
        assert restored.card_refresh_parts(case.baseline.id or 0) == current
    case.repo.restore(case.checkpoint.durable)
    monkeypatch.setattr(case.publisher, "publish_prepared", original)
    before = len(case.publisher.sent) + len(case.publisher.edited)
    refreshed = case.retry()
    if outcome == "uncertain":
        assert refreshed == 0
        assert len(case.publisher.sent) + len(case.publisher.edited) == before
        assert case.card() == case.baseline
    else:
        assert refreshed == 1
        assert len(case.publisher.sent) == len(case.texts) - len(case.previous)
        assert case.card().rendered_sha256 == current[0].rendered_sha256


@pytest.mark.parametrize("shape", ["single_many", "many_shrink"])
@pytest.mark.parametrize("boundary", ["root_pre", "root_post", "last_pre", "last_post"])
def test_checkpoint_failure_retains_only_confirmed_effects(
    repo: SqliteBillRepository,
    process_3039: ProcessDetail,
    shape: Shape,
    boundary: str,
) -> None:
    case = prepare(repo, process_3039, "on", shape)
    at = {
        "root_pre": 1,
        "root_post": 2,
        "last_pre": len(case.texts) * 2 - 1,
        "last_post": len(case.texts) * 2,
    }[boundary]
    case.checkpoint.fail_at = at

    with pytest.raises(StateCheckpointError):
        case.edit()

    assert len(case.publisher.edited) + len(case.publisher.sent) == at // 2
    assert (case.card().rendered_sha256 != case.baseline.rendered_sha256) is (
        boundary == "last_post"
    )
    with closing(empty_repository()) as fresh:
        fresh.restore(case.checkpoint.durable)
        fresh.mark_stale_pending_as_unknown(now=case.clock.now())
        assert fresh.publication_by_id(case.baseline.id or 0) == case.baseline
        current = fresh.card_refresh_parts(case.baseline.id or 0)
        if boundary == "root_pre":
            assert tuple(current) == case.previous
            return
        affected = current[0] if boundary.startswith("root") else current[-1]
        assert affected.status is (
            PublicationStatus.UNKNOWN if boundary.endswith("post") else PublicationStatus.QUEUED
        )
        restored = EditCase(
            fresh,
            case.clock,
            case.publisher,
            SnapshotCheckpoint(fresh),
            case.baseline,
            case.previous,
            case.texts,
        )
        before = len(case.publisher.sent) + len(case.publisher.edited)
        assert restored.retry() == int(boundary.endswith("pre"))
        if boundary.endswith("post"):
            assert len(case.publisher.sent) + len(case.publisher.edited) == before
            assert restored.card() == case.baseline
        else:
            assert restored.card().rendered_sha256 == restored.parts()[0].rendered_sha256
