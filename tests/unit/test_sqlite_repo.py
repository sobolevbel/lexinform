"""The SQLite repository: queries, uniqueness rules, dump/restore and migrations."""

import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from lexinform.adapters.sqlite_repo import MIGRATIONS, SCHEMA_VERSION, SqliteBillRepository
from lexinform.models import (
    ActInfo,
    AgendaItem,
    AmendmentsRecord,
    ApplicantType,
    BillContext,
    BillStatus,
    BillSubmission,
    IncomingCommand,
    ProcessDetail,
    ProcessSummary,
    Publication,
    PublicationKind,
    PublicationStatus,
    RunReport,
    StatusChange,
    process_summary,
    stage_fingerprint,
)
from tests.fakes import FakeLlm, make_amendments
from tests.harness import RCL, RCL_CONSULTATION, RCL_ID, rcl_project

CHANNEL = "chan"


def _tracked(repo: SqliteBillRepository, now: datetime) -> list[str]:
    bills = repo.list_tracked(CHANNEL, closed_grace_days=30, passed_max_days=180, now=now)
    return [b.number for b in bills]


def _publication(
    number: str, kind: PublicationKind, now: datetime, **overrides: Any
) -> Publication:
    fields: dict[str, Any] = dict(
        term=10,
        number=number,
        kind=kind,
        status=PublicationStatus.SENT,
        channel_id=CHANNEL,
        message_id=1,
        created_at=now,
    )
    fields.update(overrides)
    return Publication(**fields)


def _card_sent(repo: SqliteBillRepository, number: str, now: datetime) -> None:
    repo.create_publication(_publication(number, PublicationKind.NEW_BILL, now))


def _change(number: str, now: datetime, **overrides: Any) -> StatusChange:
    fields: dict[str, Any] = dict(
        term=10,
        number=number,
        old_fingerprint="a",
        new_fingerprint="b",
        new_stages=[],
        detected_at=now,
    )
    fields.update(overrides)
    return StatusChange(**fields)


def _submission(number: str, **overrides: Any) -> BillSubmission:
    fields: dict[str, Any] = dict(
        term=10,
        number=f"RPW/{number}/2026",
        title="t",
        date_of_receipt=date(2026, 9, 1),
        public_consultation=True,
        consultation_end=date(2026, 9, 30),
    )
    fields.update(overrides)
    return BillSubmission(**fields)


