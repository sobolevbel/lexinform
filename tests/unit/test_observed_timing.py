import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from tools.observed_timing import interval, observed_timing
from tools.work_doctor import audit, main

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    AmendmentsRecord,
    AnalysisRecord,
    Bill,
    DeliveryPlan,
    DocumentReviewRecord,
    Editorial,
    JointRecord,
    Mnemonic,
    NameRecord,
    Publication,
    PublicationKind,
    PublicationStatus,
    ResultApplication,
    ResultApplicationTarget,
    StatusChange,
    SupplementRecord,
)
from lexinform.models.batch import BatchKind
from lexinform.models.commands import DeliveryResolution, IncomingCommand
from lexinform.models.observations import UpdateSnapshot
from lexinform.services.content_history import FORMATTER_VERSION, record_publication_content
from lexinform.services.result_applications import record_secondary_application
from tests.computations import KINDS
from tests.fakes import FixedClock
from tests.unit.test_local_timing_reader import NOW, timed_result
from tests.unit.test_observed_latency import owner

OBSERVED = NOW + timedelta(hours=3)


@pytest.mark.parametrize("kind", KINDS)
def test_every_purpose_has_independent_result_clock_coverage(kind: BatchKind) -> None:
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        repo.save_computation_result(timed_result(kind))
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.executescript(repo.dump())
            report = observed_timing(connection, OBSERVED).model_dump()
    assert report["populations"]["results"]["rows"] == 1
    assert report["results"][0]["purpose"] == kind
    assert report["results"][0]["trigger_to_collection"]["seconds"] == 4200
    read = report["results"][0]["read_to_collection"]
    assert read["seconds"] == (None if kind in ("name", "joint") else 4800)
    assert len(report["purposes"]) == 10
    assert report["results"][0]["source_availability"] == "unknown"


def test_empty_available_is_different_from_unavailable_schema() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        report = observed_timing(connection, OBSERVED).model_dump()
    assert report["populations"]["results"]["rows"] is None
    assert report["populations"]["applications"]["rows"] is None


def test_corrupt_sql_identity_stays_in_unattributed_denominator() -> None:
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        repo.save_computation_result(timed_result("analysis"))
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.executescript(repo.dump())
            connection.execute(
                "UPDATE computation_results SET number='https://private.invalid/body'"
            )
            report = observed_timing(connection, OBSERVED).model_dump()
    assert report["populations"]["results"] == {"rows": 1, "attributed": 0, "unattributed": 1}
    assert report["results"][0]["reason"] == "computation_columns_mismatch"
    assert "private.invalid" not in str(report)


def application_for(kind: BatchKind, number: str = "3039") -> ResultApplication:
    target = None
    if kind == "explanation":
        target = ResultApplicationTarget(kind="command", ref="101")
    elif kind in ("amendments", "supplement"):
        target = ResultApplicationTarget(kind="status_change", ref="1")
    elif kind == "joint":
        target = ResultApplicationTarget(kind="bill", ref="joint")
    elif kind == "name":
        target = ResultApplicationTarget(kind="editorial", ref="mnemonic")
    elif kind in ("consultation_review", "commission_review", "act_application"):
        target = ResultApplicationTarget(kind="document_review", ref=f"{kind}/source")
    return ResultApplication(
        result_id=timed_result(kind).result_id,
        term=10,
        number=number,
        generation=77 if target is None else None,
        projected_generation=5 if kind in ("analysis", "reanalysis") else None,
        target=target,
        phase="projection",
        outcome="applied",
        reason="retained_result",
        observed_generation=None,
        observed_status=None,
        created_at=NOW + timedelta(hours=2),
    )


