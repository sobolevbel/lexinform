import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest
from tools.content_history_doctor import audit

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.errors import SupplementContentEvidenceError
from lexinform.models import (
    BatchResult,
    BillStatus,
    ContentSnapshot,
    ContentVersion,
    DeliveryPlan,
    LlmBatch,
    LlmBatchItem,
    Publication,
    PublicationKind,
    PublicationStatus,
    ResultApplication,
    ResultApplicationTarget,
    StatusChange,
    SupplementInputReceipt,
    SupplementRecord,
    computation_id,
)
from lexinform.models.content import SupplementContentEvidence, editorial_bill_snapshot
from lexinform.services.posting import Poster
from tests.computations import retained_result
from tests.fakes import FakePublisher, FakeSejmGateway, FixedClock
from tests.unit.test_input_receipts import NOW
from tests.unit.test_supplement_input_receipts import supplement_source


def seed(
    repo: SqliteBillRepository, path: Path, *, queued: bool = False
) -> tuple[ContentVersion, int]:
    (intent,) = repo.list_queued_batch_intents()
    receipt = intent.meta.prepared_input_receipt
    assert (
        isinstance(receipt, SupplementInputReceipt) and receipt.source_selection_id is not None
    ), "the supplement fixture retains its source parents"
    (source,) = repo.list_source_observations(10, "3039")
    prior = retained_result("supplement")
    assert isinstance(prior.record, SupplementRecord), "fixture constructs a validated supplement"
    request_sha = intent.request.payload_sha256
    batch_id = "supplement-batch"
    custom_id = intent.request.custom_id
    record = prior.record.model_copy(
        update={
            "source_url": source.document.url,
            "source_kind": source.document.kind,
            "number": "3039-005",
            "request_sha256": request_sha,
            "computation_id": computation_id("openai", batch_id, custom_id),
        }
    )
    paid = prior.model_copy(
        update={
            "term": 10,
            "number": "3039",
            "batch_id": batch_id,
            "custom_id": custom_id,
            "request_sha256": request_sha,
            "record": record,
        }
    )
    repo.save_computation_result(paid)
    typed = intent.model_copy(
        update={
            "meta": intent.meta.model_copy(
                update={"source_url": source.document.url, "document_number": record.number}
            )
        }
    )
    batch = LlmBatch(
        batch_id=batch_id,
        provider="openai",
        call_kind="supplement",
        submitted_at=NOW,
        status="submitted",
        request_count=1,
        estimated_cost_usd=0,
    )
    item = LlmBatchItem.from_intent(batch.batch_id, typed)
    repo.save_llm_batch(batch, [item])
    repo.delete_batch_intents([custom_id])
    repo.mark_llm_batch_item_consumed(
        batch_id, custom_id, consumed_at=NOW, result=BatchResult(custom_id=custom_id)
    )
    repo.mark_batch_item_accounted(batch_id, custom_id, at=NOW)
    repo.mark_llm_batch_collected(batch_id, completed_at=NOW)
    repo.mark_llm_batch_forgotten(batch_id, forgotten_at=NOW)
    repo.set_status(10, "3039", BillStatus.ANALYZED)
    bill = repo.get(10, "3039")
    assert bill is not None, "the source fixture creates the bill"
    change = StatusChange(
        term=10,
        number="3039",
        old_fingerprint=None,
        new_fingerprint="supplement-only",
        new_stages=[],
        detected_at=NOW,
        supplements=[record],
    )
    change.id = repo.add_status_change(change)
    assert change.id is not None, "the original event has a retained status-change ID"
    repo.save_result_application(
        ResultApplication(
            result_id=paid.result_id,
            term=10,
            number="3039",
            generation=None,
            target=ResultApplicationTarget(kind="status_change", ref=str(change.id)),
            phase="projection",
            outcome="applied",
            reason="retained_result",
            observed_generation=None,
            observed_status=None,
            created_at=NOW,
        )
    )
    plan = DeliveryPlan(
        bill_json=bill.model_dump_json(),
        change_json=change.model_dump_json(),
        rendered_text="Frozen supplement update",
        reply_to=7,
        today=NOW.date(),
    )
    physical = repo.create_publication(
        Publication(
            term=10,
            number="3039",
            kind=PublicationKind.STATUS_UPDATE,
            channel_id="test",
            status=PublicationStatus.QUEUED if queued else PublicationStatus.SENT,
            status_change_id=change.id,
            message_id=None if queued else 8,
            sent_at=None if queued else NOW,
            created_at=NOW,
            delivery=plan.model_copy(update={"rendered_text": ""}) if queued else plan,
        )
    )
    snapshots = (
        editorial_bill_snapshot(plan.bill_json),
        ContentSnapshot(kind="change", payload_json=plan.change_json),
        ContentSnapshot(
            kind="plan",
            payload_json=plan.model_copy(
                update={"bill_json": "", "change_json": "", "rendered_text": ""}
            ).model_dump_json(),
        ),
    )
    if not queued:
        for snapshot in snapshots:
            repo.save_content_snapshot(snapshot)
    with closing(sqlite3.connect(path)) as connection:
        (application_id,) = connection.execute("SELECT id FROM result_applications").fetchone()
    return (
        ContentVersion(
            root_publication_id=physical,
            reason="original",
            language="ru",
            bill_snapshot_id=snapshots[0].snapshot_id,
            change_snapshot_id=snapshots[1].snapshot_id,
            plan_snapshot_id=snapshots[2].snapshot_id,
            rendered_text=plan.rendered_text,
            formatter_version="fixture",
            status_change_id=change.id,
            computation_ids=(paid.result_id,),
            application_ref_profile="supplement-projection-v1",
            application_ids=(application_id,),
            supplement_evidence=SupplementContentEvidence(
                source_observation_id=source.observation_id
            ),
            recorded_at=NOW,
        ),
        physical,
    )


