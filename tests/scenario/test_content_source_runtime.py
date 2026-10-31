import sqlite3
from dataclasses import dataclass
from datetime import datetime
from unittest.mock import patch

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.content_sources import SOURCE_CONTEXT_INVALID
from lexinform.models import (
    AnalysisRecord,
    Bill,
    BillStatus,
    ComputationResult,
    ContentVersion,
    DeliveryPlan,
    DeliveryResolution,
    IncomingCommand,
    OutcomeStatus,
    Publication,
    PublicationKind,
    PublicationStatus,
    RunMode,
    StatusChange,
)
from lexinform.models.computations import ProjectionReason
from lexinform.ports import BillRepository
from lexinform.services import analysis_applications
from lexinform.services.posting import Poster
from tests.fakes import FakePublisher, FakeSejmGateway, FixedClock
from tests.harness import CHANNEL, World
from tests.scenario.test_amendments_command import SENATE
from tests.scenario.test_primary_input_receipts import TEXT, world
from tests.unit.test_content_sources import Case
from tests.unit.test_content_sources import case as case
from tests.unit.test_physical_extraction import docx

SCOPE = "frozen_primary_context/analysis"


@dataclass(frozen=True)
class RuntimeCase:
    world: World
    poster: Poster
    publication: Publication
    paid: ComputationResult


def legacy_projection(
    repo: BillRepository,
    target: Bill,
    record: AnalysisRecord,
    *,
    now: datetime,
    reason: ProjectionReason | None = None,
    projected_generation: int | None = None,
) -> None:
    assert projected_generation is not None, (
        "the compatibility fixture omits a supplied prospective stamp"
    )
    ORIGINAL_APPLICATION(repo, target, record, now=now, reason=reason)


ORIGINAL_APPLICATION = analysis_applications.record_analysis_application


def prepared(*, native: bool = True) -> RuntimeCase:
    current = world(docx(TEXT)) if native else World(batch_only=True)
    if not native:
        current.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    current.run(track=False)
    current.batch.resolve()
    if native:
        current.run(track=False)
    else:
        with patch.object(analysis_applications, "record_analysis_application", legacy_projection):
            current.run(track=False)
    bill = current.bill("4100")
    assert bill.analysis is not None and bill.analysis.computation_id is not None, (
        "the public batch cycle retains its original paid primary analysis"
    )
    paid = current.repo.get_computation_result(bill.analysis.computation_id)
    assert paid is not None, "primary collection retains the immutable paid result"
    poster = current.container.poster(dry_run=False)
    change = poster.record_change(
        StatusChange(
            term=bill.term,
            number=bill.number,
            old_fingerprint="before-senate",
            new_fingerprint="source-runtime-senate",
            new_stages=[SENATE.model_copy(update={"date": current.clock.now().date()})],
            detected_at=current.clock.now(),
        )
    )
    assert change is not None and change.id is not None, "record_change stores the Senate event"
    poster.prepare(bill, change)
    publication = current.repo.get_update_publication(change.id, CHANNEL)
    assert publication is not None and publication.id is not None, (
        "prepare stores the actual STATUS_UPDATE before its first render"
    )
    assert publication.kind is PublicationKind.STATUS_UPDATE and publication.delivery is not None
    assert not publication.delivery.rendered_text and publication.attempts == 0
    return RuntimeCase(current, poster, publication, paid)


def stored(case: RuntimeCase) -> Publication:
    assert case.publication.id is not None, "prepared retains its publication identity"
    publication = case.world.repo.publication_by_id(case.publication.id)
    assert publication is not None, "runtime operations preserve the original publication row"
    return publication


def editorial_rows(repo: SqliteBillRepository) -> tuple[str, ...]:
    prefixes = tuple(
        f'INSERT INTO "{name}"'
        for name in ("content_snapshots", "content_versions", "publication_content")
    )
    return tuple(row for row in repo.dump().splitlines() if row.startswith(prefixes))


def upstream_rows(repo: SqliteBillRepository) -> tuple[str, ...]:
    prefixes = tuple(
        f'INSERT INTO "{name}"'
        for name in ("computation_results", "document_work", "result_applications")
    )
    return tuple(row for row in repo.dump().splitlines() if row.startswith(prefixes))


def block(case: RuntimeCase, monkeypatch: pytest.MonkeyPatch) -> None:
    original = case.world.repo.get_computation_result

    def contradictory(identity: str) -> ComputationResult | None:
        paid = original(identity)
        return (
            paid.model_copy(update={"request_sha256": "f" * 64})
            if paid is not None and identity == case.paid.result_id
            else paid
        )

    with monkeypatch.context() as patch:
        patch.setattr(case.world.repo, "get_computation_result", contradictory)
        assert not case.poster.deliver(case.publication)
    publication = stored(case)
    assert publication.error == SOURCE_CONTEXT_INVALID
    assert publication.status is PublicationStatus.QUEUED and publication.attempts == 0


