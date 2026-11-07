import datetime as dt
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Literal, Never

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    AnalysisRecord,
    Bill,
    ConsultationFinding,
    DeliveryPlan,
    DocumentReviewRecord,
    EvidenceCitation,
    LocatedText,
    Phase,
    ProcessDetail,
    Publication,
    PublicationKind,
    PublicationStatus,
    ReadyAnalysis,
    ReviewDocument,
    ReviewWork,
    Stage,
    StatusChange,
)
from lexinform.ports import PublishResult
from lexinform.services.message_parts import freeze_parts
from lexinform.services.posting import Poster
from tests.computations import result_record
from tests.fakes import FakePublisher, FakeSejmGateway, FixedClock, make_analysis
from tests.harness import act, submission

Mode = Literal["off", "shadow", "on"]
CHANNEL = "@test"
NOTICE_KINDS = (
    PublicationKind.ACT_PUBLISHED,
    PublicationKind.CONSULTATION_RESULTS,
    PublicationKind.IN_FORCE,
    PublicationKind.CONSULTATION_DEADLINE,
    PublicationKind.DECISION_DEADLINE,
    PublicationKind.HEARING_DEADLINE,
)
REVIEW_KINDS = (
    PublicationKind.CONSULTATION_REVIEW,
    PublicationKind.COMMISSION_REVIEW,
    PublicationKind.AMENDMENTS_REVISION,
)


def row(repo: SqliteBillRepository, identity: int) -> Publication:
    publication = repo.publication_by_id(identity)
    assert publication is not None, "the contract stores every physical publication before delivery"
    return publication


def plan(repo: SqliteBillRepository, identity: int) -> DeliveryPlan:
    delivery = row(repo, identity).delivery
    assert delivery is not None, "the contract retains each publication's delivery snapshot"
    return delivery


@dataclass
class DumpCheckpoint:
    repo: SqliteBillRepository
    snapshots: list[str] = field(default_factory=list)

    def __call__(self) -> None:
        self.snapshots.append(self.repo.dump())

    def restore(self, durable: SqliteBillRepository) -> None:
        assert self.snapshots, "Poster must checkpoint before invoking the publisher"
        durable.restore(self.snapshots[-1])


@dataclass(frozen=True)
class DeliveryContract:
    repo: SqliteBillRepository
    publisher: FakePublisher
    clock: FixedClock
    gateway: FakeSejmGateway
    checkpoint: DumpCheckpoint
    poster: Poster
    card: int

    def bill(self) -> Bill:
        bill = self.repo.get(10, "3039")
        assert bill is not None, "the contract persists its bill before preparing deliveries"
        return bill

    def change(self, label: str, *, content_changed: bool = False) -> StatusChange:
        change = self.poster.record_change(
            StatusChange(
                term=10,
                number="3039",
                old_fingerprint=None,
                new_fingerprint=label,
                new_stages=[],
                detected_at=self.clock.now(),
                content_changed=content_changed,
            )
        )
        assert change is not None and change.id is not None, (
            "each contract event has a new identity"
        )
        return change

    def hold(self, label: str) -> int:
        change = self.change(label)
        self.poster.hold(self.bill(), change)
        publication = self.repo.get_update_publication(change.id or 0, CHANNEL)
        assert publication is not None and publication.id is not None, "Poster.hold stores its row"
        return publication.id


def contract(
    repo: SqliteBillRepository, process: ProcessDetail, mode: Mode, *, long: bool = False
) -> DeliveryContract:
    clock = FixedClock()
    repo.upsert_summary(process, now=clock.now())
    analysis = make_analysis()
    if long:
        analysis.summary = "Старые заявления не подавать заново; 200 PLN. " * 220
        analysis.practical_impact = "Старые заявления не подавать заново; 200 PLN. " * 220
    repo.save_analysis(
        10,
        "3039",
        AnalysisRecord(
            analysis=analysis,
            model="fixture",
            prompt_version="fixture",
            input_chars=100,
            truncated=False,
            text_source="pdf",
            created_at=clock.now(),
        ),
    )
    repo.save_act(10, "3039", act(entry_into_force=clock.now().date()))
    repo.save_submission(
        10,
        "3039",
        submission(print_number="3039", consultation_end=clock.now().date() + dt.timedelta(days=2)),
    )
    card = repo.create_publication(
        Publication(
            term=10,
            number="3039",
            kind=PublicationKind.NEW_BILL,
            channel_id=CHANNEL,
            status=PublicationStatus.SENT,
            message_id=42,
            created_at=clock.now(),
        )
    )
    publisher = FakePublisher(publication_by_id=repo.publication_by_id)
    gateway = FakeSejmGateway()
    checkpoint = DumpCheckpoint(repo)
    poster = Poster(
        repo,
        publisher,
        clock,
        gateway=gateway,
        channel_id=CHANNEL,
        max_attempts=3,
        attention_mode=mode,
        consultation_reviews_enabled=True,
        commission_reviews_enabled=True,
        checkpoint=checkpoint,
    )
    return DeliveryContract(repo, publisher, clock, gateway, checkpoint, poster, card)