def frozen_for(kind: BatchKind, bill: Bill) -> tuple[Bill, StatusChange | None, PublicationKind]:
    record = timed_result(kind).record
    change = None
    publication_kind = PublicationKind.NEW_BILL
    if isinstance(record, AnalysisRecord):
        bill = bill.model_copy(update={"analysis": record, "analysis_generation": 5})
        if kind == "reanalysis":
            change = StatusChange(
                id=7,
                term=10,
                number=bill.number,
                old_fingerprint="old",
                new_fingerprint="new",
                detected_at=NOW,
                new_stages=[],
                content_changed=True,
            )
            publication_kind = PublicationKind.STATUS_UPDATE
    elif isinstance(record, AmendmentsRecord | SupplementRecord):
        change = StatusChange(
            id=7,
            term=10,
            number=bill.number,
            old_fingerprint="old",
            new_fingerprint="new",
            detected_at=NOW,
            new_stages=[],
            amendments=record if isinstance(record, AmendmentsRecord) else None,
            supplements=(record,) if isinstance(record, SupplementRecord) else (),
        )
        publication_kind = PublicationKind.STATUS_UPDATE
    elif isinstance(record, JointRecord):
        bill = bill.model_copy(update={"joint": record})
        publication_kind = PublicationKind.JOINT_BILL
    elif isinstance(record, NameRecord):
        bill = bill.model_copy(
            update={
                "editorial": Editorial(
                    mnemonic=Mnemonic(
                        name=record.proposal.name,
                        language="ru",
                        basis=record.proposal.basis,
                        selected_at=NOW,
                        origin="backfill",
                        computation_id=record.computation_id,
                        request_sha256=record.request_sha256,
                    )
                )
            }
        )
    elif isinstance(record, DocumentReviewRecord):
        bill = bill.model_copy(
            update={
                f"{kind}_review" if kind == "act_application" else kind: record.model_copy(
                    update={"source_key": "source"}
                )
            }
        )
        publication_kind = PublicationKind(kind)
    return bill, change, publication_kind


def arrange(kind: BatchKind, *, number: str = "3039") -> SqliteBillRepository:
    repo = SqliteBillRepository(":memory:")
    repo.migrate()
    bill = owner(repo, number)
    result = timed_result(kind)
    repo.save_computation_result(result)
    if kind == "explanation":
        command = IncomingCommand(
            update_id=101, chat_id="ops", message_id=9, text=f"/explain {number}", received_at=NOW
        )
        assert repo.record_command(command), (
            "the public fixture records its new operator command once"
        )
        with repo.atomic():
            repo.mark_command_executed(101, outcome="shown", at=NOW + timedelta(hours=2))
            record_secondary_application(
                repo,
                10,
                number,
                result.result_id,
                target=ResultApplicationTarget(kind="command", ref="101"),
                now=NOW + timedelta(hours=2),
            )
        return repo
    repo.save_result_application(application_for(kind, number))
    bill, change, pub_kind = frozen_for(kind, bill)
    if change:
        actual = repo.add_status_change(change)
        assert actual is not None, "the public repository persists the technical changed event"
        change = change.model_copy(update={"id": actual})
        assert actual == 1, "the empty fixture allocates its first event"
    plan = DeliveryPlan(
        bill_json=bill.model_dump_json(),
        change_json=change.model_dump_json() if change else "",
        rendered_text="PRIVATE BODY",
    )
    repo.create_publication(
        Publication(
            term=10,
            number=number,
            kind=pub_kind,
            status=PublicationStatus.SENT,
            channel_id="private channel",
            message_id=42,
            status_change_id=change.id if change else None,
            created_at=NOW,
            sent_at=NOW + timedelta(hours=2, minutes=10),
            delivery=plan,
        )
    )
    if kind == "name":
        physical = repo.publication_by_id(1)
        assert physical is not None, "the public writer saved the technical original card"
        queued = physical.model_copy(update={"status": PublicationStatus.QUEUED})
        version = record_publication_content(
            repo,
            FixedClock(NOW),
            queued,
            formatter_version=FORMATTER_VERSION,
            language="ru",
            card_history_parent="original",
        )
        assert version is not None, "the technical named-card fixture has frozen formatter evidence"
    return repo


def read(repo: SqliteBillRepository) -> dict[str, Any]:
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.executescript(repo.dump())
        before = connection.serialize()
        connection.execute("PRAGMA query_only=ON")
        report = observed_timing(connection, OBSERVED).model_dump()
        assert connection.serialize() == before
        return report


@pytest.mark.parametrize("kind", KINDS)
def test_all_purpose_physical_inventory_uses_only_actual_render_slot(kind: BatchKind) -> None:
    with closing(arrange(kind)) as repo, closing(sqlite3.connect(":memory:")) as connection:
        connection.executescript(repo.dump())
        report = observed_timing(connection, OBSERVED).model_dump()
    edges = report["physical_edges"]
    if kind == "explanation":
        assert not edges
        assert report["populations"]["applications"]["rows"] == 1
        assert report["applications"][0]["phase"] == "projection"
        assert report["applications"][0]["target_kind"] == "command"
        assert report["applications"][0]["physical"]["status"] == "unknown"
        assert (
            report["applications"][0]["physical"]["reason"] == "no_durable_operator_reply_receipt"
        )
    else:
        assert len(edges) == 1
        assert edges[0]["status"] == "recorded_operation"
        assert edges[0]["application_to_operation"]["seconds"] == 600
    assert "PRIVATE BODY" not in str(report)
    assert "private channel" not in str(report)


