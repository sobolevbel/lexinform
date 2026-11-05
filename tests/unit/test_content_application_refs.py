import datetime as dt
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Never

import pytest
from tools.content_history_doctor import audit

from lexinform.adapters.content_applications import find_projection_application_ids
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import (
    AmendmentsRecord,
    CommandOutcome,
    ContentHistoryPage,
    ContentSnapshot,
    ContentVersion,
    DeliveryPlan,
    IncomingCommand,
    OutcomeStatus,
    ProcessDetail,
    Publication,
    PublicationKind,
    PublicationStatus,
    ResultApplication,
    ResultApplicationTarget,
    Stage,
    StatusChange,
    SupplementRecord,
)
from lexinform.models.content import ContentVersionSummary
from lexinform.services.content_inspection import inspect_content_page
from tests.computations import NOW, retained_result
from tests.unit.test_content_history import record, version


def test_legacy_application_values_remain_opaque(tmp_path: Path) -> None:
    with closing(SqliteBillRepository(tmp_path / "refs.db")) as repo:
        repo.migrate()
        legacy = version().model_copy(update={"application_ids": (987654,)})
        record(repo, legacy)
        assert repo.get_content_version(legacy.content_id) == legacy
        assert "application_ref_profile" not in legacy.model_dump()
        assert ContentVersionSummary.from_version(legacy).known_application_refs == ()


def test_lookup_returns_actual_persisted_projection_id(tmp_path: Path) -> None:
    with closing(SqliteBillRepository(tmp_path / "refs.db")) as repo:
        repo.migrate()
        result = retained_result("amendments")
        repo.save_computation_result(result)
        target = ResultApplicationTarget(kind="status_change", ref="27")
        repo.save_result_application(
            ResultApplication(
                result_id=result.result_id,
                term=10,
                number="foreign",
                generation=None,
                target=target,
                phase="projection",
                outcome="applied",
                reason="retained_result",
                created_at=NOW,
                observed_generation=None,
                observed_status=None,
            )
        )
        with closing(sqlite3.connect(tmp_path / "refs.db")) as db:
            expected = db.execute("SELECT id FROM result_applications").fetchone()[0]
            assert find_projection_application_ids(db, result.result_id, target) == (expected,)
        assert repo.find_projection_application_ids(result.result_id, target) == (expected,)


@pytest.mark.parametrize("ids", [(), (2, 1), (1, 1), (2**63,), tuple(range(1, 34))])
def test_profile_requires_bounded_canonical_ids(ids: tuple[int, ...]) -> None:
    payload = version().model_dump()
    payload.update(application_ref_profile="amendment-projections-v1", application_ids=ids)
    with pytest.raises(ValueError):
        ContentVersion.model_validate(payload)


