import datetime as dt
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    Bill,
    CheckAspect,
    ObservationBasis,
    ObservationMode,
    Publication,
    StatusChange,
)
from lexinform.models.batch import BatchIntent, LlmBatch, LlmBatchItem
from lexinform.models.bill import LocatedText, ReadyAnalysis
from lexinform.models.enums import PublicationKind, PublicationStatus
from lexinform_web.ingestion.process import project_in_process, restore_in_process
from lexinform_web.ingestion.projection import project_state
from lexinform_web.ingestion.reader import read_document
from lexinform_web.ingestion.restore import SnapshotError, restore_snapshot

NOW = dt.datetime(2026, 9, 27, tzinfo=dt.UTC)


@pytest.fixture
def repository() -> Iterator[SqliteBillRepository]:
    repository = SqliteBillRepository(":memory:")
    repository.migrate()
    raw = json.loads((Path(__file__).parent / "fixtures/import_legacy.json").read_text())
    bill = Bill.model_validate(raw["bill"])
    repository.upsert_summary(bill.summary, now=bill.first_seen_at)
    assert bill.analysis is not None
    repository.save_analysis(bill.term, bill.number, bill.analysis)
    try:
        yield repository
    finally:
        repository.close()


def test_restored_rows_match_pure_projection(repository: SqliteBillRepository) -> None:
    with restore_in_process(repository.dump().encode()) as snapshot:
        document = read_document(snapshot, source_commit="a" * 40)
        identity = document.bills[0].identity
        bill = repository.get(int(identity.scope), identity.external_id)
        assert bill is not None
        assert document == project_state(origin=document.origin, bills=[bill])
        assert document == project_in_process(repository.dump().encode(), source_commit="a" * 40)
        assert (document.bills[0].observation_mode, document.bills[0].checks) == (None, ())
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            snapshot.connection.execute("DELETE FROM bills")


def test_observation_and_checks_are_exported(repository: SqliteBillRepository) -> None:
    stored, _ = repository.list_observation_candidates("@none")[0]
    repository.set_observation(
        stored.term, stored.number, ObservationMode.FULL, ObservationBasis.RELEVANT_ANALYSIS
    )
    repository.record_check(stored.term, stored.number, CheckAspect.PROCESS, at=NOW, ok=True)
    repository.record_check(
        stored.term, stored.number, CheckAspect.ACT, at=NOW, ok=False, outage=True
    )

    document = project_in_process(repository.dump().encode(), source_commit="a" * 40)

    exported = document.bills[0]
    assert (exported.observation_mode, exported.observation_basis) == ("full", "relevant_analysis")
    assert [(c.aspect, c.last_success_at, c.last_outage_at) for c in exported.checks] == [
        ("act", None, NOW),
        ("process", NOW, None),
    ]


def test_history_and_only_explicit_public_channel_survive(repository: SqliteBillRepository) -> None:
    with restore_snapshot(repository.dump().encode()) as snapshot:
        term, number = snapshot.connection.execute("SELECT term, number FROM bills").fetchone()
    change = StatusChange(
        term=term,
        number=number,
        old_fingerprint=None,
        new_fingerprint="event",
        new_stages=[],
        detected_at=NOW,
        consultation_opened=True,
    )
    repository.add_status_change(change)
    for channel, status, message in (
        ("@readers", PublicationStatus.SENT, 12),
        ("@technical", PublicationStatus.SENT, 13),
        ("@readers", PublicationStatus.UNKNOWN, 14),
    ):
        repository.create_publication(
            Publication(
                term=term,
                number=number,
                channel_id=channel,
                status=status,
                kind=PublicationKind.AGENDA,
                ref=str(message),
                message_id=message,
                created_at=NOW,
                error="PRIVATE ERROR",
            )
        )
    with restore_in_process(repository.dump().encode()) as snapshot:
        document = read_document(snapshot, source_commit="a" * 40, public_channel="@readers")
        assert document.events[0].consultation_opened
        assert [item.link.url for item in document.telegram] == ["https://t.me/readers/12"]
        assert "PRIVATE" not in document.model_dump_json()
        assert "technical" not in document.model_dump_json()
        assert not read_document(snapshot, source_commit="a" * 40).telegram