@pytest.fixture
def durable() -> Iterator[SqliteBillRepository]:
    repository = SqliteBillRepository(":memory:")
    try:
        yield repository
    finally:
        repository.close()


def refuse_render(*args: object, **kwargs: object) -> Never:
    raise AssertionError("saved physical HTML must never be rendered from current data")


def test_new_status_snapshot_omits_review_data_reserved_for_its_own_message(
    repo: SqliteBillRepository, process_3039: ProcessDetail
) -> None:
    case = contract(repo, process_3039, "on")
    review = result_record("consultation_review")
    analysis = case.bill().analysis
    assert isinstance(review, DocumentReviewRecord) and analysis is not None, (
        "the fixture carries a completed review and primary analysis"
    )
    ready = ReadyAnalysis(record=analysis, located=LocatedText(summary=process_3039))
    now = case.clock.now()
    work = ReviewWork(
        memo_key="active-review",
        purpose="consultation_review",
        input_bill_json='{"operational":"retained only on the bill"}',
        since=now,
        baseline_day=now.date(),
    )
    repo.save_document_review(10, "3039", review)
    repo.save_ready_analysis(10, "3039", ready)
    repo.save_review_work(10, "3039", (work,))
    original = case.bill()
    assert original.consultation_review is not None and original.ready_analysis is not None, (
        "both completed review and ready primary context survive the repository round trip"
    )
    change = case.change("compact-status", content_changed=True)

    delivery = case.poster.prepare(original, change)
    assert delivery is not None, "a new update is prepared with its original plan"

    assert {"review_work", "consultation_review"}.isdisjoint(json.loads(delivery.bill_json))
    frozen = Bill.model_validate_json(delivery.bill_json)
    assert frozen == original.model_copy(update={"review_work": (), "consultation_review": None})
    assert case.bill() == original
    publication = repo.get_update_publication(change.id or 0, CHANNEL)
    assert publication is not None and publication.id is not None, "prepare stores the status row"
    legacy = delivery.model_copy(update={"bill_json": original.model_dump_json()})
    assert case.publisher.render_delivery(publication) == case.publisher.render_delivery(
        publication.model_copy(update={"delivery": legacy})
    )

    assert repo.replace_unattempted_delivery(publication.id, legacy)
    assert case.poster.prepare(original, change) == legacy
    review_publication = case.poster.prepare_message(original, PublicationKind.CONSULTATION_REVIEW)
    assert review_publication.delivery is not None, "the dedicated review freezes its own context"
    review_bill = Bill.model_validate_json(review_publication.delivery.bill_json)
    assert review_bill == original.model_copy(update={"review_work": ()})


