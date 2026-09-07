from __future__ import annotations

from datetime import UTC, datetime

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    BillStatus,
    Publication,
    PublicationKind,
    PublicationStatus,
    RunReport,
    StatusChange,
    stage_fingerprint,
)
from tests.fakes import FakeLlm


def test_migrate_is_idempotent(repo: SqliteBillRepository) -> None:
    repo.migrate()
    repo.migrate()
    assert repo.get(10, "1") is None


def test_upsert_and_status_roundtrip(repo, processes_page, now) -> None:  # type: ignore[no-untyped-def]
    summary = processes_page[0]
    bill = repo.upsert_summary(summary, now=now)
    assert bill.status is BillStatus.DISCOVERED
    repo.set_status(10, summary.number, BillStatus.ANALYSIS_PENDING, prefilter_hits=["cudzoziemcy"])
    again = repo.upsert_summary(summary, now=now)  # update path keeps status/hits
    assert again.status is BillStatus.ANALYSIS_PENDING
    assert again.prefilter_hits == ["cudzoziemcy"]


def test_analysis_and_candidates(repo, processes_page, now) -> None:  # type: ignore[no-untyped-def]
    llm = FakeLlm()
    for s in processes_page[:3]:
        repo.upsert_summary(s, now=now)
    from lexinform.models import ApplicantType, BillContext

    ctx = BillContext(
        number="x",
        title="t",
        description=None,
        document_date=None,
        applicant_type=ApplicantType.UNKNOWN,
        text="",
        truncated=False,
        text_source="pdf",
    )
    repo.save_analysis(10, processes_page[0].number, llm.analyze(ctx))
    candidates = repo.list_publish_candidates(10, "chan", min_score=2, limit=10)
    assert [c.number for c in candidates] == [processes_page[0].number]
    # a skipped publication removes it from the candidates
    repo.create_publication(
        Publication(
            term=10,
            number=processes_page[0].number,
            kind=PublicationKind.NEW_BILL,
            status=PublicationStatus.SKIPPED,
            channel_id="chan",
            created_at=now,
        )
    )
    assert repo.list_publish_candidates(10, "chan", min_score=2, limit=10) == []
    # ...but only for that channel
    assert len(repo.list_publish_candidates(10, "other", min_score=2, limit=10)) == 1


def test_failure_counter(repo, processes_page, now) -> None:  # type: ignore[no-untyped-def]
    s = processes_page[0]
    repo.upsert_summary(s, now=now)
    repo.record_analysis_failure(10, s.number, "boom")
    repo.record_analysis_failure(10, s.number, "boom again")
    bill = repo.get(10, s.number)
    assert (
        bill is not None
        and bill.analysis_attempts == 2
        and bill.status is BillStatus.ANALYSIS_FAILED
    )
    assert bill.last_error == "boom again"


def test_publication_unique_per_bill_and_channel(repo, processes_page, now) -> None:  # type: ignore[no-untyped-def]
    s = processes_page[0]
    repo.upsert_summary(s, now=now)
    pub = Publication(
        term=10,
        number=s.number,
        kind=PublicationKind.NEW_BILL,
        status=PublicationStatus.FAILED,
        channel_id="chan",
        created_at=now,
    )
    first = repo.create_publication(pub)
    second = repo.create_publication(pub.model_copy(update={"status": PublicationStatus.PENDING}))
    assert first == second
    repo.mark_publication(
        first, PublicationStatus.SENT, message_id=42, document_message_ids=[43], sent_at=now
    )
    stored = repo.get_publication(10, s.number, "new_bill", "chan")
    assert (
        stored is not None and stored.status is PublicationStatus.SENT and stored.message_id == 42
    )
    assert stored.document_message_ids == [43]


def test_stale_pending_marked_unknown(repo, processes_page, now) -> None:  # type: ignore[no-untyped-def]
    s = processes_page[0]
    repo.upsert_summary(s, now=now)
    repo.create_publication(
        Publication(
            term=10,
            number=s.number,
            kind=PublicationKind.NEW_BILL,
            status=PublicationStatus.PENDING,
            channel_id="chan",
            created_at=now,
        )
    )
    assert repo.mark_stale_pending_as_unknown(now=now) == 1
    stored = repo.get_publication(10, s.number, "new_bill", "chan")
    assert stored is not None and stored.status is PublicationStatus.UNKNOWN


