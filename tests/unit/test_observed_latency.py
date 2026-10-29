import datetime as dt
import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest
from tools.observed_latency import LatencyRow, ObservedLatencyReport, observed_latency
from tools.work_doctor import audit

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    AnalysisRecord,
    Bill,
    DeliveryPlan,
    ProcessSummary,
    Publication,
    PublicationKind,
    PublicationStatus,
    ResultApplication,
    ResultApplicationTarget,
    StatusChange,
)
from lexinform.models.batch import BatchKind
from lexinform.models.commands import DeliveryResolution, IncomingCommand
from lexinform.models.observations import UpdateSnapshot
from lexinform.models.wykaz import WykazEntry
from tests.computations import NOW, retained_result

OBSERVED = NOW + dt.timedelta(days=1)


@pytest.fixture
def repository() -> Iterator[SqliteBillRepository]:
    repo = SqliteBillRepository(":memory:")
    repo.migrate()
    yield repo
    repo.close()


def owner(repo: SqliteBillRepository, number: str = "3039") -> Bill:
    return repo.upsert_summary(
        ProcessSummary(
            term=10,
            number=number,
            title="PRIVATE TITLE",
            document_type="projekt ustawy",
            change_date=NOW - dt.timedelta(days=100),
        ),
        now=NOW - dt.timedelta(hours=1),
    )


def copy(repo: SqliteBillRepository) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(repo.dump())
    return connection


def row(report: ObservedLatencyReport, metric: str, ref: str | None = None) -> LatencyRow:
    rows = [
        item for item in report.rows if item.metric == metric and (ref is None or item.ref == ref)
    ]
    assert len(rows) == 1, "The fixture has exactly one matching latency fact"
    return rows[0]


def analyzed(repo: SqliteBillRepository) -> None:
    owner(repo)
    result = retained_result("analysis")
    repo.save_computation_result(result)
    assert isinstance(result.record, AnalysisRecord), "The analysis fixture retains an analysis"
    repo.save_analysis(10, "3039", result.record)
    repo.save_result_application(
        ResultApplication(
            result_id=result.result_id,
            term=10,
            number="3039",
            generation=0,
            phase="projection",
            outcome="applied",
            reason="batch_result",
            observed_generation=0,
            observed_status=None,
            created_at=NOW + dt.timedelta(hours=1),
        )
    )


def publication(repo: SqliteBillRepository, bill: Bill, *, change_id: int | None = None) -> int:
    return repo.create_publication(
        Publication(
            term=bill.term,
            number=bill.number,
            kind=PublicationKind.STATUS_UPDATE if change_id else PublicationKind.NEW_BILL,
            status=PublicationStatus.SENT,
            channel_id="channel",
            message_id=42,
            status_change_id=change_id,
            created_at=NOW,
            sent_at=NOW + dt.timedelta(hours=2),
            delivery=DeliveryPlan(bill_json=bill.model_dump_json()),
        )
    )


def test_exact_collection_application_and_send_clocks_have_distinct_meanings(
    repository: SqliteBillRepository,
) -> None:
    analyzed(repository)
    bill = repository.get(10, "3039")
    assert bill is not None, "The arranged owner has an applied analysis"
    publication(repository, bill)
    with closing(copy(repository)) as connection:
        before = connection.serialize()
        connection.execute("PRAGMA query_only=ON")
        report = observed_latency(connection, OBSERVED)
        assert connection.serialize() == before
    assert row(report, "analysis_collected").seconds == 3600
    assert row(report, "analysis_collected").projection_match
    assert row(report, "analysis_applied").seconds == 7200
    assert row(report, "analysis_applied").purpose == "analysis"
    assert row(report, "delivery").seconds == 10800
    assert row(report, "read_completed").reason == "read_completion_unavailable"
    assert row(report, "source_discovery").reason == "official_availability_unknown"
    assert "PRIVATE TITLE" not in report.model_dump_json()
    assert report.coverage == "partial" and not report.missing_schema