def modern_history(
    repo: SqliteBillRepository,
    *,
    previous: ContentVersion | None = None,
    supplement: bool = False,
    different_display: bool = False,
) -> tuple[ContentVersion, int, int]:
    result = retained_result("amendments")
    repo.save_computation_result(result)
    assert isinstance(result.record, AmendmentsRecord), "the fixture requests amendments"
    extra = retained_result("supplement") if supplement else None
    if extra is not None:
        repo.save_computation_result(extra)
        assert isinstance(extra.record, SupplementRecord), "the fixture requests supplement"
    projected = (
        result.record.model_copy(update={"model": "different-display"})
        if different_display
        else result.record
    )
    change = StatusChange(
        id=27,
        term=10,
        number="foreign-original-input",
        old_fingerprint=None,
        new_fingerprint="frozen",
        new_stages=[
            Stage(
                stage_name="report",
                stage_type="CommitteeReport",
                report_file="3039-A.pdf",
                proposal="przyjąć poprawki",
            )
        ],
        detected_at=NOW,
        amendments=projected,
        supplements=[extra.record]
        if extra is not None and isinstance(extra.record, SupplementRecord)
        else [],
    )
    change_id = repo.add_status_change(change)
    if change_id is None:
        existing = repo.list_status_changes(10, "foreign-original-input")
        change_id = existing[0].id
    assert change_id is not None, "the fixture retains its unique change"
    change = change.model_copy(update={"id": change_id})
    plan = DeliveryPlan(
        bill_json='{"historical":"bill"}',
        change_json=change.model_dump_json(),
        rendered_text="new" if previous else "original",
        today=NOW.date(),
        revision_of=previous.root_publication_id if previous else None,
        edit_message_id=10 if previous else None,
    )
    physical = repo.create_publication(
        Publication(
            term=12,
            number="relocated",
            channel_id="chan",
            created_at=NOW,
            kind=PublicationKind.AMENDMENTS_REVISION if previous else PublicationKind.STATUS_UPDATE,
            ref="revision" if previous else None,
            status_change_id=None if previous else change_id,
            status=PublicationStatus.SENT,
            sent_at=NOW,
            message_id=10,
            delivery=plan,
        )
    )
    root = previous.root_publication_id if previous else physical
    target = ResultApplicationTarget(
        kind="publication" if previous else "status_change",
        ref=str(physical if previous else change_id),
    )
    repo.save_result_application(
        ResultApplication(
            result_id=result.result_id,
            term=11,
            number="application-owner",
            generation=None,
            target=target,
            phase="projection",
            outcome="applied",
            reason="memo_reuse",
            created_at=NOW,
            observed_generation=None,
            observed_status=None,
        )
    )
    if extra is not None:
        repo.save_result_application(
            ResultApplication(
                result_id=extra.result_id,
                term=77,
                number="supplement-owner",
                generation=None,
                target=target,
                phase="projection",
                outcome="applied",
                reason="retained_result",
                created_at=NOW,
                observed_generation=None,
                observed_status=None,
            )
        )
    ids = tuple(
        sorted(
            (
                *repo.find_projection_application_ids(result.result_id, target),
                *(repo.find_projection_application_ids(extra.result_id, target) if extra else ()),
            )
        )
    )
    bill = ContentSnapshot(kind="bill", payload_json=plan.bill_json)
    event = ContentSnapshot(kind="change", payload_json=plan.change_json)
    semantic = ContentSnapshot(
        kind="plan",
        payload_json=plan.model_copy(
            update={"bill_json": "", "change_json": "", "rendered_text": ""}
        ).model_dump_json(),
    )
    for snapshot in (bill, event, semantic):
        repo.save_content_snapshot(snapshot)
    value = ContentVersion(
        root_publication_id=root,
        previous_version_id=previous.content_id if previous else None,
        reason="amendments_revision" if previous else "original",
        language="ru",
        bill_snapshot_id=bill.snapshot_id,
        change_snapshot_id=event.snapshot_id,
        plan_snapshot_id=semantic.snapshot_id,
        rendered_text=plan.rendered_text,
        application_ref_profile="amendment-projections-v1",
        application_ids=ids,
        computation_ids=tuple(sorted((result.result_id, *((extra.result_id,) if extra else ())))),
        status_change_id=change_id,
        recorded_at=NOW,
    )
    repo.save_content_version(value)
    repo.bind_publication_content(physical, value.content_id)
    return value, physical, ids[0]


def test_original_revision_duplicate_restore_and_bounded_display(tmp_path: Path) -> None:
    path = tmp_path / "refs.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        original, _, _ = modern_history(repo)
        revision, physical, app = modern_history(repo, previous=original)
        repo.save_content_version(revision)
        repo.bind_publication_content(physical, revision.content_id)
        assert repo.get_publication_content(physical) == revision
        assert ContentVersionSummary.from_version(revision).known_application_refs == (
            f"result-application-v1:{app}",
        )
        dump = repo.dump()
        repo.restore(dump)
        repo.restore(dump)
        assert repo.dump() == dump
        assert repo.get_content_version(original.content_id) == original
    assert audit(path)["status"] == "recorded"


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE result_applications SET number='wrong-index'",
        "UPDATE result_applications SET application_json='{}'",
        pytest.param(
            "UPDATE computation_results SET result_json='[" + "[" * 2000 + "]" * 2000 + "]'",
            id="deep-json",
        ),
        "UPDATE result_applications SET application_json=json_set(application_json,'$.target.ref','999')",
        "UPDATE computation_results SET custom_id='wrong-index'",
        "UPDATE computation_results SET result_json=json_set(result_json,'$.request_sha256','bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb')",
        "DELETE FROM result_applications",
    ],
)
def test_corrupt_known_reference_rejected_get_doctor_atomic_restore(
    tmp_path: Path, mutation: str
) -> None:
    path = tmp_path / "refs.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        value, _, _ = modern_history(repo)
        before = repo.dump()
        with closing(sqlite3.connect(path)) as db:
            db.execute("PRAGMA ignore_check_constraints=ON")
            db.execute(mutation)
            db.commit()
        with pytest.raises(ValueError, match="content_application_reference_invalid"):
            repo.get_content_version(value.content_id)
        broken = repo.dump()
        repo.restore(before)
        with pytest.raises((ValueError, sqlite3.Error)):
            repo.restore(broken)
        assert repo.dump() == before
    assert any(
        finding["code"] == "content_application_reference_invalid"
        for finding in audit_broken(tmp_path, broken)["findings"]
    )