def test_paid_owner_and_generation_are_not_current_target_generation() -> None:
    with closing(arrange("analysis", number="9999")) as repo:
        report = read(repo)
    assert report["physical_edges"][0]["status"] == "recorded_operation"
    assert report["results"][0]["retained_owner"] != report["applications"][0]["owner"]
    assert (
        report["results"][0]["input_scope"]["number_sha256"]
        == report["results"][0]["retained_owner"]["number_sha256"]
    )


@pytest.mark.parametrize("corruption", ["columns", "duplicate", "deep"])
def test_application_corruption_keeps_all_row_denominator_without_attribution(
    corruption: str,
) -> None:
    with closing(arrange("analysis")) as repo, closing(sqlite3.connect(":memory:")) as connection:
        connection.executescript(repo.dump())
        if corruption == "columns":
            connection.execute("UPDATE result_applications SET number='different'")
        else:
            raw = "{}" if corruption == "duplicate" else "[" * 2000 + "0" + "]" * 2000
            if corruption == "duplicate":
                raw = '{"result_id":"a","result_id":"b"}'
            connection.execute("PRAGMA ignore_check_constraints=ON")
            connection.execute("DROP INDEX ux_result_application_target")
            connection.execute("UPDATE result_applications SET application_json=?", (raw,))
        report = observed_timing(connection, OBSERVED).model_dump()
    assert report["populations"]["applications"] == {"rows": 1, "attributed": 0, "unattributed": 1}
    assert not report["physical_edges"]


def test_saved_clock_intervals_handle_fold_extremes_and_read_before_trigger() -> None:
    warsaw = ZoneInfo("Europe/Warsaw")
    start = datetime(2026, 10, 25, 2, 30, tzinfo=warsaw, fold=0)
    end = datetime(2026, 10, 25, 2, 30, tzinfo=warsaw, fold=1)
    assert interval(start, end, end)["seconds"] == 3600
    extreme = datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=14)))
    assert interval(extreme, extreme, extreme)["seconds"] == 0
    assert interval(NOW, NOW - timedelta(seconds=1), OBSERVED)["reason"] == "end_before_start"