def test_fresh_status_update_captures_original_primary_source_and_survives_restore() -> None:
    case = prepared()
    before = upstream_rows(case.world.repo)
    requests = tuple(case.world.batch.submitted)
    assert case.poster.deliver(case.publication)
    publication = stored(case)
    assert publication.status is PublicationStatus.SENT
    assert publication.id is not None, "the stored delivery retains its original identity"
    version = case.world.repo.get_publication_content(publication.id)
    assert version is not None, "first rendering binds its editorial version atomically"
    observations = case.world.repo.list_source_observations(10, "4100")
    expected = tuple(item.observation_id for item in observations if item.purpose == "analysis")
    assert len(expected) == 1 and version.source_observation_ids == expected
    assert version.source_scope == SCOPE
    case.world.repo.restore(case.world.repo.dump())
    assert case.world.repo.get_content_version(version.content_id) == version
    assert upstream_rows(case.world.repo) == before
    assert tuple(case.world.batch.submitted) == requests
    assert case.world.repo.get_computation_result(case.paid.result_id) == case.paid


def test_fresh_supported_legacy_input_records_unknown_without_inventing_source_ids() -> None:
    case = prepared(native=False)
    assert case.poster.deliver(case.publication)
    assert case.publication.id is not None, "prepared stores the supported legacy input's update"
    version = case.world.repo.get_publication_content(case.publication.id)
    assert version is not None, "valid unknown input still receives editorial history"
    assert version.source_scope == SCOPE and version.source_observation_ids == ()
    assert stored(case).error is None


def test_invalid_source_rolls_back_freeze_history_and_preserves_paid_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = prepared()
    rows, upstream = editorial_rows(case.world.repo), upstream_rows(case.world.repo)
    sent, edited = len(case.world.publisher.sent), len(case.world.publisher.edited)
    block(case, monkeypatch)
    publication = stored(case)
    assert publication.delivery == case.publication.delivery
    assert editorial_rows(case.world.repo) == rows and upstream_rows(case.world.repo) == upstream
    assert (len(case.world.publisher.sent), len(case.world.publisher.edited)) == (sent, edited)
    assert case.world.repo.get_computation_result(case.paid.result_id) == case.paid