@pytest.mark.parametrize(
    ("stamp", "reason"),
    [
        ("", "missing_timestamp"),
        ("invalid", "invalid_timestamp"),
        ("2026-10-27T00:00:00", "timezone_unknown"),
        ("2026-10-29T00:00:00Z", "timestamp_after_observation"),
        ("2026-10-27T00:30:00Z", "reversed_timestamps"),
    ],
)
def test_unknown_or_reversed_discovery_clocks_never_become_zero(
    repository: SqliteBillRepository,
    stamp: str,
    reason: str,
) -> None:
    analyzed(repository)
    with closing(copy(repository)) as connection:
        connection.execute("UPDATE bills SET first_seen_at=?", (stamp,))
        value = row(observed_latency(connection, OBSERVED), "analysis_collected")
    assert value.seconds is None and value.reason == reason


def test_current_projection_requires_exact_ref_and_generation_not_latest_result(
    repository: SqliteBillRepository,
) -> None:
    analyzed(repository)
    with closing(copy(repository)) as connection:
        connection.execute("UPDATE bills SET analysis_json=NULL,analysis_generation=0")
        value = row(observed_latency(connection, OBSERVED), "analysis_collected")
        assert value.seconds == 3600 and not value.projection_match
        connection.execute("UPDATE bills SET analysis_generation=1")
        report = observed_latency(connection, OBSERVED)
    assert row(report, "analysis_collected").reason == "stale_generation"
    assert row(report, "analysis_applied").reason == "stale_generation"


def test_rekeyed_result_and_application_owners_cannot_borrow_discovery(
    repository: SqliteBillRepository,
) -> None:
    analyzed(repository)
    with closing(copy(repository)) as connection:
        connection.execute("DELETE FROM bills")
        report = observed_latency(connection, OBSERVED)
        assert row(report, "analysis_collected").reason == "missing_owner"
        assert row(report, "analysis_applied").reason == "missing_owner"
    owner(repository, "3040")
    with closing(copy(repository)) as connection:
        application = repository.list_result_applications(10, "3039")[0]
        changed = application.model_copy(update={"number": "3040"})
        connection.execute(
            "UPDATE result_applications SET number='3040',application_json=?",
            (changed.model_dump_json(),),
        )
        value = row(
            observed_latency(connection, OBSERVED), "analysis_applied", "result_applications/1"
        )
    assert value.seconds is None and value.reason == "cross_owner_result"


@pytest.mark.parametrize("kind", ["reanalysis", "amendments"])
def test_later_purposes_are_not_timed_from_first_discovery(
    repository: SqliteBillRepository, kind: BatchKind
) -> None:
    owner(repository)
    result = retained_result(kind)
    repository.save_computation_result(result)
    repository.save_result_application(
        ResultApplication(
            result_id=result.result_id,
            term=10,
            number="3039",
            generation=result.generation,
            target=None
            if result.generation is not None
            else ResultApplicationTarget(kind="supplement", ref="10/3039"),
            phase="projection",
            outcome="applied",
            reason="batch_result",
            observed_generation=0,
            observed_status=None,
            created_at=NOW + dt.timedelta(hours=1),
        )
    )
    with closing(copy(repository)) as connection:
        report = observed_latency(connection, OBSERVED)
    for metric in ("analysis_collected", "analysis_applied"):
        value = row(report, metric)
        assert value.purpose == kind
        assert value.seconds is None and value.reason == "trigger_event_unbound"


def test_application_cannot_precede_its_bound_collection(repository: SqliteBillRepository) -> None:
    analyzed(repository)
    with closing(copy(repository)) as connection:
        application = repository.list_result_applications(10, "3039")[0]
        changed = application.model_copy(update={"created_at": NOW - dt.timedelta(minutes=1)})
        connection.execute(
            "UPDATE result_applications SET application_json=?", (changed.model_dump_json(),)
        )
        value = row(observed_latency(connection, OBSERVED), "analysis_applied")
    assert value.reason == "application_before_collection" and value.seconds is None