def test_opt_in_requires_clock_and_default_is_v1(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
    with pytest.raises(ValueError, match="explicit observed"):
        audit(path, include_timing_witnesses=True)
    original = audit(path, observed_at=OBSERVED)
    assert original["latencies"]["version"] == 1
    assert audit(path, observed_at=OBSERVED, include_timing_witnesses=False) == original
    assert (
        audit(path, observed_at=OBSERVED, include_timing_witnesses=True)["latencies"]["version"]
        == 2
    )


@pytest.mark.parametrize(
    "fault",
    [
        "unknown",
        "manual",
        "missing_resolution_table",
        "bad_payload",
        "missing_payload",
        "wrong_generation",
        "wrong_render_kind",
        "source_dates_only",
        "future_send",
        "unbound_alias",
    ],
)
def test_missing_or_ambiguous_physical_evidence_never_creates_known_latency(fault: str) -> None:
    with closing(arrange("analysis")) as repo, closing(sqlite3.connect(":memory:")) as connection:
        connection.executescript(repo.dump())
        if fault == "unknown":
            connection.execute("UPDATE publications SET status='unknown'")
        elif fault == "manual":
            resolution = DeliveryResolution(
                publication_id=1,
                term=10,
                number="3039",
                kind=PublicationKind.NEW_BILL,
                channel_id="private channel",
                action="confirm",
                message_id=42,
                command=IncomingCommand(
                    update_id=1,
                    chat_id="ops",
                    message_id=2,
                    text="PRIVATE COMMAND",
                    received_at=NOW,
                ),
                resolved_at=NOW,
            )
            connection.execute(
                (
                    "INSERT INTO "
                    "delivery_resolutions(publication_id,command_update_id,resolution_json) "
                    "VALUES(1,1,?)"
                ),
                (resolution.model_dump_json(),),
            )
        elif fault == "missing_resolution_table":
            connection.execute("DROP TABLE delivery_resolutions")
        elif fault in ("bad_payload", "missing_payload"):
            connection.execute(
                "UPDATE publications SET delivery_json=?",
                ('{"bill_json":"bad"}' if fault == "bad_payload" else None,),
            )
        elif fault == "wrong_generation":
            plan = DeliveryPlan.model_validate_json(
                connection.execute("SELECT delivery_json FROM publications").fetchone()[0]
            )
            bill = Bill.model_validate_json(plan.bill_json).model_copy(
                update={"analysis_generation": 77}
            )
            connection.execute(
                "UPDATE publications SET delivery_json=?",
                (plan.model_copy(update={"bill_json": bill.model_dump_json()}).model_dump_json(),),
            )
        elif fault == "wrong_render_kind":
            connection.execute("UPDATE publications SET kind='agenda'")
        elif fault == "source_dates_only":
            connection.execute("DELETE FROM result_applications")
        elif fault == "future_send":
            connection.execute(
                "UPDATE publications SET sent_at=?", ((OBSERVED + timedelta(days=1)).isoformat(),)
            )
        else:
            connection.execute(
                "INSERT INTO "
                "publications(id,term,number,kind,status,channel_id,message_id,created_at,sent_at"
                ",ref) SELECT "
                "2,term,'another','agenda',status,channel_id,message_id,created_at,sent_at,'alias"
                "' FROM publications WHERE id=1"
            )
        report = observed_timing(connection, OBSERVED).model_dump()
    assert all(
        edge["application_to_operation"]["seconds"] is None for edge in report["physical_edges"]
    )
    assert all(edge["status"] != "recorded_operation" for edge in report["physical_edges"])
    assert "PRIVATE COMMAND" not in str(report)


def test_multipart_completion_requires_every_frozen_part_and_its_chronology() -> None:
    with closing(arrange("analysis")) as repo, closing(sqlite3.connect(":memory:")) as connection:
        connection.executescript(repo.dump())
        plan = DeliveryPlan.model_validate_json(
            connection.execute("SELECT delivery_json FROM publications").fetchone()[0]
        )
        child = DeliveryPlan(
            bill_json=plan.bill_json,
            rendered_text="PRIVATE CHILD",
            continuation_root=1,
            continuation_parent=1,
            continuation_index=2,
            reply_to=42,
        )
        connection.execute(
            "UPDATE publications SET delivery_json=?",
            (plan.model_copy(update={"continuation_ids": (2,)}).model_dump_json(),),
        )
        connection.execute(
            (
                "INSERT INTO "
                "publications(id,term,number,kind,status,channel_id,message_id,created_at,sent_at"
                ",ref,delivery_json) VALUES(2,10,'3039','message_part','sent','private "
                "channel',43,?,?,?,?)"
            ),
            (
                NOW.isoformat(),
                (NOW + timedelta(hours=2, minutes=15)).isoformat(),
                "message/1/2",
                child.model_dump_json(),
            ),
        )
        report = observed_timing(connection, OBSERVED).model_dump()
        assert report["physical_edges"][0]["application_to_operation"]["seconds"] == 900
        assert report["physical_edges"][0]["parts"] == 2
        connection.execute("UPDATE publications SET status='unknown' WHERE id=2")
        report = observed_timing(connection, OBSERVED).model_dump()
        assert report["physical_edges"][0]["application_to_operation"]["seconds"] is None
        connection.execute(
            "UPDATE publications SET status='sent', sent_at=? WHERE id=2",
            ((NOW + timedelta(hours=2)).isoformat(),),
        )
        report = observed_timing(connection, OBSERVED).model_dump()
        assert report["physical_edges"][0]["reason"] == "multipart_clock_order_unproven"


def test_update_group_uses_each_member_frozen_owner_and_shared_receipt() -> None:
    with closing(arrange("reanalysis")) as repo, closing(sqlite3.connect(":memory:")) as connection:
        connection.executescript(repo.dump())
        first = DeliveryPlan.model_validate_json(
            connection.execute("SELECT delivery_json FROM publications").fetchone()[0]
        )
        bill = Bill.model_validate_json(first.bill_json)
        second_bill = bill.model_copy(
            update={"summary": bill.summary.model_copy(update={"number": "3040"})}
        )
        change = StatusChange.model_validate_json(first.change_json)
        second_change = change.model_copy(update={"id": 2, "number": "3040"})
        snapshots = (
            UpdateSnapshot(
                publication_id=1, bill_json=first.bill_json, change_json=first.change_json
            ),
            UpdateSnapshot(
                publication_id=2,
                bill_json=second_bill.model_dump_json(),
                change_json=second_change.model_dump_json(),
            ),
        )
        root = first.model_copy(update={"update_group": snapshots, "group_text": "PRIVATE GROUP"})
        child = root.model_copy(
            update={
                "bill_json": second_bill.model_dump_json(),
                "change_json": second_change.model_dump_json(),
            }
        )
        connection.execute("UPDATE publications SET delivery_json=?", (root.model_dump_json(),))
        connection.execute(
            (
                "INSERT INTO "
                "publications(id,term,number,kind,status,channel_id,message_id,status_change_id,c"
                "reated_at,sent_at,ref,delivery_json) SELECT "
                "2,term,'3040',kind,status,channel_id,message_id,2,created_at,sent_at,'group-chil"
                "d',? FROM publications WHERE id=1"
            ),
            (child.model_dump_json(),),
        )
        application = application_for("reanalysis", "3040")
        connection.execute(
            (
                "INSERT INTO "
                "result_applications(result_id,term,number,generation,phase,application_json) "
                "VALUES(?,?,?,?,?,?)"
            ),
            (
                application.result_id,
                application.term,
                application.number,
                application.generation,
                application.phase,
                application.model_dump_json(),
            ),
        )
        report = observed_timing(connection, OBSERVED).model_dump()
        assert len(report["physical_edges"]) == 2
        assert {edge["physical_publication_id"] for edge in report["physical_edges"]} == {1}
        assert all(edge["status"] == "recorded_operation" for edge in report["physical_edges"])
        connection.execute("UPDATE publications SET status='unknown' WHERE id=2")
        report = observed_timing(connection, OBSERVED).model_dump()
        assert all(edge["status"] == "unknown" for edge in report["physical_edges"])


@pytest.mark.parametrize("topology", ["digest", "absorbed"])
def test_event_operation_requires_both_stored_membership_directions(topology: str) -> None:
    with closing(arrange("supplement")) as repo, closing(sqlite3.connect(":memory:")) as connection:
        connection.executescript(repo.dump())
        source = DeliveryPlan.model_validate_json(
            connection.execute("SELECT delivery_json FROM publications").fetchone()[0]
        )
        parent = DeliveryPlan(
            bill_json=source.bill_json,
            rendered_text="PRIVATE DIGEST",
            digest_event_ids=(1,) if topology == "digest" else (),
            held_change_ids=(1,) if topology == "absorbed" else (),
        )
        child = (
            source.model_copy(update={"destination": "digest_only"})
            if topology == "digest"
            else source.model_copy(update={"absorbed_by": 2})
        )
        connection.execute("UPDATE publications SET delivery_json=?", (child.model_dump_json(),))
        connection.execute(
            (
                "INSERT INTO "
                "publications(id,term,number,kind,status,channel_id,message_id,created_at,sent_at"
                ",ref,delivery_json) SELECT "
                "2,term,number,?,status,channel_id,message_id,created_at,sent_at,'physical-parent"
                "',? FROM publications WHERE id=1"
            ),
            ("digest" if topology == "digest" else "status_update", parent.model_dump_json()),
        )
        if topology == "digest":
            connection.execute("INSERT INTO digest_event_links VALUES(1,2)")
        report = observed_timing(connection, OBSERVED).model_dump()
        assert report["physical_edges"][0]["status"] == "recorded_operation"
        assert report["physical_edges"][0]["physical_publication_id"] == 2
        connection.execute(
            "UPDATE publications SET delivery_json=? WHERE id=2",
            (
                parent.model_copy(
                    update={"digest_event_ids": (), "held_change_ids": ()}
                ).model_dump_json(),
            ),
        )
        report = observed_timing(connection, OBSERVED).model_dump()
        assert report["physical_edges"][0]["status"] == "unknown"
        assert report["physical_edges"][0]["reason"] == "physical_membership_unproven"


def test_name_requires_known_formatter_language_and_survives_payload_cleanup() -> None:
    with closing(arrange("name")) as repo, closing(sqlite3.connect(":memory:")) as connection:
        connection.executescript(repo.dump())
        connection.execute("UPDATE publications SET delivery_json=NULL")
        report = observed_timing(connection, OBSERVED).model_dump()
        assert len(report["physical_edges"]) == 1
        assert report["physical_edges"][0]["status"] == "recorded_operation"
        connection.execute("DELETE FROM publication_content")
        report = observed_timing(connection, OBSERVED).model_dump()
        assert not report["physical_edges"]


def test_legacy_witness_missing_keeps_collection_and_application_coverage() -> None:
    with closing(arrange("analysis")) as repo, closing(sqlite3.connect(":memory:")) as connection:
        connection.executescript(repo.dump())
        result = timed_result("analysis").model_copy(update={"local_timing_witness": None})
        connection.execute(
            "UPDATE computation_results SET result_json=?", (result.model_dump_json(),)
        )
        collection = ResultApplication(
            result_id=result.result_id,
            term=10,
            number="3039",
            generation=0,
            phase="collection",
            outcome="ready",
            reason="current_generation",
            observed_generation=0,
            observed_status=None,
            created_at=NOW + timedelta(hours=1),
        )
        connection.execute(
            (
                "INSERT INTO "
                "result_applications(result_id,term,number,generation,phase,application_json) "
                "VALUES(?,?,?,?,?,?)"
            ),
            (
                collection.result_id,
                collection.term,
                collection.number,
                collection.generation,
                collection.phase,
                collection.model_dump_json(),
            ),
        )
        report = observed_timing(connection, OBSERVED).model_dump()
    assert report["populations"]["applications"]["rows"] == 2
    assert report["results"][0]["witness"] == "unavailable"
    assert report["results"][0]["input_scope"] is not None
    assert report["results"][0]["trigger_to_collection"]["reason"] == "start_missing_timestamp"
    assert report["applications"][1]["physical"]["reason"] == "collection_receipt"
    purpose = report["purposes"][0]
    assert purpose["collection_receipts"] == purpose["projection_receipts"] == 1


def test_opt_in_extreme_observation_avoids_absolute_utc_conversion(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
    extreme = datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=14)))
    report = audit(path, observed_at=extreme, include_timing_witnesses=True)
    assert report["latencies"]["observed_at"] == extreme.isoformat()


