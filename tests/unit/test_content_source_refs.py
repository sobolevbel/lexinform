import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import timedelta
from typing import Any

import pytest
from pydantic import ValidationError
from tools.content_history_doctor import audit

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import (
    AnalysisRecord,
    CommandOutcome,
    ContentHistoryView,
    ContentSnapshot,
    ContentVersion,
    ContentVersionSummary,
    DeliveryPlan,
    IncomingCommand,
    OutcomeStatus,
    Publication,
    PublicationKind,
    PublicationStatus,
    TextDocument,
)
from tests.unit.test_content_sources import Case
from tests.unit.test_content_sources import case as case
from tests.unit.test_input_receipts import NOW, SOURCE


def diagnose(case: Case) -> dict[str, Any]:
    path = case.path.with_name("doctor-snapshot.db")
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(case.repo.dump())
    return audit(path)


def content(case: Case, *, known: bool = True, scoped: bool = True) -> ContentVersion:
    change_id = case.repo.add_status_change(case.change)
    assert change_id is not None, "the isolated fixture records a fresh amendment event"
    change = case.change.model_copy(update={"id": change_id})
    change_snapshot = ContentSnapshot(kind="change", payload_json=change.model_dump_json())
    case.repo.save_content_snapshot(change_snapshot)
    plan = DeliveryPlan(
        bill_json=case.bill.model_dump_json(),
        change_json=change.model_dump_json(),
        rendered_text="fixed",
        today=NOW.date(),
    )
    root_id = case.repo.create_publication(
        Publication(
            term=10,
            number="3039",
            kind=PublicationKind.STATUS_UPDATE,
            status=PublicationStatus.SENT,
            channel_id="@source-test",
            status_change_id=change_id,
            message_id=17,
            created_at=NOW,
            delivery=plan,
        )
    )
    template = ContentSnapshot(
        kind="plan",
        payload_json=plan.model_copy(
            update={"bill_json": "", "change_json": "", "rendered_text": ""}
        ).model_dump_json(),
    )
    case.repo.save_content_snapshot(template)
    return ContentVersion(
        root_publication_id=root_id,
        reason="original",
        bill_snapshot_id=case.bill_id,
        change_snapshot_id=change_snapshot.snapshot_id,
        plan_snapshot_id=template.snapshot_id,
        rendered_text="fixed",
        source_observation_ids=(case.observation.observation_id,) if known else (),
        source_scope="frozen_primary_context/analysis" if scoped else None,
        recorded_at=NOW,
        status_change_id=change_id,
    )


def test_none_scope_keeps_legacy_json_id_summary_and_parent_behavior(case: Case) -> None:
    version = content(case, scoped=False)
    encoded = version.model_dump_json()
    assert "source_scope" not in encoded
    assert version.model_copy(update={"source_scope": None}).model_dump_json() == encoded
    payload = json.loads(encoded)
    payload.pop("recorded_at")
    expected = (
        "content-v1:"
        + hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
    )
    assert version.content_id == expected
    assert "source_scope" not in ContentVersionSummary.from_version(version).model_dump_json()
    case.execute("DELETE FROM source_observations")
    case.repo.save_content_version(version)
    case.repo.restore(case.repo.dump())
    assert case.repo.get_content_version(version.content_id) == version


@pytest.mark.parametrize("known", [False, True])
def test_scoped_reader_roundtrips_explicit_absence_or_one_exact_retained_id(
    case: Case, known: bool
) -> None:
    version = content(case, known=known)
    case.repo.save_content_version(version)
    case.repo.bind_publication_content(version.root_publication_id, version.content_id)
    summary = ContentVersionSummary.from_version(version)
    assert summary.source_scope == "frozen_primary_context/analysis"
    assert len(summary.source_observation_ids) == int(known)
    before = case.repo.dump()
    case.repo.restore(before)
    assert case.repo.get_publication_content(version.root_publication_id) == version
    assert case.repo.dump() == before
    assert diagnose(case)["summary"]["errors"] == 0