def _act(**overrides: Any) -> ActInfo:
    fields: dict[str, Any] = dict(
        eli="DU/2026/1099",
        display_address="Dz.U. 2026 poz. 1099",
        title="Ustawa z dnia 17 lipca 2026 r.",
        promulgation_date=date(2026, 8, 18),
        entry_into_force=date(2026, 11, 19),
        fetched_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    fields.update(overrides)
    return ActInfo(**fields)


def test_migrate_is_idempotent(repo: SqliteBillRepository) -> None:
    repo.migrate()
    repo.migrate()

    assert repo.schema_version == SCHEMA_VERSION
    assert repo.get(10, "1") is None


def test_upsert_keeps_status_and_hits_of_a_known_bill(
    repo: SqliteBillRepository, processes_page: list[ProcessSummary], now: datetime
) -> None:
    process = processes_page[0]
    first = repo.upsert_summary(process, now=now)
    repo.set_status(10, process.number, BillStatus.ANALYSIS_PENDING, prefilter_hits=["cudzoziemcy"])

    again = repo.upsert_summary(process, now=now)

    assert first.status is BillStatus.DISCOVERED
    assert again.status is BillStatus.ANALYSIS_PENDING
    assert again.prefilter_hits == ["cudzoziemcy"]


def test_analysed_bill_is_a_publish_candidate_until_its_channel_has_a_row(
    repo: SqliteBillRepository, processes_page: list[ProcessSummary], now: datetime
) -> None:
    for process in processes_page[:3]:
        repo.upsert_summary(process, now=now)
    number = processes_page[0].number
    repo.save_analysis(10, number, FakeLlm().analyze(_context(number)))

    before = repo.list_publish_candidates(CHANNEL, min_score=2, limit=10)
    repo.create_publication(
        _publication(number, PublicationKind.NEW_BILL, now, status=PublicationStatus.SKIPPED)
    )
    after = repo.list_publish_candidates(CHANNEL, min_score=2, limit=10)
    other_channel = repo.list_publish_candidates("other", min_score=2, limit=10)

    assert [c.number for c in before] == [number]
    assert after == []
    assert len(other_channel) == 1


def _context(number: str) -> BillContext:
    return BillContext(
        number=number,
        title="t",
        description=None,
        document_date=None,
        applicant_type=ApplicantType.UNKNOWN,
        text="",
        truncated=False,
        text_source="pdf",
    )


def test_analysis_failures_are_counted_and_the_last_error_kept(
    repo: SqliteBillRepository, processes_page: list[ProcessSummary], now: datetime
) -> None:
    number = processes_page[0].number
    repo.upsert_summary(processes_page[0], now=now)

    repo.record_analysis_failure(10, number, "boom")
    repo.record_analysis_failure(10, number, "boom again")

    bill = repo.get(10, number)
    assert bill is not None
    assert (bill.analysis_attempts, bill.status) == (2, BillStatus.ANALYSIS_FAILED)
    assert bill.last_error == "boom again"


def test_reset_puts_the_bill_back_with_a_clean_budget(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> None:
    repo.upsert_summary(process_3039, now=now)
    repo.record_analysis_failure(10, "3039", "boom")
    repo.record_analysis_failure(10, "3039", "boom again")

    repo.reset_bill(10, "3039", BillStatus.ANALYSIS_PENDING)

    bill = repo.get(10, "3039")
    assert bill is not None
    assert (bill.status, bill.analysis_attempts, bill.last_error) == (
        BillStatus.ANALYSIS_PENDING,
        0,
        None,
    )


def test_card_row_is_unique_per_bill_and_channel(
    repo: SqliteBillRepository, processes_page: list[ProcessSummary], now: datetime
) -> None:
    number = processes_page[0].number
    repo.upsert_summary(processes_page[0], now=now)
    failed = _publication(
        number, PublicationKind.NEW_BILL, now, status=PublicationStatus.FAILED, message_id=None
    )

    first = repo.create_publication(failed)
    second = repo.create_publication(
        failed.model_copy(update={"status": PublicationStatus.PENDING})
    )
    repo.mark_publication(
        first, PublicationStatus.SENT, message_id=42, document_message_ids=[43], sent_at=now
    )

    assert first == second
    stored = repo.get_publication(10, number, PublicationKind.NEW_BILL, CHANNEL)
    assert stored is not None
    assert (stored.status, stored.message_id, stored.document_message_ids) == (
        PublicationStatus.SENT,
        42,
        [43],
    )


def test_pending_rows_of_a_crashed_run_become_unknown(
    repo: SqliteBillRepository, processes_page: list[ProcessSummary], now: datetime
) -> None:
    number = processes_page[0].number
    repo.upsert_summary(processes_page[0], now=now)
    repo.create_publication(
        _publication(number, PublicationKind.NEW_BILL, now, status=PublicationStatus.PENDING)
    )

    marked = repo.mark_stale_pending_as_unknown(now=now)

    stored = repo.get_publication(10, number, PublicationKind.NEW_BILL, CHANNEL)
    assert marked == 1
    assert stored is not None and stored.status is PublicationStatus.UNKNOWN


def test_forgetting_a_card_lets_it_be_sent_again(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> None:
    repo.upsert_summary(process_3039, now=now)
    _card_sent(repo, "3039", now)

    deleted = repo.delete_publication(10, "3039", PublicationKind.NEW_BILL, CHANNEL)
    deleted_again = repo.delete_publication(10, "3039", PublicationKind.NEW_BILL, CHANNEL)

    assert (deleted, deleted_again) == (1, 0)
    assert repo.get_publication(10, "3039", PublicationKind.NEW_BILL, CHANNEL) is None


def test_agenda_posts_are_unique_per_sitting(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> None:
    repo.upsert_summary(process_3039, now=now)

    def post(ref: str) -> int:
        return repo.create_publication(
            _publication(
                "3039",
                PublicationKind.AGENDA,
                now,
                status=PublicationStatus.PENDING,
                message_id=None,
                ref=ref,
            )
        )

    committee = post("ASW/136/2026-09-17")
    plenary = post("sejm/65/2026-09-15")
    committee_again = post("ASW/136/2026-09-17")
    repo.mark_publication(committee, PublicationStatus.SENT, message_id=7, sent_at=now)

    assert committee != plenary
    assert committee_again == committee
    agenda = PublicationKind.AGENDA
    by_ref = repo.get_publication(10, "3039", agenda, CHANNEL, ref="ASW/136/2026-09-17")
    assert by_ref is not None and (by_ref.message_id, by_ref.ref) == (7, "ASW/136/2026-09-17")
    assert repo.get_publication(10, "3039", agenda, CHANNEL, ref="ASW/999/2026-10-01") is None
    latest = repo.get_publication(10, "3039", agenda, CHANNEL)
    assert latest is not None and latest.ref == "sejm/65/2026-09-15"


def test_agenda_items_round_trip(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> None:
    repo.upsert_summary(process_3039, now=now)
    items = (
        AgendaItem(kind="sejm", ref="sejm/65/2026-09-15", date=date(2026, 9, 15), text="x"),
        AgendaItem(
            kind="committee",
            ref="ASW/136/2026-09-17",
            date=date(2026, 9, 17),
            committee_code="ASW",
            text="Pierwsze czytanie (druk nr 3039)",
        ),
    )

    repo.save_agenda(10, "3039", items)
    stored = repo.get(10, "3039")
    repo.save_agenda(10, "3039", ())
    cleared = repo.get(10, "3039")

    assert stored is not None and stored.agenda == items
    assert cleared is not None and cleared.agenda == ()


def test_in_force_reminder_row_is_unique_per_bill_and_channel(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> None:
    repo.upsert_summary(process_3039, now=now)

    first = repo.create_publication(_publication("3039", PublicationKind.IN_FORCE, now))
    again = repo.create_publication(
        _publication("3039", PublicationKind.IN_FORCE, now, status=PublicationStatus.PENDING)
    )

    assert first == again


def test_status_change_is_stored_once_per_fingerprint(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> None:
    repo.upsert_summary(process_3039, now=now)
    change = _change("3039", now, new_stages=list(process_3039.stages[1:]))

    first = repo.add_status_change(change)
    second = repo.add_status_change(change)

    assert first is not None
    assert second is None


def test_amendments_summary_is_stored_with_its_status_change(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> None:
    repo.upsert_summary(process_3039, now=now)
    change_id = repo.add_status_change(_change("3039", now))
    assert change_id is not None
    record = AmendmentsRecord(
        amendments=make_amendments(),
        model="m",
        prompt_version="v",
        source_url="https://api.test/prints/2994/2994.pdf",
        source_kind="senate_amendments",
        created_at=now,
        input_tokens=40,
    )
    repo.create_publication(
        _publication(
            "3039",
            PublicationKind.STATUS_UPDATE,
            now,
            status=PublicationStatus.SKIPPED,
            message_id=None,
            status_change_id=change_id,
        )
    )

    repo.save_status_change_amendments(change_id, record)
    held = repo.list_held_status_changes(10, "3039", CHANNEL)

    assert len(held) == 1 and held[0].amendments == record
    released = repo.release_held_status_changes(10, "3039", CHANNEL, message_id=7, sent_at=now)
    assert released == 1 and repo.list_held_status_changes(10, "3039", CHANNEL) == []
    update = repo.get_publication(10, "3039", PublicationKind.STATUS_UPDATE, CHANNEL)
    assert update is not None and (update.status, update.message_id) == (
        PublicationStatus.SENT,
        7,
    )


def test_failed_status_updates_are_listed_for_retry_until_the_attempts_run_out(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> None:
    repo.upsert_summary(process_3039, now=now)
    change_id = repo.add_status_change(_change("3039", now, content_changed=True))
    pub_id = repo.create_publication(
        _publication(
            "3039",
            PublicationKind.STATUS_UPDATE,
            now,
            status=PublicationStatus.PENDING,
            message_id=None,
            status_change_id=change_id,
        )
    )

    repo.mark_publication(pub_id, PublicationStatus.FAILED, error="boom")
    listed = repo.list_failed_status_changes(CHANNEL, max_attempts=3)
    repo.mark_publication(pub_id, PublicationStatus.FAILED, error="down", count_attempt=False)
    repo.mark_publication(pub_id, PublicationStatus.FAILED, error="down", count_attempt=False)
    after_outages = repo.list_failed_status_changes(CHANNEL, max_attempts=3)
    repo.mark_publication(pub_id, PublicationStatus.FAILED, error="boom")
    repo.mark_publication(pub_id, PublicationStatus.FAILED, error="boom")
    exhausted = repo.list_failed_status_changes(CHANNEL, max_attempts=3)

    assert [c.id for c in listed] == [change_id]
    assert listed[0].content_changed is True
    assert [c.id for c in after_outages] == [change_id]  # outages are not the post's attempts
    assert exhausted == []
    assert repo.closure_announced(10, "3039") is False


def test_only_bills_with_a_sent_card_are_tracked(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> None:
    repo.upsert_summary(process_3039, now=now)
    fingerprint = stage_fingerprint(process_3039.stages)
    repo.save_stages(10, "3039", process_3039.stages, fingerprint)

    before = _tracked(repo, now)
    _card_sent(repo, "3039", now)
    after = repo.list_tracked(CHANNEL, closed_grace_days=30, passed_max_days=180, now=now)

    assert before == []
    assert [b.number for b in after] == ["3039"]
    assert after[0].stages_fingerprint == fingerprint


def test_passed_bills_stay_tracked_until_their_act_is_published(
    repo: SqliteBillRepository, process_3039: ProcessDetail
) -> None:
    closed = process_3039.model_copy(update={"closure_date": date(2026, 7, 17), "passed": True})
    now = datetime(2026, 8, 25, tzinfo=UTC)  # 39 days after closure: past the 30-day grace
    repo.upsert_summary(closed, now=now)
    _card_sent(repo, "3039", now)

    waiting = _tracked(repo, now)
    repo.save_act(10, "3039", _act())
    published = _tracked(repo, now)

    assert waiting == ["3039"]
    assert published == []  # the reminder query takes over


def test_passed_bill_without_an_act_is_dropped_after_passed_max_days(
    repo: SqliteBillRepository, process_3039: ProcessDetail
) -> None:
    closed = process_3039.model_copy(update={"closure_date": date(2026, 7, 17), "passed": True})
    now = datetime(2027, 3, 1, tzinfo=UTC)  # 200+ days: vetoed or in the Tribunal
    repo.upsert_summary(closed, now=now)
    _card_sent(repo, "3039", now)

    assert _tracked(repo, now) == []


def test_list_tracked_filters_by_change_date_but_keeps_passed_bills(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> None:
    stale = process_3039.model_copy(update={"change_date": datetime(2026, 9, 1, 10, tzinfo=UTC)})
    fresh = process_3039.model_copy(
        update={"number": "3040", "change_date": datetime(2026, 9, 6, 10, tzinfo=UTC)}
    )
    passed = process_3039.model_copy(
        update={
            "number": "3041",
            "change_date": datetime(2026, 9, 1, 10, tzinfo=UTC),
            "passed": True,
            "closure_date": date(2026, 9, 1),
        }
    )
    for process in (stale, fresh, passed):
        repo.upsert_summary(process, now=now)
        _card_sent(repo, process.number, now)

    def tracked(changed_since: datetime | None) -> list[str]:
        bills = repo.list_tracked(
            CHANNEL,
            closed_grace_days=90,
            passed_max_days=180,
            now=now,
            changed_since=changed_since,
        )
        return [b.number for b in bills]

    everyone = tracked(None)
    aware = tracked(datetime(2026, 9, 5, tzinfo=UTC))
    naive = tracked(datetime(2026, 9, 5))

    assert everyone == ["3039", "3040", "3041"]
    assert aware == ["3040", "3041"]  # passed without an act: always
    assert naive == ["3040", "3041"]


def test_in_force_reminders_are_due_from_the_day_on_until_reminded(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> None:
    repo.upsert_summary(process_3039, now=now)
    _card_sent(repo, "3039", now)
    repo.save_act(10, "3039", _act())  # in force 2026-11-19

    eve = repo.list_due_in_force(CHANNEL, today=date(2026, 11, 18))
    day = repo.list_due_in_force(CHANNEL, today=date(2026, 11, 19))
    repo.create_publication(_publication("3039", PublicationKind.IN_FORCE, now))
    reminded = repo.list_due_in_force(CHANNEL, today=date(2026, 12, 1))

    assert eve == []
    assert [b.number for b in day] == ["3039"] and day[0].act is not None
    assert reminded == []


def test_consultation_reminders_are_due_inside_the_window_only(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> None:
    def bill(number: str, end: date | None, *, public: bool = True) -> None:
        repo.upsert_summary(process_3039.model_copy(update={"number": number}), now=now)
        _card_sent(repo, number, now)
        repo.save_submission(
            10, number, _submission(number, public_consultation=public, consultation_end=end)
        )

    bill("1", date(2026, 9, 10))  # last day: due
    bill("2", date(2026, 9, 13))  # exactly days_before ahead: due
    bill("3", date(2026, 9, 14))  # one day too early
    bill("4", date(2026, 9, 9))  # already over
    bill("5", date(2026, 9, 11), public=False)  # no public consultation
    bill("6", None)
    project = rcl_project(
        consultation=RCL_CONSULTATION.model_copy(update={"deadline": today_plus(2)})
    )
    repo.upsert_summary(process_summary(project, term=10), now=now)
    _card_sent(repo, RCL, now)
    repo.save_rcl(10, RCL, project)  # a government project consulted on RCL: due as well
    today = date(2026, 9, 10)

    due = repo.list_due_consultations(CHANNEL, today=today, days_before=3)
    repo.create_publication(_publication("1", PublicationKind.CONSULTATION_DEADLINE, now))
    after_reminder = repo.list_due_consultations(CHANNEL, today=today, days_before=3)
    other_channel = repo.list_due_consultations("other", today=today, days_before=3)

    assert [b.number for b in due] == ["1", RCL, "2"]
    assert [b.number for b in after_reminder] == [RCL, "2"]
    assert other_channel == []


def today_plus(days: int) -> date:
    return date(2026, 9, 10) + timedelta(days=days)


def test_rcl_project_round_trips_and_is_found_by_its_numbers(
    repo: SqliteBillRepository, now: datetime
) -> None:
    project = rcl_project(rm_number="RM-0610-139-26")
    repo.upsert_summary(process_summary(project, term=10), now=now)

    repo.save_rcl(10, RCL, project)

    stored = repo.get(10, RCL)
    assert stored is not None and stored.rcl == project and stored.is_rcl
    assert (
        stored.consultation is not None and stored.consultation.email == "dep.prawny@mswia.gov.pl"
    )
    by_rm = repo.find_by_rm_number("RM-0610-139-26")
    by_wykaz = repo.find_by_wykaz_number("UC164")
    assert by_rm is not None and by_rm.number == RCL
    assert by_wykaz is not None and by_wykaz.number == RCL
    assert repo.find_by_rm_number("RM-0610-1-26") is None


def test_bills_awaiting_consultation_results(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> None:
    def bill(number: str, *, public: bool, results: bool, card: bool = True) -> None:
        repo.upsert_summary(process_3039.model_copy(update={"number": number}), now=now)
        if card:
            _card_sent(repo, number, now)
        repo.save_submission(
            10,
            number,
            _submission(
                number,
                public_consultation=public,
                consultation_end=date(2026, 9, 30) if public else None,
                consultation_results=results,
            ),
        )

    bill("1", public=True, results=False)  # awaiting
    bill("2", public=True, results=True)  # already published
    bill("3", public=False, results=False)  # no consultation
    bill("4", public=True, results=False, card=False)  # never posted: nothing to reply under
    repo.upsert_summary(process_3039.model_copy(update={"number": "5"}), now=now)  # no /bills row

    awaiting = repo.list_awaiting_consultation_results(CHANNEL)
    other_channel = repo.list_awaiting_consultation_results("other")

    assert [b.number for b in awaiting] == ["1"]
    assert other_channel == []


def test_watermark_is_the_last_run_whose_discovery_completed(
    repo: SqliteBillRepository, now: datetime
) -> None:
    report = RunReport(started_at=now, since=now, mode="run", finished_at=now)
    run_id = repo.start_run(report)

    report.errors.append("publishing: 1 publication(s) failed")
    repo.finish_run(run_id, report)
    before_discovery_ok = repo.last_discovery_started_at()
    report.discovery_ok = True
    repo.finish_run(run_id, report)
    after = repo.last_discovery_started_at()

    assert before_discovery_ok is None  # errors after discovery do not hold the watermark back
    assert after == now


def test_dry_run_transaction_rolls_back(
    repo: SqliteBillRepository, processes_page: list[ProcessSummary], now: datetime
) -> None:
    repo.begin()
    repo.upsert_summary(processes_page[0], now=now)

    repo.rollback()

    assert repo.get(10, processes_page[0].number) is None


def test_dump_restores_rows_indexes_and_the_schema_version(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> None:
    repo.upsert_summary(process_3039, now=now)
    change_id = repo.add_status_change(_change("3039", now))
    _card_sent(repo, "3039", now)
    repo.create_publication(
        _publication(
            "3039", PublicationKind.STATUS_UPDATE, now, message_id=2, status_change_id=change_id
        )
    )
    report = RunReport(started_at=now, since=now, mode="run", finished_at=now, discovery_ok=True)
    repo.finish_run(repo.start_run(report), report)

    script = repo.dump()
    fresh = SqliteBillRepository(":memory:")
    fresh.migrate()  # a pre-existing schema must be replaced, not merged
    fresh.restore(script)

    assert script.rstrip().endswith(f"PRAGMA user_version = {SCHEMA_VERSION};")
    assert fresh.schema_version == SCHEMA_VERSION
    assert fresh.get(10, "3039") is not None
    assert fresh.last_discovery_started_at() == now
    card = fresh.get_publication(10, "3039", PublicationKind.NEW_BILL, CHANNEL)
    update = fresh.get_publication(10, "3039", PublicationKind.STATUS_UPDATE, CHANNEL)
    assert card is not None and card.message_id == 1
    assert update is not None and update.status_change_id == change_id
    assert fresh.add_status_change(_change("3039", now)) is None  # unique index restored too


def test_restore_of_a_v1_dump_applies_every_later_migration(tmp_path: Path) -> None:
    legacy = sqlite3.connect(tmp_path / "v1.db")
    legacy.executescript(f"BEGIN;{MIGRATIONS[0]}PRAGMA user_version = 1;COMMIT;")
    legacy.execute(
        "INSERT INTO runs (started_at, since, mode, ok) VALUES ('2026-09-01T05:00:00+00:00',"
        " '2026-08-31T05:00:00+00:00', 'run', 1)"
    )
    legacy_dump = "\n".join(legacy.iterdump()) + "\n"  # the v1 release wrote no version line
    legacy.close()
    repo = SqliteBillRepository(tmp_path / "current.db")
    repo.migrate()

    repo.restore(legacy_dump)

    assert "user_version" not in legacy_dump
    assert repo.schema_version == SCHEMA_VERSION
    assert repo.last_discovery_started_at() is not None  # v2 backfill: ok -> discovery_ok
    with sqlite3.connect(tmp_path / "current.db") as conn:
        publications = {r[1] for r in conn.execute("PRAGMA table_info(publications)")}
        bills = {r[1] for r in conn.execute("PRAGMA table_info(bills)")}
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"attempts", "ref"} <= publications
    assert {
        "submission_json",
        "linked_number",
        "act_json",
        "entry_into_force",
        "agenda_json",
        "rcl_json",
        "discontinued_at",
        "linked_wykaz_number",
    } <= bills
    assert {
        "ux_pub_once_per_kind",
        "ux_pub_consultation",
        "ux_pub_consultation_results",
        "ux_pub_agenda",
        "ux_pub_hearing",
        "ux_pub_joint",  # v12
    } <= indexes
    with sqlite3.connect(tmp_path / "current.db") as conn:
        changes = {r[1] for r in conn.execute("PRAGMA table_info(status_changes)")}
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        commands = {r[1] for r in conn.execute("PRAGMA table_info(commands)")}
    assert "amendments_json" in changes  # v11
    assert "commands" in tables  # v13
    assert "executed_at" in commands  # v14
    # v9: the flag is stored, so a retried post renders the same message
    when = datetime(2026, 9, 7, 6, 0, tzinfo=UTC)
    assert repo.add_status_change(_change("1", when, discontinued=True)) is not None


def test_a_skipped_rcl_project_keeps_only_its_skeleton(
    repo: SqliteBillRepository, now: datetime
) -> None:
    kept = _rcl_row(repo, now)
    skipped = _rcl_row(repo, now, id=RCL_ID + 1)

    repo.set_status(10, kept, BillStatus.ANALYSIS_PENDING, prefilter_hits=["cudzoziemcy"])
    repo.set_status(10, skipped, BillStatus.SKIPPED_TEXT_PREFILTER, prefilter_hits=[])

    full, slim = repo.get(10, kept), repo.get(10, skipped)
    assert full is not None and full.rcl is not None and full.rcl.text_documents() != {}
    assert slim is not None and slim.rcl is not None and slim.rcl.text_documents() == {}
    assert len(slim.rcl.stages) == len(full.rcl.stages)  # the timeline stays
    assert slim.rcl.consultation == full.rcl.consultation


def test_old_run_records_are_pruned_and_the_watermark_survives(
    repo: SqliteBillRepository, now: datetime
) -> None:
    for days_ago in (120, 100, 5):
        started = now - timedelta(days=days_ago)
        report = RunReport(started_at=started, since=started, mode="run", discovery_ok=True)
        repo.finish_run(repo.start_run(report), report)

    pruned = repo.prune_runs(before=now - timedelta(days=90))

    assert pruned == 2
    assert repo.last_discovery_started_at() == now - timedelta(days=5)


def _rcl_row(repo: SqliteBillRepository, now: datetime, **fields: Any) -> str:
    project = rcl_project(**fields)
    number = repo.upsert_summary(process_summary(project, term=10), now=now).number
    repo.save_rcl(10, number, project)
    return number


def test_known_terms_lists_every_term_with_bills(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> None:
    assert repo.known_terms() == []

    repo.upsert_summary(process_3039.model_copy(update={"term": 11}), now=now)
    repo.upsert_summary(process_3039, now=now)

    assert repo.known_terms() == [10, 11]


def test_rcl_projects_waiting_for_their_druk_move_to_the_new_term_with_their_posts(
    repo: SqliteBillRepository, now: datetime
) -> None:
    waiting = _rcl_row(repo, now)
    _card_sent(repo, waiting, now)
    repo.add_status_change(_change(waiting, now, closure_detected=True))
    joined = _rcl_row(repo, now, id=RCL_ID + 1, print_number="3100")

    moved = repo.move_rcl_projects(10, 11)

    assert moved == 1
    carried = repo.get(11, waiting)
    assert repo.get(10, waiting) is None and carried is not None
    assert (carried.term, carried.summary.term, carried.rcl is not None) == (11, 11, True)
    assert repo.get_publication(11, waiting, PublicationKind.NEW_BILL, CHANNEL) is not None
    assert repo.closure_announced(11, waiting) and not repo.closure_announced(10, waiting)
    assert repo.get(10, joined) is not None  # already joined to its druk: stays with it
    assert repo.move_rcl_projects(10, 11) == 0


def test_unfinished_bills_of_a_term_lapse_and_leave_every_listing(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> None:
    repo.upsert_summary(process_3039, now=now)  # in committee, published
    _card_sent(repo, "3039", now)
    passed = process_3039.model_copy(
        update={"number": "3040", "closure_date": now.date(), "passed": True}
    )
    repo.upsert_summary(passed, now=now)  # passed by the Sejm, waiting for the act
    _card_sent(repo, "3040", now)
    repo.upsert_summary(process_3039.model_copy(update={"number": "3050"}), now=now)
    repo.set_status(10, "3050", BillStatus.ANALYSIS_PENDING)  # never published

    unfinished = repo.list_unfinished_published(10, CHANNEL)
    marked = repo.discontinue_unfinished(10, at=now)
    lapsed = repo.get(10, "3039")

    assert [b.number for b in unfinished] == ["3039"]
    assert marked == 2  # 3039 and the unpublished 3050; the passed 3040 goes on
    assert lapsed is not None and lapsed.discontinued_at == now
    assert _tracked(repo, now) == ["3040"]
    assert repo.list_by_status([BillStatus.ANALYSIS_PENDING], limit=10) == []
    assert repo.list_unfinished_published(10, CHANNEL) == []
    assert repo.discontinue_unfinished(10, at=now) == 0


def _command(update_id: int, now: datetime, text: str = "/analyze 3039") -> IncomingCommand:
    return IncomingCommand(
        update_id=update_id, chat_id="-100", message_id=update_id + 10, text=text, received_at=now
    )


def test_a_command_is_recorded_once_and_marked_executed_then_handled(
    repo: SqliteBillRepository, now: datetime
) -> None:
    first = repo.record_command(_command(5, now))
    recorded = repo.command_state(5)
    repo.mark_command_executed(5, outcome="3039 analysed (message 101)", at=now)
    executed = repo.command_state(5)
    repo.mark_command_handled(5, reply="3039 analysed (message 101)", at=now)
    again = repo.record_command(_command(5, now))

    assert first and not again  # the same Telegram update is never a second command
    assert recorded is not None and (recorded.executed_at, recorded.handled_at) == (None, None)
    assert executed is not None and executed.executed_at == now and executed.handled_at is None
    assert executed.reply == "3039 analysed (message 101)"  # what an unanswered command did
    handled = repo.command_state(5)
    assert handled is not None and handled.handled_at == now
    assert repo.command_state(6) is None  # unknown updates have no state