def test_event_delivery_is_bound_to_the_exact_saved_event_owner(
    repository: SqliteBillRepository,
) -> None:
    bill = owner(repository)
    change_id = repository.add_status_change(
        StatusChange(
            term=10,
            number="3039",
            old_fingerprint=None,
            new_fingerprint="event",
            new_stages=[],
            detected_at=NOW,
        )
    )
    assert change_id is not None, "The new event fingerprint is unique"
    publication(repository, bill, change_id=change_id)
    with closing(copy(repository)) as connection:
        value = row(observed_latency(connection, OBSERVED), "delivery")
        assert value.seconds == 7200 and value.start_field == "status_changes.detected_at"
        connection.execute("UPDATE status_changes SET number='other'")
        value = row(observed_latency(connection, OBSERVED), "delivery")
    assert value.seconds is None and value.reason == "event_owner_mismatch"


def test_manual_confirmation_is_not_an_automatic_send_time(
    repository: SqliteBillRepository,
) -> None:
    bill = owner(repository)
    identity = publication(repository, bill)
    resolution = DeliveryResolution(
        publication_id=identity,
        term=10,
        number=bill.number,
        kind=PublicationKind.NEW_BILL,
        channel_id="channel",
        action="confirm",
        message_id=42,
        command=IncomingCommand(
            update_id=1, chat_id="ops", message_id=2, text="PRIVATE COMMAND", received_at=NOW
        ),
        resolved_at=NOW + dt.timedelta(hours=2),
    )
    with closing(copy(repository)) as connection:
        connection.execute(
            "INSERT INTO delivery_resolutions(publication_id,command_update_id,resolution_json) VALUES(?,?,?)",
            (identity, 1, resolution.model_dump_json()),
        )
        report = observed_latency(connection, OBSERVED)
    value = row(report, "delivery")
    assert value.delivery_class == "manual_confirmation"
    assert value.seconds is None and value.reason == "manual_confirmation_not_send_time"
    assert "PRIVATE COMMAND" not in report.model_dump_json()


def test_shared_message_needs_explicit_membership_and_matching_confirmation(
    repository: SqliteBillRepository,
) -> None:
    first, second = owner(repository), owner(repository, "3040")
    a, b = publication(repository, first), publication(repository, second)
    with closing(copy(repository)) as connection:
        report = observed_latency(connection, OBSERVED)
        assert (
            row(report, "delivery", f"publications/{b}").reason
            == "shared_message_without_membership"
        )
        connection.execute("INSERT INTO digest_event_links VALUES(?,?)", (b, a))
        value = row(observed_latency(connection, OBSERVED), "delivery", f"publications/{b}")
        assert value.delivery_class == "recorded_shared_membership" and value.seconds == 10800
        connection.execute("UPDATE publications SET sent_at=? WHERE id=?", (NOW.isoformat(), a))
        value = row(observed_latency(connection, OBSERVED), "delivery", f"publications/{b}")
    assert value.seconds is None and value.reason == "invalid_delivery_membership"


def test_only_exact_wykaz_announcement_has_official_availability(
    repository: SqliteBillRepository,
) -> None:
    entry = WykazEntry(
        number="UD999",
        title="PRIVATE TITLE",
        kind="Projekt ustawy",
        published_at=NOW - dt.timedelta(hours=2),
        web_url="https://example.test/private",
    )
    owner(repository, entry.bill_number)
    repository.save_wykaz(10, entry.bill_number, entry)
    with closing(copy(repository)) as connection:
        value = row(observed_latency(connection, OBSERVED), "source_discovery")
        assert value.seconds == 3600 and value.source_precision == "minute"
        assert value.source_timezone == "Europe/Warsaw"
        connection.execute("UPDATE bills SET number='3039'")
        report = observed_latency(connection, OBSERVED)
    assert row(report, "source_discovery").reason == "official_availability_unknown"
    assert "example.test" not in report.model_dump_json()