def test_stored_positive_survives_later_legitimate_observations_and_actual_cleanup(
    case: Case,
) -> None:
    version = content(case)
    case.repo.save_content_version(version)
    case.repo.bind_publication_content(version.root_publication_id, version.content_id)
    alias = case.observation.model_copy(
        update={
            "document": TextDocument(url="https://example.test/later", kind="print"),
            "recorded_at": NOW + timedelta(days=1),
        }
    )
    case.repo.save_source_observation(case.selected, alias)
    assert case.inspect().reason == "observation_ambiguous"
    case.repo.upsert_summary(case.bill.summary, now=NOW)
    assert isinstance(case.paid.record, AnalysisRecord), "the synthetic paid result is primary"
    case.repo.save_analysis(10, "3039", case.paid.record)
    case.execute("UPDATE llm_batch_items SET meta_json=json_set(meta_json,'$.text',?)", (SOURCE,))
    case.repo.mark_batch_item_accounted(case.paid.batch_id, case.paid.custom_id, at=NOW)
    case.repo.mark_llm_batch_forgotten(case.paid.batch_id, forgotten_at=NOW)
    future = NOW + timedelta(days=365)
    result = case.repo.prune_history(before=future, memo_before=future)
    assert result["batch_payloads"] == 1
    case.repo.restore(case.repo.dump())
    assert case.repo.get_content_version(version.content_id) == version
    assert diagnose(case)["summary"]["errors"] == 0
    assert case.repo.get_computation_result(case.paid.result_id) == case.paid


@pytest.mark.parametrize("fault", ["missing", "owner", "binding", "queue"])
def test_known_scoped_corruption_rejects_read_restore_before_target_touch_and_doctor(
    case: Case, fault: str
) -> None:
    version = content(case)
    case.repo.save_content_version(version)
    if fault == "missing":
        case.execute("DELETE FROM source_observations")
    elif fault == "owner":
        case.execute(
            "UPDATE source_observations SET observation_json=json_set(observation_json,'$.number','foreign')"
        )
    elif fault == "binding":
        case.execute(
            "UPDATE llm_batch_items SET meta_json=json_set(meta_json,'$.input_binding.number','foreign')"
        )
    else:
        case.execute(
            "UPDATE llm_batch_items SET meta_json=json_set(meta_json,'$.queued_at',?)",
            ((NOW - timedelta(days=1)).isoformat(),),
        )
    with pytest.raises(ValueError, match="source"):
        case.repo.get_content_version(version.content_id)
    target = SqliteBillRepository(":memory:")
    target.migrate()
    before = target.dump()
    with pytest.raises(ValueError, match="source"):
        target.restore(case.repo.dump())
    assert target.dump() == before
    target.close()
    assert any(
        row["code"] == "content_source_reference_invalid" for row in diagnose(case)["findings"]
    )


def test_scoped_save_rejects_missing_reference_and_foreign_root(case: Case) -> None:
    version = content(case)
    bad = version.model_copy(
        update={"source_observation_ids": ("source-observation-v1:" + "f" * 64,)}
    )
    with pytest.raises(ValueError, match="source"):
        case.repo.save_content_version(bad)
    case.execute("UPDATE publications SET number='foreign'")
    with pytest.raises(ValueError, match="owner"):
        case.repo.save_content_version(version)


def test_scope_caps_one_id_without_reclassifying_legacy_opaque_metadata(case: Case) -> None:
    version = content(case)
    raw = version.model_dump(mode="json")
    raw["source_observation_ids"] = [case.observation.observation_id] * 2
    with pytest.raises(ValidationError, match="at most one"):
        ContentVersion.model_validate(raw)
    raw["source_scope"] = None
    assert len(ContentVersion.model_validate(raw).source_observation_ids) == 2


def test_public_inspection_labels_the_primary_context_scope_explicitly(case: Case) -> None:
    version = content(case)
    summary = ContentVersionSummary.from_version(version)
    history = ContentHistoryView(
        root_publication_id=version.root_publication_id,
        summaries=(summary,),
        original_id=version.content_id,
        current_id=version.content_id,
        inspected_id=version.content_id,
    )
    command = IncomingCommand(
        update_id=1, chat_id="@test", message_id=1, text="/delivery 1", received_at=NOW
    )
    result = (
        MessageFormatter("en")
        .command_reply(
            command,
            CommandOutcome(
                status=OutcomeStatus.DELIVERY,
                content_history=history,
                delivery=case.repo.publication_by_id(version.root_publication_id),
            ),
        )
        .text
    )
    assert "primary analysis context; amendment source unavailable" in result
