import sqlite3
from contextlib import closing
from datetime import timedelta

import pytest
from tools.replay_content_applications import NOW, poster, queued, replay, revision, rows

from lexinform.models import (
    AmendmentsRecord,
    ComputationResult,
    ContentVersion,
    DeliveryResolution,
    IncomingCommand,
    Publication,
    PublicationStatus,
    ResultApplicationTarget,
    SupplementRecord,
)
from lexinform.ports import PublishResult
from lexinform.services.content_history import (
    prepare_publication_content,
    record_publication_content,
)
from lexinform.services.result_applications import record_secondary_application
from tests.computations import retained_result
from tests.fakes import FakePublisher, FixedClock
from tests.unit.test_content_sources import Case
from tests.unit.test_content_sources import case as case


def correction() -> ComputationResult:
    result = retained_result("amendments")
    assert isinstance(result.record, AmendmentsRecord), "the fixture requests amendments"
    return result.model_copy(
        update={
            "record": result.record.model_copy(
                update={"source_url": "https://offline.invalid/technical-correction"}
            )
        }
    )


class InspectingPublisher(FakePublisher):
    def __init__(self, case: Case) -> None:
        super().__init__(publication_by_id=case.repo.publication_by_id)
        self.repo = case.repo
        self.observed: list[tuple[str, tuple[int, ...]]] = []

    def publish_prepared(self, publication: Publication) -> PublishResult:
        assert publication.id is not None, "the publisher receives its saved operation"
        version = self.repo.get_publication_content(publication.id)
        assert version is not None and version.application_ref_profile == "amendment-projections-v1"
        assert version.application_ids
        self.observed.append((version.reason, version.application_ids))
        return super().publish_prepared(publication)


def test_original_revision_bind_actual_projection_ids_before_api(case: Case) -> None:
    result = correction()
    original = queued(case.repo, case.bill, case.change, result)
    publisher = InspectingPublisher(case)
    service = poster(case.repo, publisher)
    before_apps = rows(case.repo.dump())["result_applications"]
    assert service.deliver(original)
    assert rows(case.repo.dump())["result_applications"] == before_apps
    confirmed = case.repo.publication_by_id(original.id or 0)
    assert confirmed is not None, "the public delivery confirms its original"
    revised = revision(case.repo, confirmed, result)
    before_apps = rows(case.repo.dump())["result_applications"]
    assert service.deliver(revised)
    assert rows(case.repo.dump())["result_applications"] == before_apps
    assert [kind for kind, _ in publisher.observed] == ["original", "amendments_revision"]
    assert publisher.observed[0][1] != publisher.observed[1][1]
    dump = case.repo.dump()
    case.repo.restore(dump)
    case.repo.restore(dump)
    assert case.repo.dump() == dump


def test_audited_recovery_uses_same_fresh_capture_without_new_applications(case: Case) -> None:
    report = replay(case.repo, case.bill, case.change, correction(), recovery=True)
    assert all(
        value["application_ref_profile"] == "amendment-projections-v1"
        for value in report["revision"]["raw_versions"]
    )
    assert report["repeat_exact"] and report["revision"]["double_restore_exact"]


def test_missing_receipts_leave_unprofiled_unknown_history(case: Case) -> None:
    original = queued(case.repo, case.bill, case.change, correction())
    with closing(sqlite3.connect(case.path)) as db:
        db.execute("DELETE FROM result_applications WHERE generation IS NULL")
        db.commit()
    publisher = FakePublisher()
    assert poster(case.repo, publisher).deliver(original)
    version = case.repo.get_publication_content(original.id or 0)
    assert version is not None and version.application_ref_profile is None
    assert version.application_ids == ()