def test_source_sql_failure_propagates_and_rolls_back_the_entire_freeze(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = prepared()
    rows, upstream = editorial_rows(case.world.repo), upstream_rows(case.world.repo)
    save = case.world.repo.save_content_version
    failure = sqlite3.OperationalError("controlled source history storage failure")

    def fail_after_save(version: ContentVersion) -> None:
        save(version)
        raise failure

    with monkeypatch.context() as patch:
        patch.setattr(case.world.repo, "save_content_version", fail_after_save)
        with pytest.raises(sqlite3.OperationalError) as caught:
            case.poster.deliver(case.publication)
    assert caught.value is failure
    assert stored(case) == case.publication
    assert editorial_rows(case.world.repo) == rows and upstream_rows(case.world.repo) == upstream


def test_delivery_retry_recovers_only_the_known_original_frozen_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = prepared()
    block(case, monkeypatch)
    upstream, requests = upstream_rows(case.world.repo), tuple(case.world.batch.submitted)
    case.world.repo.reset_bill(10, "4100", BillStatus.ANALYSIS_PENDING)
    identity = case.publication.id
    assert identity is not None, "prepared retains the operator's exact target"
    case.world.command(f"/delivery {identity} retry")
    case.world.run(mode=RunMode.COMMANDS, discover=False, track=False, max_analyze=0, max_publish=0)
    outcome = case.world.replier.replies[-1][1]
    assert outcome.status is OutcomeStatus.DELIVERY and "validated" in outcome.note
    recovered = stored(case)
    assert recovered.error is None and recovered.attempts == 0
    assert recovered.status is PublicationStatus.QUEUED
    assert recovered.delivery is not None and case.publication.delivery is not None, (
        "recovery preserves the saved delivery context"
    )
    assert recovered.delivery.bill_json == case.publication.delivery.bill_json
    assert recovered.delivery.change_json == case.publication.delivery.change_json
    version = case.world.repo.get_publication_content(identity)
    assert version is not None and version.source_scope == SCOPE, (
        "the audited recovery binds its known original source context"
    )
    assert len(version.source_observation_ids) == 1
    assert len(case.world.repo.delivery_resolutions(identity)) == 1
    assert upstream_rows(case.world.repo) == upstream
    assert tuple(case.world.batch.submitted) == requests
    case.poster.start_run()
    assert case.poster.deliver(recovered)


def test_delivery_retry_cannot_clear_a_block_when_its_source_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = prepared()
    block(case, monkeypatch)
    before, rows = stored(case), editorial_rows(case.world.repo)
    identity = case.publication.id
    assert identity is not None, "prepared retains the blocked operator target"
    get = case.world.repo.get_computation_result

    def missing(result_id: str) -> ComputationResult | None:
        return None if result_id == case.paid.result_id else get(result_id)

    case.world.command(f"/delivery {identity} retry")
    with monkeypatch.context() as patch:
        patch.setattr(case.world.repo, "get_computation_result", missing)
        case.world.run(
            mode=RunMode.COMMANDS, discover=False, track=False, max_analyze=0, max_publish=0
        )
    assert case.world.replier.replies[-1][1].status is OutcomeStatus.ERROR
    assert stored(case) == before and editorial_rows(case.world.repo) == rows
    assert not case.world.repo.delivery_resolutions(identity)


def test_already_rendered_legacy_update_is_not_retrofitted_with_a_source_scope() -> None:
    case = prepared()
    original = case.publication
    assert original.id is not None and original.delivery is not None, (
        "prepared stores the original legacy-shaped payload"
    )
    assert case.world.repo.replace_unattempted_delivery(
        original.id, original.delivery.model_copy(update={"rendered_text": "Already frozen"})
    )
    assert case.poster.deliver(stored(case))
    version = case.world.repo.get_publication_content(original.id)
    assert version is not None, "the existing rendered update receives legacy editorial history"
    assert version.source_scope is None and not version.source_observation_ids
    before = version.content_id
    case.world.repo.restore(case.world.repo.dump())
    assert case.world.repo.get_publication_content(original.id) == version
    assert case.world.repo.get_content_version(before) == version


def test_synthetic_known_recovery_with_wrong_owner_rolls_back_all_prepared_history(
    case: Case,
) -> None:
    assert case.inspect().state == "known"
    change_id = case.repo.add_status_change(case.change)
    assert change_id is not None, "the synthetic future chain has one original status change"
    publication = Publication(
        term=case.bill.term,
        number=case.bill.number,
        kind=PublicationKind.STATUS_UPDATE,
        status=PublicationStatus.QUEUED,
        channel_id=CHANNEL,
        status_change_id=change_id,
        created_at=case.paid.collected_at,
        error=SOURCE_CONTEXT_INVALID,
        delivery=DeliveryPlan(
            bill_json=case.bill.model_dump_json(),
            change_json=case.change.model_copy(update={"id": change_id}).model_dump_json(),
        ),
    )
    identity = case.repo.create_publication(publication)
    command = IncomingCommand(
        update_id=31337,
        chat_id=CHANNEL,
        message_id=77,
        text=f"/delivery {identity} retry",
        received_at=case.paid.collected_at,
    )
    case.repo.record_command(command)
    publisher = FakePublisher()
    poster = Poster(
        case.repo,
        publisher,
        FixedClock(case.paid.collected_at),
        gateway=FakeSejmGateway(),
        channel_id=CHANNEL,
        max_attempts=3,
    )
    before = case.repo.dump()
    assert not poster.recover_source_context_retry(
        DeliveryResolution(
            publication_id=identity,
            term=publication.term,
            number="wrong-owner",
            kind=publication.kind,
            channel_id=CHANNEL,
            action="retry",
            command=command,
            resolved_at=case.paid.collected_at,
        )
    )
    assert not publisher.sent and not publisher.edited
    assert not case.repo.delivery_resolutions(identity)
    assert case.repo.dump() == before


def test_native_recovery_sql_failure_preserves_block_then_succeeds_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = prepared()
    block(current, monkeypatch)
    publication = stored(current)
    assert publication.id is not None, "the failed source audit retains its queued target"
    command = IncomingCommand(
        update_id=99101,
        chat_id=CHANNEL,
        message_id=17,
        text=f"/delivery {publication.id} retry",
        received_at=current.world.clock.now(),
    )
    current.world.repo.record_command(command)
    resolution = DeliveryResolution(
        publication_id=publication.id,
        publication_ids=(publication.id,),
        term=publication.term,
        number=publication.number,
        kind=publication.kind,
        channel_id=CHANNEL,
        action="retry",
        command=command,
        resolved_at=current.world.clock.now(),
    )
    save = current.world.repo.save_content_version
    failure = sqlite3.OperationalError("controlled recovery storage failure")

    def fail_after_save(version: ContentVersion) -> None:
        save(version)
        raise failure

    before = current.world.repo.dump()
    sent, edited = len(current.world.publisher.sent), len(current.world.publisher.edited)
    with monkeypatch.context() as patch:
        patch.setattr(current.world.repo, "save_content_version", fail_after_save)
        with pytest.raises(sqlite3.OperationalError) as caught:
            current.poster.recover_source_context_retry(resolution)
    assert caught.value is failure and current.world.repo.dump() == before
    assert current.poster.recover_source_context_retry(resolution)
    recovered = current.world.repo.dump()
    assert not current.poster.recover_source_context_retry(resolution)
    assert current.world.repo.dump() == recovered
    assert len(current.world.repo.delivery_resolutions(publication.id)) == 1
    assert stored(current).error is None and stored(current).attempts == 0
    assert (len(current.world.publisher.sent), len(current.world.publisher.edited)) == (
        sent,
        edited,
    )
    assert current.world.repo.get_computation_result(current.paid.result_id) == current.paid
