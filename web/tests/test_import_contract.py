import datetime as dt
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from lexinform.adapters.sqlite_repo import SCHEMA_VERSION
from lexinform.models import Bill, Publication, Stage, StatusChange
from lexinform.models.batch import BatchIntent, BatchItemMeta, BatchResult, LlmBatchItem
from lexinform.models.bill import LocatedText, ReadyAnalysis
from lexinform.models.enums import BillStatus, PublicationKind, PublicationStatus
from lexinform.models.observations import ObservedProcess
from lexinform.models.wykaz import WykazEntry, wykaz_summary
from lexinform_web.ingestion.contract import (
    REVIEWED_MIGRATIONS,
    SUPPORTED_SCHEMA,
    Identity,
    ImportDocumentV1,
    PublicLink,
    SnapshotOrigin,
)
from lexinform_web.ingestion.projection import identity_of, project_state

NOW = dt.datetime(2026, 9, 27, tzinfo=dt.UTC)
ORIGIN = SnapshotOrigin(source_commit="a" * 40, dump_sha256="b" * 64, source_schema=34)


@pytest.fixture
def legacy_bill() -> Bill:
    raw = json.loads((Path(__file__).parent / "fixtures/import_legacy.json").read_text())
    return Bill.model_validate(raw["bill"])


def test_legacy_missing_freshness_and_negative_analysis_survive(legacy_bill: Bill) -> None:
    document = project_state(
        origin=ORIGIN.model_copy(update={"source_schema": 22}), bills=[legacy_bill]
    )

    restored = ImportDocumentV1.model_validate_json(document.model_dump_json())
    assert restored == document
    imported = restored.bills[0]
    assert imported.applied_analysis is not None
    assert imported.applied_analysis.relevant is False
    assert imported.applied_analysis.practical_impact == ""
    assert imported.applied_analysis.text_checked_at is None
    assert imported.processed is None
    assert imported.aspect_freshness == "unknown"
    assert imported.visibility == "editorial"
    assert restored.coverage.analyzed_candidates == 1


def test_current_pending_result_does_not_replace_applied_analysis(legacy_bill: Bill) -> None:
    record = legacy_bill.analysis
    assert record is not None
    newer = record.model_copy(update={"revision": 2})
    raw_stage = Stage(stage_name="New", stage_type="New")
    baseline = ObservedProcess(analysis_revision=1, stages=())
    bill = legacy_bill.model_copy(
        update={
            "stages": (raw_stage,),
            "observed_process": baseline,
            "awaiting_batch_since": NOW,
            "ready_analysis": ReadyAnalysis(record=newer, located=LocatedText(), text="SECRET"),
            "last_error": "SECRET",
        }
    )

    document = project_state(origin=ORIGIN, bills=[bill])

    imported = document.bills[0]
    assert imported.applied_analysis is not None
    assert imported.applied_analysis.revision == 1
    assert imported.processed is not None
    assert imported.processed.stages == ()
    assert imported.facts.stages[0].name == "New"
    assert imported.awaiting_batch_since == NOW
    assert imported.has_staged_analysis
    assert "SECRET" not in document.model_dump_json()
    assert "prompt_version" not in document.model_dump_json()


def test_staged_result_without_applied_analysis_is_not_a_candidate(legacy_bill: Bill) -> None:
    record = legacy_bill.analysis
    assert record is not None
    bill = legacy_bill.model_copy(
        update={
            "analysis": None,
            "ready_analysis": ReadyAnalysis(record=record, located=LocatedText()),
        }
    )
    document = project_state(origin=ORIGIN, bills=[bill])
    assert document.bills[0].applied_analysis is None
    assert document.coverage.analyzed_candidates == 0


@pytest.mark.parametrize("state", ["queued", "submitting"])
def test_pending_intent_is_metadata_only(legacy_bill: Bill, state: str) -> None:
    raw = json.loads((Path(__file__).parent / "fixtures/import_pending.json").read_text())
    raw["state"] = state
    intent = BatchIntent.model_validate(raw)

    document = project_state(origin=ORIGIN, bills=[legacy_bill], intents=[intent])

    assert document.pending_work[0].state == ("unknown" if state == "submitting" else "queued")
    assert "SECRET" not in document.model_dump_json()
    assert document.coverage.analyzed_candidates == 1