@pytest.mark.parametrize("cancel", [False, True])
def test_failed_known_capture_rolls_back_frozen_payload_and_history_before_api(
    case: Case, monkeypatch: pytest.MonkeyPatch, cancel: bool
) -> None:
    original = queued(case.repo, case.bill, case.change, correction())
    before = case.repo.dump()

    def fail(result_id: str, target: object) -> tuple[int, ...]:
        if cancel:
            raise KeyboardInterrupt
        raise ValueError("content_application_reference_invalid")

    monkeypatch.setattr(case.repo, "find_projection_application_ids", fail)
    publisher = FakePublisher()
    with pytest.raises(KeyboardInterrupt if cancel else ValueError):
        poster(case.repo, publisher).deliver(original)
    assert case.repo.dump() == before
    assert not publisher.sent and not publisher.edited


def test_real_corrupt_receipt_refuses_freeze_atomically(case: Case) -> None:
    original = queued(case.repo, case.bill, case.change, correction())
    with closing(sqlite3.connect(case.path)) as db:
        db.execute(
            "UPDATE result_applications SET number='contradictory-index' WHERE generation IS NULL"
        )
        db.commit()
    before = case.repo.dump()
    publisher = FakePublisher()
    with pytest.raises(ValueError, match="content_application_reference_invalid"):
        poster(case.repo, publisher).deliver(original)
    assert case.repo.dump() == before
    assert not publisher.sent and not publisher.edited