def test_legacy_schema_is_explicit_and_does_not_invent_dates() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        report = observed_latency(connection, OBSERVED)
    assert report.missing_schema and not report.rows and report.coverage == "partial"
    with (
        closing(sqlite3.connect(":memory:")) as connection,
        pytest.raises(ValueError, match="timezone"),
    ):
        observed_latency(connection, OBSERVED.replace(tzinfo=None))


def test_corrupt_result_identity_cannot_lend_an_application_clock(
    repository: SqliteBillRepository,
) -> None:
    analyzed(repository)
    with closing(copy(repository)) as connection:
        connection.execute("UPDATE computation_results SET custom_id='different'")
        report = observed_latency(connection, OBSERVED)
        assert row(report, "analysis_collected").reason == "result_identity_mismatch"
        assert row(report, "analysis_applied").reason == "missing_valid_result"
        connection.execute("DROP TABLE computation_results")
        report = observed_latency(connection, OBSERVED)
    assert row(report, "analysis_collected").reason == "result_schema_unavailable"
    assert "computation_results.result_json" in report.missing_schema


def test_source_subminute_value_does_not_claim_official_precision(
    repository: SqliteBillRepository,
) -> None:
    entry = WykazEntry(
        number="UD999",
        title="PRIVATE TITLE",
        kind="Projekt ustawy",
        published_at=NOW - dt.timedelta(hours=2) + dt.timedelta(seconds=1),
        web_url="https://example.test/private",
    )
    owner(repository, entry.bill_number)
    repository.save_wykaz(10, entry.bill_number, entry)
    with closing(copy(repository)) as connection:
        value = row(observed_latency(connection, OBSERVED), "source_discovery")
    assert value.seconds is None and value.reason == "source_precision_mismatch"


def test_self_group_cannot_bypass_its_frozen_owner(repository: SqliteBillRepository) -> None:
    first, other = owner(repository), owner(repository, "3040")
    identity = publication(repository, first)
    plan = DeliveryPlan(bill_json=other.model_dump_json(), sitting_group=(identity,))
    with closing(copy(repository)) as connection:
        connection.execute("UPDATE publications SET delivery_json=?", (plan.model_dump_json(),))
        value = row(observed_latency(connection, OBSERVED), "delivery")
    assert value.reason == "delivery_owner_mismatch" and value.seconds is None


@pytest.mark.parametrize("corrupt", [None, "bill", "change"])
def test_shared_child_uses_its_explicit_member_snapshot(
    repository: SqliteBillRepository,
    corrupt: str | None,
) -> None:
    first, second = owner(repository), owner(repository, "3040")
    publications = []
    snapshots = []
    for bill in (first, second):
        change = StatusChange(
            term=10,
            number=bill.number,
            old_fingerprint=None,
            new_fingerprint="group",
            new_stages=[],
            detected_at=NOW,
        )
        change_id = repository.add_status_change(change)
        assert change_id is not None, "Each owner has a new event fingerprint"
        identity = publication(repository, bill, change_id=change_id)
        publications.append(identity)
        snapshots.append(
            UpdateSnapshot(
                publication_id=identity,
                bill_json=bill.model_dump_json(),
                change_json=change.model_copy(update={"id": change_id}).model_dump_json(),
            )
        )
    if corrupt is not None:
        snapshots[1] = snapshots[1].model_copy(
            update={
                "bill_json" if corrupt == "bill" else "change_json": snapshots[0].bill_json
                if corrupt == "bill"
                else snapshots[0].change_json,
            }
        )
    plan = DeliveryPlan(bill_json=first.model_dump_json(), update_group=tuple(snapshots))
    with closing(copy(repository)) as connection:
        connection.execute("UPDATE publications SET delivery_json=?", (plan.model_dump_json(),))
        report = observed_latency(connection, OBSERVED)
    for identity in publications:
        value = row(report, "delivery", f"publications/{identity}")
        if corrupt is None:
            assert value.delivery_class == "recorded_shared_membership"
            assert value.seconds == 7200 and value.reason is None
        else:
            assert value.seconds is None and value.reason == "invalid_delivery_member_snapshot"


