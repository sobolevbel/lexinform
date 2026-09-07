from __future__ import annotations

from datetime import UTC, date, datetime

from lexinform.adapters.sqlite_repo import MIGRATIONS, SCHEMA_VERSION, SqliteBillRepository
from lexinform.models import (
    ActInfo,
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
    assert repo.list_tracked(10, "chan", closed_grace_days=30, passed_max_days=180, now=now) == []
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
    tracked = repo.list_tracked(10, "chan", closed_grace_days=30, passed_max_days=180, now=now)
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
    assert repo.last_discovery_started_at() is None
    report.finished_at = now
    report.errors.append("publishing: 1 publication(s) failed")
    repo.finish_run(run_id, report)
    # errors after discovery do not hold the watermark back
    assert repo.last_discovery_started_at() is None
    report.discovery_ok = True
    repo.finish_run(run_id, report)
    assert repo.last_discovery_started_at() == now
    script = repo.dump()
    assert script.rstrip().endswith(f"PRAGMA user_version = {SCHEMA_VERSION};")
    other = SqliteBillRepository(":memory:")
    other.restore(script)
    other.migrate()
    assert other.last_discovery_started_at() == now


def test_restore_of_a_previous_schema_dump_applies_missing_migrations() -> None:
    """A dump from an older release (v1, no version line) must be migrated, not stamped current."""
    old = SqliteBillRepository(":memory:")
    old._conn.executescript(f"BEGIN;{MIGRATIONS[0]}PRAGMA user_version = 1;COMMIT;")
    old._conn.execute(
        "INSERT INTO runs (started_at, since, mode, ok) VALUES ('2026-09-01T05:00:00+00:00',"
        " '2026-08-31T05:00:00+00:00', 'run', 1)"
    )
    legacy_dump = "\n".join(old._conn.iterdump()) + "\n"  # what the v1 release wrote
    assert "user_version" not in legacy_dump

    repo = SqliteBillRepository(":memory:")
    repo.migrate()  # fresh schema at the current version, as in the daily workflow
    repo.restore(legacy_dump)
    assert int(repo._conn.execute("PRAGMA user_version").fetchone()[0]) == SCHEMA_VERSION
    columns = {r[1] for r in repo._conn.execute("PRAGMA table_info(publications)")}
    assert "attempts" in columns
    bill_columns = {r[1] for r in repo._conn.execute("PRAGMA table_info(bills)")}
    assert {"submission_json", "linked_number", "act_json", "entry_into_force"} <= bill_columns
    # the v2 backfill copies ok -> discovery_ok for old runs
    assert repo.last_discovery_started_at() is not None


def test_failed_status_updates_are_listed_for_retry(repo, process_3039, now) -> None:  # type: ignore[no-untyped-def]
    repo.upsert_summary(process_3039, now=now)
    change_id = repo.add_status_change(
        StatusChange(
            term=10,
            number="3039",
            old_fingerprint="a",
            new_fingerprint="b",
            new_stages=[],
            content_changed=True,
            detected_at=now,
        )
    )
    pub_id = repo.create_publication(
        Publication(
            term=10,
            number="3039",
            kind=PublicationKind.STATUS_UPDATE,
            status=PublicationStatus.PENDING,
            channel_id="chan",
            status_change_id=change_id,
            created_at=now,
        )
    )
    repo.mark_publication(pub_id, PublicationStatus.FAILED, error="boom")
    [change] = repo.list_failed_status_changes(10, "chan", max_attempts=3)
    assert change.id == change_id and change.content_changed is True
    repo.mark_publication(pub_id, PublicationStatus.FAILED, error="boom")
    repo.mark_publication(pub_id, PublicationStatus.FAILED, error="boom")
    assert repo.list_failed_status_changes(10, "chan", max_attempts=3) == []
    assert repo.closure_announced(10, "3039") is False


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


def _act(**kw: object) -> ActInfo:
    base: dict[str, object] = dict(
        eli="DU/2026/1099",
        display_address="Dz.U. 2026 poz. 1099",
        title="Ustawa z dnia 17 lipca 2026 r.",
        promulgation_date=date(2026, 8, 18),
        entry_into_force=date(2026, 11, 19),
        fetched_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    base.update(kw)
    return ActInfo(**base)  # type: ignore[arg-type]


def _sent_card(repo: SqliteBillRepository, number: str, now: datetime) -> None:
    repo.create_publication(
        Publication(
            term=10,
            number=number,
            kind=PublicationKind.NEW_BILL,
            status=PublicationStatus.SENT,
            channel_id="chan",
            created_at=now,
            message_id=1,
        )
    )


def test_passed_bills_stay_tracked_until_their_act_is_published(repo, process_3039) -> None:  # type: ignore[no-untyped-def]
    closed = process_3039.model_copy(update={"closure_date": date(2026, 7, 17), "passed": True})
    now = datetime(2026, 8, 25, tzinfo=UTC)  # 39 days after closure
    repo.upsert_summary(closed, now=now)
    _sent_card(repo, "3039", now)

    def tracked() -> list[str]:
        bills = repo.list_tracked(10, "chan", closed_grace_days=30, passed_max_days=180, now=now)
        return [b.number for b in bills]

    assert tracked() == ["3039"]  # past the 30-day grace, but passed and not published yet
    repo.save_act(10, "3039", _act())
    assert tracked() == []  # published: the reminder query takes over
    now = datetime(2027, 3, 1, tzinfo=UTC)
    repo.upsert_summary(closed, now=now)
    repo._conn.execute("UPDATE bills SET act_json = NULL")
    assert tracked() == []  # 200+ days without publication (veto, Tribunal): dropped


def test_in_force_reminders_are_due_once(repo, process_3039, now) -> None:  # type: ignore[no-untyped-def]
    repo.upsert_summary(process_3039, now=now)
    _sent_card(repo, "3039", now)
    repo.save_act(10, "3039", _act())
    assert repo.list_due_in_force(10, "chan", today=date(2026, 11, 18)) == []
    due = repo.list_due_in_force(10, "chan", today=date(2026, 11, 19))
    assert [b.number for b in due] == ["3039"] and due[0].act is not None
    first = repo.create_publication(
        Publication(
            term=10,
            number="3039",
            kind=PublicationKind.IN_FORCE,
            status=PublicationStatus.SENT,
            channel_id="chan",
            created_at=now,
        )
    )
    again = repo.create_publication(
        Publication(
            term=10,
            number="3039",
            kind=PublicationKind.IN_FORCE,
            status=PublicationStatus.PENDING,
            channel_id="chan",
            created_at=now,
        )
    )
    assert first == again  # one reminder per bill and channel, enforced by the schema
    assert repo.list_due_in_force(10, "chan", today=date(2026, 12, 1)) == []