def test_new_consultation_review_keeps_citations_but_not_source_text_in_delivery(
    repo: SqliteBillRepository, process_3039: ProcessDetail
) -> None:
    case = contract(repo, process_3039, "off")
    record = result_record("consultation_review")
    assert isinstance(record, DocumentReviewRecord), "fixture creates the full review"
    document = ReviewDocument(
        identity="response-document",
        url="https://example.test/official-response",
        role="response",
        text="Private-to-input source paragraph containing the exact quote.",
        sha256="a" * 64,
        normalized_sha256="b" * 64,
    )
    finding = ConsultationFinding(
        proposal="Zmiana terminu",
        respondent="Urząd",
        outcome="accepted",
        reason="Wyjaśnienie",
        affected_groups=(),
        evidence=EvidenceCitation(
            document_id=document.identity, locator="p. 4", quote="the exact quote"
        ),
        text_change="Termin wydłużono.",
        text_evidence=None,
    )
    review = record.model_copy(
        update={
            "context": record.context.model_copy(update={"documents": (document,)}),
            "review": record.review.model_copy(update={"consultation_findings": (finding,)}),
        }
    )
    repo.save_document_review(10, "3039", review)
    original = case.bill()
    publication = case.poster.prepare_message(
        original,
        PublicationKind.CONSULTATION_REVIEW,
        ref="native-review",
        today=case.clock.now().date(),
    )
    assert publication.id is not None and publication.delivery is not None, "prepare freezes a plan"
    frozen = Bill.model_validate_json(publication.delivery.bill_json)
    frozen_review = frozen.consultation_review
    assert frozen_review is not None, "the frozen plan keeps review findings and citations"
    assert frozen_review.review == review.review
    assert frozen_review.context.documents == (document.model_copy(update={"text": ""}),)
    assert case.bill() == original

    legacy = publication.model_copy(
        update={
            "delivery": publication.delivery.model_copy(
                update={"bill_json": original.model_dump_json()}
            )
        }
    )
    preview = case.publisher.render_delivery(legacy)
    assert case.publisher.render_delivery(publication) == preview

    case.publisher.fail_on.add(original.number)
    assert not case.poster.deliver(publication)
    assert row(repo, publication.id).status is PublicationStatus.FAILED
    assert plan(repo, publication.id).rendered_text == preview
    dump = repo.dump()
    repo.restore(dump)
    case.publisher.fail_on.clear()
    case.poster.start_run()
    retry = row(repo, publication.id)
    assert case.poster.deliver(retry)
    assert case.publisher.texts(PublicationKind.CONSULTATION_REVIEW) == [preview]
    assert case.bill() == original
    assert repo.get_publication_content(publication.id) is None


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("attempts", [0, 1])
@pytest.mark.parametrize("long", [False, True])
@pytest.mark.parametrize("policy", ["legacy", "attention-v1"])
def test_failed_status_snapshot_freezes_callback_equivalent_replies(
    repo: SqliteBillRepository,
    durable: SqliteBillRepository,
    process_3039: ProcessDetail,
    monkeypatch: pytest.MonkeyPatch,
    mode: Mode,
    attempts: int,
    long: bool,
    policy: str,
) -> None:
    case = contract(repo, process_3039, mode, long=long)
    included = case.hold("included")
    change = case.change("content-change", content_changed=True)
    case.poster.prepare(case.bill(), change, with_act=True)
    publication = repo.get_update_publication(change.id or 0, CHANNEL)
    assert publication is not None and publication.id is not None, "prepare stores the status row"
    identity = publication.id
    later = case.hold("later")
    legacy = plan(repo, identity).model_copy(update={"policy_version": policy})
    assert repo.replace_unattempted_delivery(identity, legacy)
    repo.mark_publication(
        identity, PublicationStatus.FAILED, error="prior failure", count_attempt=bool(attempts)
    )
    original = case.publisher.publish_prepared
    frozen = legacy.model_copy(
        update={
            "rendered_text": case.publisher.render_delivery(
                row(repo, identity).model_copy(
                    update={
                        "delivery": legacy.model_copy(update={"render_origin": "legacy_snapshot"})
                    }
                )
            ),
            "render_origin": "legacy_snapshot",
        }
    )
    expected = case.publisher.split_message(frozen.rendered_text)
    assert (len(expected) > 1) is long
    observed: list[int] = []
    monkeypatch.setattr(case.publisher, "publish_status_update", refuse_render)

    def inspect(current: Publication) -> PublishResult:
        case.checkpoint.restore(durable)
        root = plan(durable, identity)
        identities = (identity, *root.continuation_ids)
        assert len(identities) == len(expected)
        index = identities.index(current.id or 0)
        assert root == frozen.model_copy(
            update={
                "rendered_text": expected[0],
                "continuation_ids": root.continuation_ids,
                "held_change_ids": () if root.continuation_ids else frozen.held_change_ids,
            }
        )
        pending = row(durable, identities[index])
        assert pending.status is PublicationStatus.PENDING
        assert pending.attempts == (attempts if index == 0 else 0)
        assert pending.channel_id == CHANNEL and pending.delivery == current.delivery
        assert plan(durable, identities[index]).rendered_text == expected[index]
        if index:
            parent = row(durable, identities[index - 1])
            assert parent.status is PublicationStatus.SENT and parent.message_id is not None
            assert plan(durable, identities[index]).reply_to == parent.message_id
        assert row(durable, included).status is PublicationStatus.SKIPPED
        assert row(durable, later).status is PublicationStatus.SKIPPED
        observed.append(identities[index])
        return original(current)

    monkeypatch.setattr(case.publisher, "publish_prepared", inspect)

    assert case.poster.status_update(case.bill(), change)

    identities = [identity, *plan(repo, identity).continuation_ids]
    assert observed == identities
    case.checkpoint.restore(durable)
    assert all(row(durable, member).status is PublicationStatus.SENT for member in identities)
    assert row(durable, identity).attempts == attempts
    assert row(durable, included).status is PublicationStatus.SENT
    assert row(durable, later).status is PublicationStatus.SKIPPED
    assert not repo.list_queued_batch_intents() and not case.gateway.calls


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("kind", NOTICE_KINDS)
@pytest.mark.parametrize("attempts", [0, 1])
def test_failed_legacy_notice_freezes_its_saved_payload_and_date_before_api(
    repo: SqliteBillRepository,
    durable: SqliteBillRepository,
    process_3039: ProcessDetail,
    monkeypatch: pytest.MonkeyPatch,
    mode: Mode,
    kind: PublicationKind,
    attempts: int,
) -> None:
    case = contract(repo, process_3039, mode)
    day = case.clock.now().date()
    publication = case.poster.prepare_message(
        case.bill(),
        kind,
        today=day if kind in NOTICE_KINDS[2:] else None,
        phase=Phase(key="senate", deadline=day + dt.timedelta(days=2), deadline_exact=True)
        if kind is PublicationKind.DECISION_DEADLINE
        else None,
        hearing=Stage(
            stage_name="Wysłuchanie publiczne",
            stage_type="PublicHearing",
            date=day + dt.timedelta(days=12),
        )
        if kind is PublicationKind.HEARING_DEADLINE
        else None,
    )
    assert publication.id is not None, "prepare_message persists the notice identity"
    identity = publication.id
    original_plan = plan(repo, identity).model_copy(update={"policy_version": "legacy"})
    assert repo.replace_unattempted_delivery(identity, original_plan)
    repo.mark_publication(
        identity,
        PublicationStatus.FAILED,
        error="prior transport refusal",
        count_attempt=bool(attempts),
    )
    expected = case.publisher.render_delivery(row(repo, identity))
    frozen = original_plan.model_copy(
        update={"rendered_text": expected, "render_origin": "legacy_snapshot"}
    )
    repo.mark_publication(case.card, PublicationStatus.SENT, message_id=999, count_attempt=False)
    case.clock.advance(days=6)
    original = case.publisher.publish_prepared
    observed: list[int] = []

    def inspect(current: Publication) -> PublishResult:
        case.checkpoint.restore(durable)
        pending = row(durable, identity)
        assert pending.status is PublicationStatus.PENDING and pending.attempts == attempts
        assert pending.delivery == current.delivery == frozen
        assert pending.channel_id == CHANNEL and frozen.reply_to != 999
        observed.append(identity)
        return original(current)

    monkeypatch.setattr(case.publisher, "publish_prepared", inspect)

    assert case.poster.deliver(row(repo, identity))

    assert observed == [identity] and len(case.publisher.sent) == 1
    assert case.publisher.sent[-1].text == expected
    assert case.publisher.sent[-1].reply_to == original_plan.reply_to
    assert plan(repo, identity) == frozen
    assert not original_plan.rendered_text and not original_plan.continuation_ids
    case.checkpoint.restore(durable)
    assert row(durable, identity).status is PublicationStatus.SENT
    assert row(durable, identity).attempts == attempts
    assert not repo.list_queued_batch_intents() and not case.gateway.calls


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("kind", REVIEW_KINDS)
def test_saved_review_parts_checkpoint_exact_html_parent_and_edit_target_before_api(
    repo: SqliteBillRepository,
    durable: SqliteBillRepository,
    process_3039: ProcessDetail,
    monkeypatch: pytest.MonkeyPatch,
    mode: Mode,
    kind: PublicationKind,
) -> None:
    case = contract(repo, process_3039, mode)
    held = case.hold("held-review")
    held_change = row(repo, held).status_change_id
    assert held_change is not None, "Poster.hold records the source event identity"
    change = repo.get_status_change(held_change)
    assert change is not None, "Poster.record_change retains the held event"
    original = None
    if kind is PublicationKind.AMENDMENTS_REVISION:
        original_change = case.change("original-amendments")
        original = Publication(
            term=10,
            number="3039",
            kind=PublicationKind.STATUS_UPDATE,
            channel_id=CHANNEL,
            status=PublicationStatus.SENT,
            message_id=777,
            created_at=case.clock.now(),
            sent_at=case.clock.now(),
            ref="original-amendments",
            status_change_id=original_change.id,
            delivery=DeliveryPlan(
                bill_json=case.bill().model_dump_json(),
                change_json=change.model_dump_json(),
                rendered_text="<b>Исходное сообщение о поправках</b>",
                reply_to=42,
                today=case.clock.now().date(),
            ),
        )
        original.id = repo.create_publication(original)
    publication = Publication(
        term=10,
        number="3039",
        kind=kind,
        channel_id=CHANNEL,
        status=PublicationStatus.QUEUED,
        ref="saved-review",
        created_at=case.clock.now(),
        delivery=DeliveryPlan(
            bill_json=case.bill().model_dump_json(),
            change_json=change.model_dump_json(),
            reply_to=42,
            today=case.clock.now().date(),
            policy_version="attention-v1",
            edit_message_id=original.message_id if original else None,
            revision_of=original.id if original else None,
            held_change_ids=(held_change,),
        ),
    )
    publication.id = repo.create_publication(publication)
    identity = publication.id
    text = "<b>Сохранённое сообщение: сбор 200 PLN, заявление не подавать заново.</b>"
    text += "\nУсловие для старых заявлений, срок 30 дней. " * 220
    freeze_parts(repo, case.clock, case.publisher, publication, text)
    saved = plan(repo, identity)
    assert len(saved.continuation_ids) >= 2, "the contract reaches root and final part boundaries"
    identities = (identity, *saved.continuation_ids)
    payloads = {member: plan(repo, member).rendered_text for member in identities}
    case.clock.advance(days=30)
    repo.upsert_summary(
        case.bill().summary.model_copy(update={"title": "Changed live title"}), now=case.clock.now()
    )
    prepared = case.publisher.publish_prepared
    observed: list[int] = []

    def inspect(current: Publication) -> PublishResult:
        assert current.id is not None, "prepared review sends keep their physical identity"
        case.checkpoint.restore(durable)
        pending = row(durable, current.id)
        assert pending.status is PublicationStatus.PENDING and pending.delivery == current.delivery
        frozen = plan(durable, current.id)
        assert frozen.rendered_text == payloads[current.id]
        assert row(durable, held).status is PublicationStatus.SKIPPED
        if current.id == identity:
            assert frozen.reply_to == saved.reply_to
            assert frozen.edit_message_id == saved.edit_message_id
            assert all(
                row(durable, member).status is PublicationStatus.QUEUED for member in identities[1:]
            )
        else:
            parent = row(durable, frozen.continuation_parent or 0)
            assert parent.status is PublicationStatus.SENT and parent.message_id is not None
            assert frozen.reply_to == parent.message_id
        if original is not None:
            assert original.id is not None, "the revision stores its original event row"
            assert row(durable, original.id) == original
        observed.append(current.id)
        return prepared(current)

    monkeypatch.setattr(case.publisher, "render_delivery", refuse_render)
    monkeypatch.setattr(case.publisher, "publish_prepared", inspect)

    assert case.poster.deliver(row(repo, identity))

    assert observed == list(identities)
    case.checkpoint.restore(durable)
    assert all(row(durable, member).status is PublicationStatus.SENT for member in identities)
    assert row(durable, held).status is PublicationStatus.SENT
    if original is not None:
        assert original.id is not None, "the amendment revision retains its original event"
        assert row(durable, original.id) == original
        assert case.publisher.edited[-1][1] == original.message_id
        assert case.publisher.edited[-1][0].text == saved.rendered_text
    assert not repo.list_queued_batch_intents() and not case.gateway.calls