def test_legacy_null_destination_is_not_a_bound_confirmation(
    repository: SqliteBillRepository,
) -> None:
    publication(repository, owner(repository))
    with closing(copy(repository)) as connection:
        connection.execute("CREATE TABLE legacy_publications AS SELECT * FROM publications")
        connection.execute("DROP TABLE publications")
        connection.execute("ALTER TABLE legacy_publications RENAME TO publications")
        connection.execute("UPDATE publications SET channel_id=NULL")
        value = row(observed_latency(connection, OBSERVED), "delivery")
    assert value.delivery_class == "destination_unbound"
    assert value.reason == "destination_unbound" and value.seconds is None


def test_base_owner_denominators_survive_unavailable_newer_families() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute("CREATE TABLE bills(term INTEGER, number TEXT, first_seen_at TEXT)")
        connection.execute("INSERT INTO bills VALUES(10,'3039',?)", (NOW.isoformat(),))
        report = observed_latency(connection, OBSERVED)
    assert len(report.rows) == 4
    assert row(report, "read_completed").reason == "read_completion_unavailable"
    assert row(report, "source_discovery").reason == "source_schema_unavailable"
    assert row(report, "analysis_collected").reason == "result_schema_unavailable"
    assert row(report, "analysis_applied").reason == "application_schema_unavailable"
    assert "bills.wykaz_json" in report.missing_schema


@pytest.mark.parametrize("column", ["wykaz_json", "analysis_generation"])
def test_partial_feature_schema_does_not_drop_other_known_owner_clocks(
    repository: SqliteBillRepository,
    column: str,
) -> None:
    analyzed(repository)
    with closing(copy(repository)) as connection:
        connection.execute(f'ALTER TABLE bills DROP COLUMN "{column}"')
        report = observed_latency(connection, OBSERVED)
    assert row(report, "read_completed").number == "3039"
    value = row(report, "analysis_collected")
    if column == "wykaz_json":
        assert value.seconds == 3600
        assert row(report, "source_discovery").reason == "source_schema_unavailable"
    else:
        assert value.reason == "generation_schema_unavailable" and value.seconds is None
    assert "bills." + column in report.missing_schema


def test_known_empty_and_unavailable_result_application_families_are_distinct(
    repository: SqliteBillRepository,
) -> None:
    owner(repository)
    with closing(copy(repository)) as connection:
        known = observed_latency(connection, OBSERVED)
        assert row(known, "analysis_collected").reason == "missing_result"
        assert row(known, "analysis_applied").reason == "missing_application"
        connection.execute("DROP TABLE result_applications")
        connection.execute("DROP TABLE computation_results")
        unavailable = observed_latency(connection, OBSERVED)
    assert row(unavailable, "analysis_collected").reason == "result_schema_unavailable"
    assert row(unavailable, "analysis_applied").reason == "application_schema_unavailable"


def test_opt_in_public_audit_keeps_default_output_and_database_bytes(
    tmp_path: Path,
    repository: SqliteBillRepository,
) -> None:
    analyzed(repository)
    path = tmp_path / "state.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(repository.dump())
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    ordinary = audit(path)
    measured = audit(path, observed_at=OBSERVED)
    assert "latencies" not in ordinary and "work_ages" not in ordinary
    assert {
        key: value for key, value in measured.items() if key not in {"latencies", "work_ages"}
    } == ordinary
    assert ObservedLatencyReport.model_validate(measured["latencies"]).version == 1
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert json.dumps(ordinary) == json.dumps(audit(path))