def test_tracked_and_status_change_dedup(repo, process_3039, now) -> None:  # type: ignore[no-untyped-def]
    repo.upsert_summary(process_3039, now=now)
    repo.save_stages(10, "3039", process_3039.stages, stage_fingerprint(process_3039.stages))
    assert repo.list_tracked(10, "chan", closed_grace_days=30, now=now) == []
    pid = repo.create_publication(
        Publication(
            term=10,
            number="3039",
            kind=PublicationKind.NEW_BILL,
            status=PublicationStatus.SENT,
            channel_id="chan",
            created_at=now,
            message_id=1,
        )
    )
    assert pid
    tracked = repo.list_tracked(10, "chan", closed_grace_days=30, now=now)
    assert [b.number for b in tracked] == ["3039"]
    assert tracked[0].stages_fingerprint == stage_fingerprint(process_3039.stages)
    change = StatusChange(
        term=10,
        number="3039",
        old_fingerprint="a",
        new_fingerprint="b",
        new_stages=list(process_3039.stages[1:]),
        detected_at=now,
    )
    assert repo.add_status_change(change) is not None
    assert repo.add_status_change(change) is None


def test_runs_and_dump_restore(repo, now) -> None:  # type: ignore[no-untyped-def]
    report = RunReport(started_at=now, since=now, mode="run")
    run_id = repo.start_run(report)
    assert repo.last_successful_run_started_at() is None
    report.finished_at = now
    repo.finish_run(run_id, report)
    assert repo.last_successful_run_started_at() == now
    script = repo.dump()
    other = SqliteBillRepository(":memory:")
    other.restore(script)
    other.migrate()
    assert other.last_successful_run_started_at() == now


def test_dry_run_transaction_rolls_back(repo, processes_page, now) -> None:  # type: ignore[no-untyped-def]
    repo.begin()
    repo.upsert_summary(processes_page[0], now=now)
    repo.rollback()
    assert repo.get(10, processes_page[0].number) is None
    assert now.tzinfo is UTC and isinstance(datetime.now(UTC), datetime)


def test_dump_restore_with_publications_and_status_changes(repo, process_3039, now) -> None:  # type: ignore[no-untyped-def]
    repo.upsert_summary(process_3039, now=now)
    change_id = repo.add_status_change(
        StatusChange(
            term=10,
            number="3039",
            old_fingerprint="a",
            new_fingerprint="b",
            new_stages=[],
            detected_at=now,
        )
    )
    repo.create_publication(
        Publication(
            term=10,
            number="3039",
            kind=PublicationKind.NEW_BILL,
            status=PublicationStatus.SENT,
            channel_id="chan",
            created_at=now,
            message_id=1,
        )
    )
    repo.create_publication(
        Publication(
            term=10,
            number="3039",
            kind=PublicationKind.STATUS_UPDATE,
            status=PublicationStatus.SENT,
            channel_id="chan",
            status_change_id=change_id,
            created_at=now,
            message_id=2,
        )
    )
    script = repo.dump()

    fresh = SqliteBillRepository(":memory:")
    fresh.migrate()  # a pre-existing schema must be replaced, not merged
    fresh.restore(script)
    fresh.migrate()
    assert fresh.get(10, "3039") is not None
    assert fresh.get_publication(10, "3039", "new_bill", "chan").message_id == 1  # type: ignore[union-attr]
    assert fresh.get_publication(10, "3039", "status_update", "chan").status_change_id == change_id  # type: ignore[union-attr]
    assert (
        fresh.add_status_change(
            StatusChange(
                term=10,
                number="3039",
                old_fingerprint="a",
                new_fingerprint="b",
                new_stages=[],
                detected_at=now,
            )
        )
        is None
    )  # unique index restored too