def test_cli_flag_is_explicit_and_preserves_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, output = tmp_path / "state.db", tmp_path / "report.json"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
    before = path.read_bytes()
    monkeypatch.setattr(
        "sys.argv",
        [
            "work-doctor",
            "--database",
            str(path),
            "--output",
            str(output),
            "--observed-at",
            OBSERVED.isoformat(),
            "--timing-witnesses",
        ],
    )
    with pytest.raises(SystemExit) as raised:
        main()
    assert raised.value.code == 2
    report = json.loads(output.read_text())
    assert report["latencies"]["version"] == 2
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "membership", ["source_publication", "status_change", "unrelated", "wrong_kind"]
)
def test_digest_membership_uses_publication_namespace_not_change_namespace(membership: str) -> None:
    with closing(arrange("supplement")) as repo, closing(sqlite3.connect(":memory:")) as connection:
        connection.executescript(repo.dump())
        source = DeliveryPlan.model_validate_json(
            connection.execute("SELECT delivery_json FROM publications").fetchone()[0]
        )
        source_id, change_id, owner_id = 31, 1, 32
        connection.execute("UPDATE publications SET id=? WHERE id=1", (source_id,))
        child = source.model_copy(update={"destination": "digest_only"})
        member = (
            source_id
            if membership in ("source_publication", "wrong_kind")
            else change_id
            if membership == "status_change"
            else 999
        )
        parent = DeliveryPlan(
            bill_json="", rendered_text="PRIVATE DIGEST", digest_event_ids=(member,)
        )
        connection.execute(
            "UPDATE publications SET delivery_json=? WHERE id=?",
            (child.model_dump_json(), source_id),
        )
        connection.execute(
            "INSERT INTO publications(id,term,number,kind,status,channel_id,message_id,created_at,sent_at,ref,delivery_json) SELECT ?,term,number,?,status,channel_id,message_id,created_at,sent_at,'namespace-parent',? FROM publications WHERE id=?",
            (
                owner_id,
                "agenda" if membership == "wrong_kind" else "digest",
                parent.model_dump_json(),
                source_id,
            ),
        )
        connection.execute("INSERT INTO digest_event_links VALUES(?,?)", (source_id, owner_id))
        report = observed_timing(connection, OBSERVED).model_dump()
    edge = report["physical_edges"][0]
    assert (edge["status"] == "recorded_operation") == (membership == "source_publication")
    assert edge["application_to_operation"]["seconds"] == (
        600 if membership == "source_publication" else None
    )