def test_existing_unprofiled_versions_are_never_enriched_by_later_reuse(
    case: Case, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = queued(case.repo, case.bill, case.change, correction())
    publisher = FakePublisher()
    service = poster(case.repo, publisher)
    with monkeypatch.context() as empty:
        empty.setattr(case.repo, "find_projection_application_ids", lambda result_id, target: ())
        assert service.deliver(original)
    frozen = case.repo.get_publication_content(original.id or 0)
    assert frozen is not None and frozen.application_ref_profile is None
    encoded = frozen.model_dump_json()
    confirmed = case.repo.publication_by_id(original.id or 0)
    assert confirmed is not None, "the original is already confirmed"
    before = case.repo.dump()
    assert service.deliver(confirmed)
    assert record_publication_content(case.repo, FixedClock(NOW), confirmed) == frozen
    case.repo.restore(case.repo.dump())
    assert case.repo.dump() == before
    restored = case.repo.get_publication_content(original.id or 0)
    assert restored is not None and restored.model_dump_json() == encoded


def test_already_rendered_legacy_outbox_does_not_gain_references(case: Case) -> None:
    original = queued(case.repo, case.bill, case.change, correction())
    assert original.id is not None and original.delivery is not None, "the outbox stores its frame"
    publisher = FakePublisher()
    text = publisher.render_delivery(original)
    assert case.repo.replace_unattempted_delivery(
        original.id, original.delivery.model_copy(update={"rendered_text": text})
    )
    current = case.repo.publication_by_id(original.id)
    assert current is not None, "the legacy rendered outbox is persisted"
    assert poster(case.repo, publisher).deliver(current)
    version = case.repo.get_publication_content(original.id)
    assert (
        version is not None
        and version.application_ref_profile is None
        and version.application_ids == ()
    )


@pytest.mark.parametrize("missing", [False, True])
def test_partial_known_receipts_do_not_claim_completeness(case: Case, missing: bool) -> None:
    extra = retained_result("supplement")
    assert isinstance(extra.record, SupplementRecord), "the fixture requests a supplement"
    case.repo.save_computation_result(extra)
    original = queued(
        case.repo,
        case.bill,
        case.change.model_copy(update={"supplements": [extra.record]}),
        correction(),
    )
    if missing:
        with closing(sqlite3.connect(case.path)) as db:
            db.execute("DELETE FROM result_applications WHERE result_id=?", (extra.result_id,))
            db.commit()
    before = rows(case.repo.dump())["result_applications"]
    assert poster(case.repo, FakePublisher()).deliver(original)
    version = case.repo.get_publication_content(original.id or 0)
    assert version is not None and version.application_ref_profile == "amendment-projections-v1"
    assert len(version.application_ids) == (1 if missing else 2)
    assert extra.result_id in version.computation_ids
    assert rows(case.repo.dump())["result_applications"] == before


def test_foreign_application_owners_use_actual_ids_without_latest_guess(case: Case) -> None:
    result = correction()
    original = queued(case.repo, case.bill, case.change, result)
    record_secondary_application(
        case.repo,
        88,
        "foreign-target",
        result.result_id,
        target=ResultApplicationTarget(kind="status_change", ref=str(original.status_change_id)),
        now=NOW,
    )
    expected = case.repo.find_projection_application_ids(
        result.result_id,
        ResultApplicationTarget(kind="status_change", ref=str(original.status_change_id)),
    )
    assert len(expected) == 2
    assert poster(case.repo, FakePublisher()).deliver(original)
    version = case.repo.get_publication_content(original.id or 0)
    assert version is not None and version.application_ids == expected


def test_public_government_rehome_uses_frozen_context_without_owner_equality(case: Case) -> None:
    bill = case.bill.model_copy(
        update={"summary": case.bill.summary.model_copy(update={"number": "RCL/123"})}
    )
    case.repo.upsert_summary(bill.summary, now=NOW)
    original = queued(
        case.repo, bill, case.change.model_copy(update={"number": bill.number}), correction()
    )
    assert case.repo.move_government_rows(10, 11) == 1
    moved = case.repo.publication_by_id(original.id or 0)
    assert moved is not None and moved.term == 11
    assert poster(case.repo, FakePublisher()).deliver(moved)
    version = case.repo.get_publication_content(original.id or 0)
    assert version is not None and version.application_ref_profile == "amendment-projections-v1"
    dump = case.repo.dump()
    case.repo.restore(dump)
    assert case.repo.dump() == dump


class MultipartPublisher(FakePublisher):
    def render_delivery(self, publication: Publication) -> str:
        return "Controlled multipart message " * 400


def test_multipart_physical_operation_uses_distinct_profile_for_known_refs(case: Case) -> None:
    original = queued(case.repo, case.bill, case.change, correction())
    before = rows(case.repo.dump())["result_applications"]
    poster(case.repo, MultipartPublisher()).deliver(original)
    (version,) = case.repo.list_content_versions(original.id or 0)
    assert version.direct_stage_evidence is not None
    assert version.direct_stage_evidence.multipart == "direct-stage-application-parts-v1"
    assert version.application_ref_profile == "amendment-projections-v1"
    assert rows(case.repo.dump())["result_applications"] == before


def test_digest_only_never_freezes_known_refs(case: Case) -> None:
    original = queued(case.repo, case.bill, case.change, correction())
    assert original.id is not None and original.delivery is not None, (
        "the outbox has its frozen input"
    )
    assert case.repo.replace_unattempted_delivery(
        original.id, original.delivery.model_copy(update={"destination": "digest_only"})
    )
    current = case.repo.publication_by_id(original.id)
    assert current is not None, "the digest-only route is persisted"
    before = case.repo.dump()
    publisher = FakePublisher()
    assert not poster(case.repo, publisher).deliver(current)
    assert case.repo.dump() == before and not publisher.sent


@pytest.mark.parametrize("cancel", [False, True])
def test_version_write_failure_rolls_back_refs_snapshots_and_frozen_render(
    case: Case, monkeypatch: pytest.MonkeyPatch, cancel: bool
) -> None:
    original = queued(case.repo, case.bill, case.change, correction())
    saved = case.repo.save_content_version

    def fail_after(version: ContentVersion) -> None:
        saved(version)
        if cancel:
            raise KeyboardInterrupt
        raise ValueError("controlled history write failure")

    monkeypatch.setattr(case.repo, "save_content_version", fail_after)
    before = case.repo.dump()
    publisher = FakePublisher()
    with pytest.raises(KeyboardInterrupt if cancel else ValueError):
        poster(case.repo, publisher).deliver(original)
    assert case.repo.dump() == before and not publisher.sent


@pytest.mark.parametrize("cancel", [False, True])
def test_audited_recovery_capture_failure_is_atomic_without_api(
    case: Case, monkeypatch: pytest.MonkeyPatch, cancel: bool
) -> None:
    original = queued(case.repo, case.bill, case.change, correction())
    assert original.id is not None, "the original operation is saved"
    case.repo.mark_publication(
        original.id, PublicationStatus.QUEUED, error="source_context_invalid", count_attempt=False
    )
    command = IncomingCommand(
        update_id=80001,
        chat_id="@offline_application_history",
        message_id=1,
        text=f"/delivery {original.id} retry",
        received_at=NOW,
    )
    case.repo.record_command(command)
    resolution = DeliveryResolution(
        publication_id=original.id,
        publication_ids=(original.id,),
        term=original.term,
        number=original.number,
        kind=original.kind,
        channel_id=original.channel_id,
        action="retry",
        command=command,
        resolved_at=NOW,
    )

    def fail(result_id: str, target: object) -> tuple[int, ...]:
        if cancel:
            raise KeyboardInterrupt
        raise ValueError("controlled reference corruption")

    monkeypatch.setattr(case.repo, "find_projection_application_ids", fail)
    before = case.repo.dump()
    publisher = FakePublisher()
    with pytest.raises(KeyboardInterrupt if cancel else ValueError):
        poster(case.repo, publisher).recover_source_context_retry(resolution)
    assert case.repo.dump() == before and not publisher.sent and not publisher.edited
    assert not case.repo.delivery_resolutions(original.id)


def test_opaque_legacy_reference_bytes_survive_delivery_and_restore(case: Case) -> None:
    original = queued(case.repo, case.bill, case.change, correction())
    assert original.id is not None and original.delivery is not None, (
        "the fixture stores its operation"
    )
    publisher = FakePublisher()
    delivery = original.delivery.model_copy(
        update={"rendered_text": publisher.render_delivery(original)}
    )
    assert case.repo.replace_unattempted_delivery(original.id, delivery)
    frozen = case.repo.publication_by_id(original.id)
    assert frozen is not None, "the frozen legacy render is retained"
    prepared = prepare_publication_content(case.repo, FixedClock(NOW), frozen)
    assert prepared is not None, "the legacy single-message context is supported"
    version, snapshots = prepared
    for snapshot in snapshots:
        case.repo.save_content_snapshot(snapshot)
    opaque = version.model_copy(update={"application_ids": (987654,)})
    case.repo.save_content_version(opaque)
    case.repo.bind_publication_content(original.id, opaque.content_id)
    before = opaque.model_dump_json()
    assert poster(case.repo, publisher).deliver(frozen)
    current = case.repo.get_publication_content(original.id)
    assert current is not None and current.model_dump_json() == before
    case.repo.restore(case.repo.dump())
    current = case.repo.get_publication_content(original.id)
    assert current is not None and current.model_dump_json() == before


def test_prospective_refs_survive_cleanup_and_missing_physical_publication(case: Case) -> None:
    original = queued(case.repo, case.bill, case.change, correction())
    assert original.id is not None, "the queued original has an identity"
    assert poster(case.repo, FakePublisher()).deliver(original)
    known = case.repo.get_publication_content(original.id)
    assert known is not None and known.application_ref_profile == "amendment-projections-v1"
    case.repo.prune_history(before=NOW + timedelta(days=100), memo_before=NOW)
    assert case.repo.get_content_version(known.content_id) == known
    case.repo.delete_publication(original.term, original.number, original.kind, original.channel_id)
    assert case.repo.publication_by_id(original.id) is None
    assert case.repo.get_content_version(known.content_id) == known
    dump = case.repo.dump()
    case.repo.restore(dump)
    case.repo.restore(dump)
    assert case.repo.dump() == dump
    assert case.repo.get_content_version(known.content_id) == known