def audit_broken(tmp_path: Path, dump: str) -> dict[str, Any]:
    path = tmp_path / "broken.db"
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA ignore_check_constraints=ON")
        db.executescript(dump)
    return audit(path)


def test_retained_deleted_target_remains_valid_but_new_admission_requires_live(
    tmp_path: Path,
) -> None:
    path = tmp_path / "refs.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        original, _, _ = modern_history(repo)
        revision, _, _ = modern_history(repo, previous=original)
        repo.delete_publication(12, "relocated", PublicationKind.AMENDMENTS_REVISION, "chan")
        assert repo.get_content_version(revision.content_id) == revision
        dump = repo.dump()
        repo.restore(dump)
        assert repo.get_content_version(revision.content_id) == revision
        with pytest.raises(ValueError):
            repo.save_content_version(revision.model_copy(update={"rendered_text": "different"}))
    report = audit(path)
    assert report["status"] == "recorded"
    assert not any(f["code"] == "content_application_reference_invalid" for f in report["findings"])


def test_operational_cleanup_keeps_known_reference_valid(tmp_path: Path) -> None:
    with closing(SqliteBillRepository(tmp_path / "refs.db")) as repo:
        repo.migrate()
        value, _, _ = modern_history(repo)
        repo.prune_history(before=NOW + dt.timedelta(days=100), memo_before=NOW)
        assert repo.get_content_version(value.content_id) == value
        dump = repo.dump()
        repo.restore(dump)
        assert repo.dump() == dump


@pytest.mark.parametrize("ids", [(True,), ("1",), (1.0,)])
def test_profile_rejects_coerced_ids(ids: tuple[object, ...]) -> None:
    payload = version().model_dump()
    payload.update(application_ref_profile="amendment-projections-v1", application_ids=ids)
    with pytest.raises(ValueError):
        ContentVersion.model_validate(payload)


def test_lookup_validates_real_sql_columns_and_duplicate_json(tmp_path: Path) -> None:
    path = tmp_path / "refs.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        value, _, app_id = modern_history(repo)
        target = ResultApplicationTarget(kind="status_change", ref=str(value.status_change_id))
        with closing(sqlite3.connect(path)) as db:
            raw = db.execute(
                "SELECT application_json FROM result_applications WHERE id=?", (app_id,)
            ).fetchone()[0]
            db.execute(
                "UPDATE result_applications SET application_json=? WHERE id=?",
                ('{"term":11,' + raw[1:], app_id),
            )
            db.commit()
        with pytest.raises(ValueError):
            repo.find_projection_application_ids(value.computation_ids[0], target)
        with pytest.raises(ValueError):
            repo.get_content_version(value.content_id)


def test_future_target_binding_cannot_borrow_another_revision_receipt(tmp_path: Path) -> None:
    with closing(SqliteBillRepository(tmp_path / "refs.db")) as repo:
        repo.migrate()
        original, _, _ = modern_history(repo)
        revision, physical, _ = modern_history(repo, previous=original)
        saved = repo.publication_by_id(physical)
        assert saved is not None, "the revision exists"
        other = repo.create_publication(saved.model_copy(update={"id": None, "ref": "other"}))
        before = repo.dump()
        with pytest.raises(ValueError):
            repo.bind_publication_content(other, revision.content_id)
        assert repo.dump() == before