@pytest.mark.parametrize("fault", [None, "wrong_kind", "wrong_target", "wrong_change_owner"])
def test_public_amendment_revision_has_no_sql_change_reference(fault: str | None) -> None:
    with closing(arrange("amendments")) as repo:
        original = repo.publication_by_id(1)
        assert original is not None and original.delivery is not None, (
            "the public fixture saved the original amendment operation"
        )
        plan = original.delivery.model_copy(
            update={"revision_of": 1, "edit_message_id": 42, "reply_to": 42}
        )
        revision_id = repo.create_publication(
            Publication(
                term=10,
                number="3039",
                kind=PublicationKind.AMENDMENTS_REVISION,
                status=PublicationStatus.SENT,
                channel_id="private channel",
                message_id=42,
                created_at=NOW + timedelta(hours=2),
                sent_at=NOW + timedelta(hours=2, minutes=20),
                ref="amendments/technical-revision",
                delivery=plan,
            )
        )
        revision = repo.publication_by_id(revision_id)
        assert revision is not None and revision.status_change_id is None, (
            "the runtime public revision constructor carries its change only in frozen payload"
        )
        application = application_for("amendments").model_copy(
            update={"target": ResultApplicationTarget(kind="publication", ref=str(revision_id))}
        )
        repo.save_result_application(application)
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.executescript(repo.dump())
            if fault == "wrong_kind":
                connection.execute(
                    "UPDATE publications SET kind='agenda' WHERE id=?", (revision_id,)
                )
            elif fault == "wrong_target":
                raw = application.model_copy(
                    update={"target": ResultApplicationTarget(kind="publication", ref="999")}
                )
                connection.execute(
                    "UPDATE result_applications SET application_json=? WHERE json_extract(application_json,'$.target.kind')='publication'",
                    (raw.model_dump_json(),),
                )
            elif fault == "wrong_change_owner":
                change = StatusChange.model_validate_json(plan.change_json).model_copy(
                    update={"number": "different"}
                )
                connection.execute(
                    "UPDATE publications SET delivery_json=? WHERE id=?",
                    (
                        plan.model_copy(
                            update={"change_json": change.model_dump_json()}
                        ).model_dump_json(),
                        revision_id,
                    ),
                )
            report = observed_timing(connection, OBSERVED).model_dump()
    revision_edges = [
        edge for edge in report["physical_edges"] if edge["publication_id"] == revision_id
    ]
    assert bool(revision_edges) == (fault is None)
    if revision_edges:
        assert revision_edges[0]["operation"] == "edit"
        assert revision_edges[0]["application_to_operation"]["seconds"] == 1200


