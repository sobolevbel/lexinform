import gzip
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest
from tools.content_history_doctor import audit
from tools.replay_content_applications import NOW, poster, queued

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    AmendmentsRecord,
    ComputationResult,
    Publication,
    PublicationKind,
    PublicationStatus,
    Stage,
    StatusChange,
    SupplementRecord,
)
from lexinform.models.original_delivery_content import OriginalDeliveryPlan
from lexinform.services import content_history
from lexinform.services.posting import Poster
from tests.computations import retained_result
from tests.fakes import FakePublisher, FakeSejmGateway, FixedClock, make_amendments, make_digest
from tests.unit.test_content_sources import Case
from tests.unit.test_content_sources import case as case
from tests.unit.test_supplement_content_evidence import seed
from tests.unit.test_supplement_input_receipts import supplement_source


def long_points() -> list[str]:
    return [
        " ".join(
            f"Срок заявления {value + number} дней, взнос {value * 3 + number} злотых."
            for value in range(19)
        )
        for number in range(6)
    ]


def long_amendment() -> ComputationResult:
    paid = retained_result("amendments")
    assert isinstance(paid.record, AmendmentsRecord)
    return paid.model_copy(
        update={
            "record": paid.record.model_copy(
                update={"amendments": make_amendments(changes=long_points())}
            )
        }
    )


@pytest.mark.parametrize("purpose", ["amendments", "amendments_multistage", "supplement"])
def test_direct_multipart_retains_exact_application_and_parts(
    case: Case, purpose: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if purpose.startswith("amendments"):
        paid = long_amendment()
        event = case.change
        if purpose == "amendments_multistage":
            (amendment_stage,) = event.new_stages
            event = event.model_copy(
                update={
                    "new_stages": [
                        Stage(stage_type="CommitteeWork", stage_name="Prace komisji"),
                        amendment_stage,
                        Stage(stage_type="SejmReading", stage_name="Czytanie"),
                        Stage(stage_type="Voting", stage_name="Głosowanie"),
                        Stage(stage_type="End", stage_name="Zakończenie"),
                    ]
                }
            )
        original = queued(case.repo, case.bill, event, paid)
        verify_history(case.repo, original, purpose, tmp_path, monkeypatch)
    else:
        path = tmp_path / "supplement.db"
        with closing(supplement_source(path, parents=True)[0]) as repo:
            _, physical = seed(repo, path, queued=True, digest=make_digest(points=long_points()))
            supplement_original = repo.publication_by_id(physical)
            assert supplement_original is not None
            original = supplement_original
            repo.create_publication(
                Publication(
                    term=original.term,
                    number=original.number,
                    channel_id=original.channel_id,
                    kind=PublicationKind.NEW_BILL,
                    status=PublicationStatus.SENT,
                    created_at=original.created_at,
                    sent_at=original.created_at,
                    message_id=7,
                )
            )
            verify_history(repo, original, purpose, tmp_path, monkeypatch)


def verify_history(
    repo: SqliteBillRepository,
    original: Publication,
    purpose: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert original.id is not None and original.status_change_id is not None
    before = repo.dump()
    snapshots = []
    payloads = []
    for enabled in (False, True):
        repo.restore(before)
        publisher = FakePublisher(publication_by_id=repo.publication_by_id)
        service = (
            poster(repo, publisher)
            if purpose.startswith("amendments")
            else Poster(
                repo,
                publisher,
                FixedClock(NOW),
                gateway=FakeSejmGateway(),
                channel_id=original.channel_id,
                max_attempts=3,
            )
        )
        with monkeypatch.context() as branch:
            if not enabled:
                branch.setattr(content_history, "MAX_ORIGINAL_DELIVERY_BYTES", 1)
            assert service.deliver(original)
        delivered = repo.publication_by_id(original.id)
        assert delivered is not None and delivered.delivery is not None
        assert delivered.delivery.continuation_ids
        payloads.append(tuple((msg.kind, msg.text) for msg in publisher.sent))
        version = repo.get_publication_content(original.id)
        if enabled:
            assert version is not None and version.direct_stage_evidence is not None
            assert version.direct_stage_evidence.multipart == "direct-stage-application-parts-v1"
            assert len(version.application_ids) == len(version.computation_ids) == 1
            snapshot = repo.get_content_snapshot(version.plan_snapshot_id)
            assert snapshot is not None
            plan = OriginalDeliveryPlan.model_validate_json(snapshot.payload_json)
            assert plan.bindings == (original.id, *delivered.delivery.continuation_ids)
            assert [part.html for part in plan.parts] == [msg.text for msg in publisher.sent]
            assert original.delivery is not None and version.change_snapshot_id is not None
            event = repo.get_content_snapshot(version.change_snapshot_id)
            assert event is not None
            assert StatusChange.model_validate_json(event.payload_json).new_stages == (
                StatusChange.model_validate_json(original.delivery.change_json).new_stages
            )
        else:
            assert version is None
        late_result = retained_result(
            "supplement" if purpose.startswith("amendments") else "amendments"
        )
        repo.save_computation_result(late_result)
        late = late_result.record
        if isinstance(late, SupplementRecord):
            repo.save_status_change_supplements(original.status_change_id, [late])
        else:
            assert isinstance(late, AmendmentsRecord)
            repo.save_status_change_amendments(original.status_change_id, late)
        cutoff = NOW + timedelta(days=999)
        repo.prune_history(before=cutoff, memo_before=cutoff)
        dump = repo.dump()
        for _ in range(2):
            repo.restore(dump)
            assert repo.dump() == dump
            assert repo.get_publication_content(original.id) == version
        if version is not None:
            database = tmp_path / f"{purpose}-retained.db"
            with closing(SqliteBillRepository(database)) as restored:
                restored.migrate()
                restored.restore(dump)
            report = audit(database, publication_id=original.id)
            assert report["status"] == "recorded" and not report["findings"]
            assert report["histories"][0]["current_content_id"] == version.content_id
            for identity in delivered.delivery.continuation_ids:
                assert repo.get_publication_content(identity) == version
            tampered = (
                dump + f"\nDELETE FROM result_applications WHERE id={version.application_ids[0]};\n"
            )
            with pytest.raises(ValueError, match="invalid_content_history"):
                repo.restore(tampered)
            assert repo.dump() == dump
        snapshots.append(dump.encode())
    assert payloads[0] == payloads[1]
    for label, data in zip(("baseline", "candidate"), snapshots, strict=True):
        (tmp_path / f"{purpose}-{label}.sql").write_bytes(data)
        (tmp_path / f"{purpose}-{label}.sql.gz").write_bytes(gzip.compress(data, mtime=0))


def test_two_amendment_stages_leave_multipart_delivery_without_application_history(
    case: Case,
) -> None:
    (stage,) = case.change.new_stages
    other = stage.model_copy(update={"stage_name": "Drugi raport z poprawkami"})
    change = case.change.model_copy(update={"new_stages": [stage, other]})
    original = queued(case.repo, case.bill, change, long_amendment())
    publisher = FakePublisher(publication_by_id=case.repo.publication_by_id)
    assert poster(case.repo, publisher).deliver(original)
    assert original.id is not None
    root = case.repo.publication_by_id(original.id)
    assert root is not None and root.delivery is not None and root.delivery.continuation_ids
    assert case.repo.get_publication_content(original.id) is None