def test_page_displays_only_profiled_ids_without_body_or_current_bill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with closing(SqliteBillRepository(tmp_path / "refs.db")) as repo:
        repo.migrate()
        value, physical, app_id = modern_history(repo)
        selected = repo.publication_by_id(physical)
        assert selected is not None, "the physical target exists"
        before = repo.dump()

        def no_bill(*args: object, **kwargs: object) -> Never:
            raise AssertionError("history has only frozen context")

        monkeypatch.setattr(repo, "get", no_bill)
        page = inspect_content_page(repo, selected, page=1)
        assert page.status == "available" and page.version is not None
        text = render_page(page)
        assert f"result-application-v1:{app_id}" in text
        assert "foreign-original-input" not in text and "historical" not in text
        assert len(text) < 3000
        assert repo.dump() == before
        opaque = ContentVersionSummary.from_version(
            value.model_copy(update={"application_ref_profile": None, "application_ids": (987654,)})
        )
        text = render_page(page.model_copy(update={"version": opaque}))
        assert "opaque legacy references" in text and "987654" not in text


def test_paginated_refs_are_bounded_even_with_32_known_applications() -> None:
    legacy = version().model_copy(
        update={
            "application_ids": tuple(range(1, 33)),
            "application_ref_profile": "amendment-projections-v1",
        }
    )
    summary = ContentVersionSummary.from_version(legacy)
    page = ContentHistoryPage(status="available", page=1, pages=1, version=summary)
    text = render_page(page)
    assert "result-application-v1:1, result-application-v1:2" in text
    assert "additional/opaque refs: 30" in text
    assert "result-application-v1:3" not in text


def render_page(page: ContentHistoryPage) -> str:
    return (
        MessageFormatter()
        .command_reply(
            IncomingCommand(
                update_id=1,
                message_id=1,
                chat_id="ops",
                user_id=1,
                text="/delivery 1 history",
                received_at=NOW,
            ),
            CommandOutcome(status=OutcomeStatus.DELIVERY, content_history_page=page),
        )
        .text
    )


def test_actual_government_rehome_preserves_frozen_refs(
    tmp_path: Path, process_3039: ProcessDetail
) -> None:
    path = tmp_path / "refs.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        value, physical, app_id = modern_history(repo)
        with closing(sqlite3.connect(path)) as db:
            db.execute("UPDATE publications SET number='RCL/123' WHERE id=?", (physical,))
            db.commit()
        repo.upsert_summary(
            process_3039.model_copy(update={"term": 12, "number": "RCL/123"}), now=NOW
        )
        assert repo.move_government_rows(12, 13) == 1
        moved = repo.publication_by_id(physical)
        assert moved is not None and moved.term == 13
        assert repo.get_content_version(value.content_id) == value
        assert repo.find_projection_application_ids(
            value.computation_ids[0],
            ResultApplicationTarget(kind="status_change", ref=str(value.status_change_id)),
        ) == (app_id,)
        dump = repo.dump()
        repo.restore(dump)
        assert repo.dump() == dump


def test_modern_frozen_result_id_must_name_the_record_not_just_any_result(tmp_path: Path) -> None:
    with closing(SqliteBillRepository(tmp_path / "refs.db")) as repo:
        repo.migrate()
        value, _, _ = modern_history(repo)
        wrong = retained_result("supplement")
        repo.save_computation_result(wrong)
        target = ResultApplicationTarget(kind="status_change", ref=str(value.status_change_id))
        repo.save_result_application(
            ResultApplication(
                result_id=wrong.result_id,
                term=99,
                number="unrelated",
                generation=None,
                target=target,
                phase="projection",
                outcome="applied",
                reason="retained_result",
                created_at=NOW,
                observed_generation=None,
                observed_status=None,
            )
        )
        ids = repo.find_projection_application_ids(wrong.result_id, target)
        before = repo.dump()
        with pytest.raises(ValueError):
            repo.save_content_version(
                value.model_copy(
                    update={
                        "application_ids": ids,
                        "computation_ids": (*value.computation_ids, wrong.result_id),
                    }
                )
            )
        assert repo.dump() == before


def test_original_supports_known_supplement_and_display_difference_without_answer_equality(
    tmp_path: Path,
) -> None:
    with closing(SqliteBillRepository(tmp_path / "refs.db")) as repo:
        repo.migrate()
        value, _, _ = modern_history(repo, supplement=True, different_display=True)
        assert len(value.application_ids) == 2
        assert repo.get_content_version(value.content_id) == value
        dump = repo.dump()
        repo.restore(dump)
        assert repo.dump() == dump