@pytest.mark.parametrize("missing", [None, "result_applications", "computation_results"])
def test_dependent_counts_require_all_schemas_even_with_full_publications(
    missing: str | None,
) -> None:
    with (
        closing(SqliteBillRepository(":memory:")) as repo,
        closing(sqlite3.connect(":memory:")) as connection,
    ):
        repo.migrate()
        connection.executescript(repo.dump())
        if missing:
            connection.execute(f"DROP TABLE {missing}")
        report = observed_timing(connection, OBSERVED).model_dump()
    expected = None if missing else 0
    assert report["populations"]["physical_edges"]["rows"] == expected
    for purpose in report["purposes"]:
        for key in (
            "applications",
            "collection_receipts",
            "projection_receipts",
            "physical_edges",
            "projection_rows_with_input_reference",
            "recorded_operation_edges",
        ):
            assert purpose[key] == expected, (missing, key)
    for name, coverage in report["clock_coverage"].items():
        if name.startswith("physical_edges/"):
            assert coverage == {
                "denominator_rows": expected,
                "known_rows": expected,
                "unknown_rows": expected,
            }
    assert report["populations"]["applications"]["rows"] == (
        None if missing == "result_applications" else 0
    )
    assert report["populations"]["results"]["rows"] == (
        None if missing == "computation_results" else 0
    )
    for population, table in (
        ("results", "computation_results"),
        ("applications", "result_applications"),
    ):
        value = None if missing == table else 0
        assert report["populations"][population] == {
            "rows": value,
            "attributed": value,
            "unattributed": value,
        }