def test_unconsumed_batch_is_not_an_explanation(legacy_bill: Bill) -> None:
    item = LlmBatchItem(
        batch_id="batch-1",
        custom_id="item-1",
        call_kind="analysis",
        term=10,
        number="3039",
        meta=BatchItemMeta(
            input_chars=1,
            truncated=False,
            text_source="pdf",
            source_kind="print",
            source_url=None,
            revision=2,
            text_sha256=None,
            text="SECRET",
        ),
    )
    document = project_state(origin=ORIGIN, bills=[legacy_bill], batch_items=[item])
    assert document.pending_work[0].state == "submitted"
    assert "SECRET" not in document.model_dump_json()
    record = legacy_bill.analysis
    assert record is not None
    answered = item.model_copy(
        update={
            "result": BatchResult(
                custom_id=item.custom_id,
                answer=record.analysis.model_copy(update={"summary": "SECRET"}),
            )
        }
    )
    ready = project_state(origin=ORIGIN, bills=[legacy_bill], batch_items=[answered])
    assert ready.pending_work[0].state == "result_staged"
    assert "SECRET" not in ready.model_dump_json()
    assert ready.bills[0].applied_analysis == document.bills[0].applied_analysis
    consumed = item.model_copy(update={"consumed_at": NOW})
    assert not project_state(
        origin=ORIGIN, bills=[legacy_bill], batch_items=[consumed]
    ).pending_work


def test_linked_hidden_target_and_joint_keep_distinct_identities(legacy_bill: Bill) -> None:
    alias = legacy_bill.model_copy(
        update={
            "summary": legacy_bill.summary.model_copy(update={"number": "RPW/123/2026"}),
            "status": BillStatus.LINKED,
            "linked_number": "3039",
            "analysis": None,
        }
    )
    target = legacy_bill.model_copy(update={"status": BillStatus.SKIPPED_PREFILTER})
    joint = legacy_bill.model_copy(
        update={
            "summary": legacy_bill.summary.model_copy(
                update={
                    "number": "3040",
                    "prints_considered_jointly": ("3039",),
                }
            ),
        }
    )

    document = project_state(origin=ORIGIN, bills=[alias, target, joint])

    assert len(document.bills) == 3
    assert {relation.kind for relation in document.relations} == {"lifecycle", "joint"}
    assert document.bills[1].applied_analysis is not None
    assert document.bills[1].visibility == "editorial"
    assert document.coverage.analyzed_candidates == 1


def test_missing_joint_target_rejects_entire_document(legacy_bill: Bill) -> None:
    bill = legacy_bill.model_copy(
        update={
            "summary": legacy_bill.summary.model_copy(
                update={"prints_considered_jointly": ("9999",)}
            ),
        }
    )
    with pytest.raises(ValueError, match="dangling"):
        project_state(origin=ORIGIN, bills=[bill])


def test_history_is_independent_of_telegram_delivery(legacy_bill: Bill) -> None:
    change = StatusChange(
        term=10,
        number="3039",
        old_fingerprint=None,
        new_fingerprint="new",
        new_stages=[],
        content_changed=True,
        detected_at=NOW,
    )
    publication = Publication(
        term=10,
        number="3039",
        kind=PublicationKind.STATUS_UPDATE,
        status=PublicationStatus.UNKNOWN,
        channel_id="@readers",
        message_id=42,
        created_at=NOW,
        error="SECRET",
    )
    document = project_state(
        origin=ORIGIN,
        bills=[legacy_bill],
        changes=[change],
        publications=[publication],
        public_channel="@readers",
    )
    assert document.events[0].content_changed
    assert document.events[0].occurred_at is None
    assert not document.telegram
    assert "SECRET" not in document.model_dump_json()


def test_only_explicit_public_channel_sent_links_are_exported(legacy_bill: Bill) -> None:
    public = Publication(
        term=10,
        number="3039",
        kind=PublicationKind.NEW_BILL,
        status=PublicationStatus.SENT,
        channel_id="@readers",
        message_id=42,
        created_at=NOW,
    )
    technical = public.model_copy(update={"channel_id": "@technical", "message_id": 43})
    private = public.model_copy(update={"channel_id": "-10012345", "message_id": 44})
    document = project_state(
        origin=ORIGIN,
        bills=[legacy_bill],
        publications=[public, technical, private],
        public_channel="@readers",
    )
    assert [entry.link.url for entry in document.telegram] == ["https://t.me/readers/42"]
    assert "technical" not in document.model_dump_json()
    assert not project_state(origin=ORIGIN, bills=[legacy_bill], publications=[public]).telegram