def test_pending_work_and_staged_analysis_remain_separate(repository: SqliteBillRepository) -> None:
    raw = json.loads((Path(__file__).parent / "fixtures/import_pending.json").read_text())
    intent = BatchIntent.model_validate(raw)
    bill = repository.get(intent.request.term, intent.request.number)
    assert bill is not None and bill.analysis is not None
    repository.save_batch_intent(intent)
    repository.save_ready_analysis(
        bill.term,
        bill.number,
        ReadyAnalysis(
            record=bill.analysis.model_copy(update={"revision": 99}),
            located=LocatedText(),
            text="SECRET",
        ),
    )
    repository.save_llm_batch(
        LlmBatch(
            batch_id="batch",
            provider="anthropic",
            call_kind="joint",
            submitted_at=NOW,
            status="ended",
            request_count=1,
            estimated_cost_usd=0,
        ),
        [
            LlmBatchItem(
                batch_id="batch",
                custom_id="item",
                call_kind="joint",
                term=bill.term,
                number=bill.number,
                meta=intent.meta,
            )
        ],
    )
    with restore_in_process(repository.dump().encode()) as snapshot:
        document = read_document(snapshot, source_commit="a" * 40)
    assert document.bills[0].has_staged_analysis
    assert document.bills[0].applied_analysis is not None
    assert document.bills[0].applied_analysis.revision == bill.analysis.revision
    assert {work.state for work in document.pending_work} == {"unknown", "submitted"}
    assert "SECRET" not in document.model_dump_json()
    repository.mark_llm_batch_item_consumed("batch", "item", consumed_at=NOW)
    with restore_in_process(repository.dump().encode()) as snapshot:
        document = read_document(snapshot, source_commit="a" * 40)
    assert [work.state for work in document.pending_work] == ["unknown"]


@pytest.mark.parametrize("corruption", ["identity", "analysis", "dangling", "status"])
def test_bad_rows_fail_without_exposing_contents(
    repository: SqliteBillRepository, corruption: str
) -> None:
    with restore_snapshot(repository.dump().encode()) as snapshot:
        connection = sqlite3.connect(":memory:")
        try:
            snapshot.connection.backup(connection)
            if corruption == "identity":
                connection.execute("UPDATE bills SET number = '999999'")
            elif corruption == "analysis":
                connection.execute("UPDATE bills SET analysis_json = 'PRIVATE invalid JSON'")
            elif corruption == "status":
                connection.execute("UPDATE bills SET status = 'PRIVATE invalid status'")
            else:
                connection.execute("UPDATE bills SET status = 'linked', linked_number = '999999'")
            connection.commit()
            raw = ("\n".join(connection.iterdump()) + "\nPRAGMA user_version = 35;").encode()
        finally:
            connection.close()
    with restore_in_process(raw) as restored:
        with pytest.raises(SnapshotError, match="rows failed") as caught:
            read_document(restored, source_commit="a" * 40)
        assert "PRIVATE" not in str(caught.value)
    with pytest.raises(SnapshotError, match="failed validation") as caught:
        project_in_process(raw, source_commit="a" * 40)
    assert "PRIVATE" not in str(caught.value)


def test_projection_child_deadline(repository: SqliteBillRepository) -> None:
    with pytest.raises(SnapshotError, match="deadline"):
        project_in_process(repository.dump().encode(), source_commit="a" * 40, timeout=0.000001)


@pytest.mark.parametrize("failure", ["commit", "hash", "invalid", "oversized", "missing"])
def test_parent_rejects_bad_projection_result(
    repository: SqliteBillRepository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    raw = repository.dump().encode()
    document = project_in_process(raw, source_commit="a" * 40).model_dump(mode="json")
    if failure in {"commit", "hash"}:
        field = "source_commit" if failure == "commit" else "dump_sha256"
        document["origin"][field] = "b" * (40 if failure == "commit" else 64)
    payload = json.dumps(document).encode()
    if failure == "invalid":
        payload = b"PRIVATE invalid document"
    elif failure == "oversized":
        payload = b" " * (20 * 1024 * 1024 + 1)
    if failure != "missing":
        (tmp_path / "document.json").write_bytes(payload)

    @contextmanager
    def result(raw: bytes, timeout: float, arguments: tuple[str, ...] = ()) -> Iterator[Path]:
        yield tmp_path

    monkeypatch.setattr("lexinform_web.ingestion.process.worker_result", result)
    with pytest.raises(SnapshotError) as caught:
        project_in_process(raw, source_commit="a" * 40)
    assert "PRIVATE" not in str(caught.value)