def test_supplement_editorial_binding_survives_normal_cleanup_and_restore(tmp_path: Path) -> None:
    path = tmp_path / "supplement.db"
    with closing(supplement_source(path, parents=True)[0]) as repo:
        version, physical = seed(repo, path)
        repo.save_content_version(version)
        repo.bind_publication_content(physical, version.content_id)
        (source,) = repo.list_source_observations(10, "3039")
        selection = repo.get_document_selection(source.selection_id)
        assert selection is not None, "the submitted receipt retains its selection"
        later = source.model_copy(
            update={
                "document": source.document.model_copy(update={"compare_versions": True}),
                "recorded_at": NOW + timedelta(days=1),
            }
        )
        repo.save_source_observation(selection, later)
        expiry = NOW + timedelta(days=365)
        repo.prune_history(before=expiry, memo_before=expiry)
        saved = repo.publication_by_id(physical)
        assert saved is not None and saved.delivery is None
        dump = repo.dump()
        repo.restore(dump)
        repo.restore(dump)
        assert repo.dump() == dump
        assert repo.get_publication_content(physical) == version
    assert audit(path, publication_id=physical)["status"] == "recorded"


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE llm_batch_items SET consumed_at=NULL",
        "UPDATE llm_batch_items SET meta_json=json_remove(meta_json,'$.prepared_input_receipt')",
        "UPDATE llm_batch_items SET meta_json=json_set(meta_json,'$.source_url','elsewhere')",
        "UPDATE llm_batch_items SET number='elsewhere'",
        "UPDATE llm_batches SET provider='anthropic'",
        "DELETE FROM source_observations",
    ],
)
def test_supplement_binding_rejects_missing_and_foreign_parents(
    tmp_path: Path, statement: str
) -> None:
    path = tmp_path / "supplement.db"
    with closing(supplement_source(path, parents=True)[0]) as repo:
        version, physical = seed(repo, path)
        repo.save_content_version(version)
        repo.bind_publication_content(physical, version.content_id)
        original = repo.dump()
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute(statement)
        broken = repo.dump()
        with pytest.raises(ValueError, match="content_application_reference_invalid"):
            repo.get_content_version(version.content_id)
        snapshot = tmp_path / "broken.db"
        with (
            closing(sqlite3.connect(path)) as connection,
            closing(sqlite3.connect(snapshot)) as copy,
        ):
            connection.backup(copy)
        report = audit(snapshot)
        assert report["status"] == "failed"
        assert any(
            item["code"] == "content_application_reference_invalid" for item in report["findings"]
        )
        repo.restore(original)
        with pytest.raises(ValueError):
            repo.restore(broken)
        assert repo.dump() == original


@pytest.mark.parametrize(
    "case", ["exact", "ambiguous", "legacy_render", "missing", "digest", "rollback"]
)
def test_first_freeze_only_records_exact_standalone_supplement_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    path = tmp_path / "supplement.db"
    with closing(supplement_source(path, parents=True)[0]) as repo:
        expected, physical = seed(repo, path, queued=True)
        if case == "ambiguous":
            (source,) = repo.list_source_observations(10, "3039")
            selection = repo.get_document_selection(source.selection_id)
            assert selection is not None, "fixture retains its selection"
            other = source.model_copy(
                update={"document": source.document.model_copy(update={"compare_versions": True})}
            )
            repo.save_source_observation(selection, other)
        elif case == "missing":
            with closing(sqlite3.connect(path)) as db, db:
                db.execute(
                    "UPDATE llm_batch_items"
                    " SET meta_json=json_remove(meta_json, '$.prepared_input_receipt')"
                )
        elif case in {"digest", "legacy_render"}:
            original = repo.publication_by_id(physical)
            assert original is not None and original.delivery is not None, "seed freezes the plan"
            changes = (
                {"destination": "digest_only"}
                if case == "digest"
                else {"rendered_text": "Already rendered"}
            )
            assert repo.replace_unattempted_delivery(
                physical, original.delivery.model_copy(update=changes)
            )
        elif case == "rollback":

            def reject_evidence(version: ContentVersion) -> None:
                assert version.supplement_evidence is not None
                raise SupplementContentEvidenceError("fixture rejects candidate evidence")

            monkeypatch.setattr(repo, "save_content_version", reject_evidence)
        pub = repo.publication_by_id(physical)
        assert pub is not None, "seed queues the physical status update"
        publisher = FakePublisher(publication_by_id=repo.publication_by_id)
        poster = Poster(
            repo,
            publisher,
            FixedClock(NOW),
            gateway=FakeSejmGateway(),
            channel_id="test",
            max_attempts=3,
        )

        assert poster.deliver(pub) == (case != "digest")

        version = repo.get_publication_content(physical)
        if case == "exact":
            assert version is not None, "the first freeze captures a known input and application"
            assert version.application_ids == expected.application_ids
            assert version.supplement_evidence == expected.supplement_evidence
            assert version.rendered_text == publisher.texts(PublicationKind.STATUS_UPDATE)[0]
            dump = repo.dump()
            repo.restore(dump)
            assert repo.dump() == dump
            assert repo.get_publication_content(physical) == version
        else:
            assert version is None
            with closing(sqlite3.connect(path)) as db:
                assert db.execute("SELECT count(*) FROM content_snapshots").fetchone() == (0,)