def test_wykaz_key_survives_rollover_but_not_distinct_entry(legacy_bill: Bill) -> None:
    entry = WykazEntry(
        number="UD408",
        title="Projekt",
        kind="Projekty ustaw",
        published_at=NOW,
        web_url="https://www.gov.pl/web/premier/projekt-1",
    )
    bill = legacy_bill.model_copy(update={"summary": wykaz_summary(entry, term=10), "wykaz": entry})
    rollover = bill.model_copy(update={"summary": wykaz_summary(entry, term=11)})
    second_entry = entry.model_copy(update={"web_url": "https://www.gov.pl/web/premier/projekt-2"})
    second = bill.model_copy(update={"wykaz": second_entry})
    assert identity_of(bill) == identity_of(rollover)
    assert identity_of(bill) != identity_of(second)
    with pytest.raises(ValidationError, match="duplicate source identity"):
        project_state(origin=ORIGIN, bills=[bill, rollover])
    with pytest.raises(ValidationError):
        Identity(source="wykaz", scope="10", external_id="UD408")


@pytest.mark.parametrize("version", [0, SUPPORTED_SCHEMA + 1])
def test_unknown_source_schema_is_rejected(version: int) -> None:
    with pytest.raises(ValidationError):
        SnapshotOrigin(source_commit="a" * 40, dump_sha256="b" * 64, source_schema=version)


@pytest.mark.parametrize(
    "url", ["javascript:alert(1)", "https://secret@example.com/a", "file:///tmp/a"]
)
def test_unsafe_links_are_rejected(url: str) -> None:
    with pytest.raises(ValidationError):
        PublicLink(url=url)


def test_contract_rejects_unknown_fields_versions_and_false_counts(legacy_bill: Bill) -> None:
    document = project_state(origin=ORIGIN, bills=[legacy_bill])
    for field, value in (
        ("contract_version", 2),
        ("commands", []),
        (
            "coverage",
            {
                "total": 0,
                "with_applied_analysis": 0,
                "analyzed_candidates": 0,
            },
        ),
    ):
        raw = document.model_dump(mode="json")
        raw[field] = value
        with pytest.raises(ValidationError):
            ImportDocumentV1.model_validate(raw)
    raw = document.model_dump(mode="json")
    raw["bills"][0]["facts"]["raw_error"] = "SECRET"
    with pytest.raises(ValidationError):
        ImportDocumentV1.model_validate(raw)


def test_contract_rejects_cycles_and_duplicate_events(legacy_bill: Bill) -> None:
    other = legacy_bill.model_copy(
        update={
            "summary": legacy_bill.summary.model_copy(update={"number": "3040"}),
        }
    )
    document = project_state(origin=ORIGIN, bills=[legacy_bill, other])
    raw = document.model_dump(mode="json")
    first, second = (bill.identity.model_dump() for bill in document.bills)
    raw["relations"] = [
        {"source": first, "target": second, "kind": "lifecycle"},
        {"source": second, "target": first, "kind": "lifecycle"},
    ]
    with pytest.raises(ValidationError, match="cycle"):
        ImportDocumentV1.model_validate(raw)

    change = StatusChange(
        term=10,
        number="3039",
        old_fingerprint=None,
        new_fingerprint="new",
        new_stages=[],
        detected_at=NOW,
    )
    with pytest.raises(ValidationError, match="duplicate event"):
        project_state(origin=ORIGIN, bills=[legacy_bill], changes=[change, change])


def test_incomplete_wykaz_and_empty_analysis_require_reconciliation(legacy_bill: Bill) -> None:
    bill = legacy_bill.model_copy(
        update={
            "summary": legacy_bill.summary.model_copy(update={"number": "WPL/UD408"}),
        }
    )
    with pytest.raises(ValueError, match="evidence is missing"):
        project_state(origin=ORIGIN, bills=[bill])
    record = legacy_bill.analysis
    assert record is not None
    broken = legacy_bill.model_copy(
        update={
            "analysis": record.model_copy(
                update={
                    "analysis": record.analysis.model_copy(update={"summary": " "}),
                }
            ),
        }
    )
    with pytest.raises(ValidationError, match="summary and rationale"):
        project_state(origin=ORIGIN, bills=[broken])


def test_contract_schema_pin_requires_explicit_review() -> None:
    from_schema = json.loads(
        (Path(__file__).parents[2] / "tests/fixtures/website/state_audit_v2.json").read_text()
    )
    audited = from_schema["source"]["validated_schema_version"]
    assert set(REVIEWED_MIGRATIONS) == set(range(audited + 1, SUPPORTED_SCHEMA + 1))
    assert SUPPORTED_SCHEMA == SCHEMA_VERSION
