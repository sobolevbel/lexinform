import logging
from collections.abc import Iterator

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.log_context import correlation_fields, current_log_fields, log_context
from lexinform.logging_setup import CorrelationFilter, JsonFormatter
from lexinform.models import (
    AmendmentInputReceipt,
    AmendmentsRecord,
    AnalysisRecord,
    BatchIntent,
    ComputationResult,
    DeliveryPlan,
    LlmBatch,
    LlmBatchItem,
    ProcessDetail,
    Publication,
    PublicationKind,
    PublicationStatus,
    ResultApplicabilityObservation,
    ResultApplicationTarget,
    Stage,
    StatusChange,
    computation_id,
)
from lexinform.services.analysis import Waiting
from lexinform.services.content_history import record_publication_content
from lexinform.services.document_history import record_source_read
from lexinform.services.input_receipts import capture_primary_input_receipt
from lexinform.services.result_applications import record_secondary_application
from lexinform.text_projection import SourcePiece, project_pieces
from tests.computations import retained_result
from tests.fakes import FixedClock
from tests.unit.test_content_history import publication as content_publication
from tests.unit.test_input_receipts import NOW, meta, request
from tests.unit.test_primary_input_receipt_capture import captured
from tests.unit.test_senate_input_capture import senate_case


@pytest.fixture(autouse=True)
def filtered_architectural_logs(caplog: pytest.LogCaptureFixture) -> Iterator[None]:
    correlation = CorrelationFilter()
    caplog.handler.addFilter(correlation)
    try:
        yield
    finally:
        caplog.handler.removeFilter(correlation)


def test_native_source_receipt_result_application_content_and_physical_ids_match_sql(
    repo: SqliteBillRepository, process_3039: ProcessDetail, caplog: pytest.LogCaptureFixture
) -> None:
    case, _, _, _ = senate_case(repo, process_3039)
    caplog.set_level(logging.DEBUG, logger="lexinform.adapters.sqlite_repo")
    with log_context(run_id=31):
        assert isinstance(
            case.service.summarize_amendments(case.bill, case.document, may_wait=True), Waiting
        )
        (intent,) = repo.list_queued_batch_intents()
        receipt = intent.meta.prepared_input_receipt
        assert isinstance(receipt, AmendmentInputReceipt), (
            "the native Senate identity fixture retains its input parents"
        )
        item = LlmBatchItem.from_intent("correlation-batch", intent)
        repo.save_llm_batch(
            LlmBatch(
                batch_id=item.batch_id,
                provider="openai",
                call_kind="amendments",
                submitted_at=NOW,
                status="submitted",
                request_count=1,
                estimated_cost_usd=0,
            ),
            [item],
        )
        result = retained_result("amendments")
        assert isinstance(result.record, AmendmentsRecord), (
            "the retained fixture supplies an amendment result"
        )
        identity = computation_id("openai", item.batch_id, item.custom_id)
        record = result.record.model_copy(
            update={
                "computation_id": identity,
                "request_sha256": intent.request.payload_sha256,
                "source_kind": "senate_amendments",
            }
        )
        result = ComputationResult.model_validate(
            result.model_copy(
                update={
                    "batch_id": item.batch_id,
                    "custom_id": item.custom_id,
                    "request_sha256": intent.request.payload_sha256,
                    "record": record,
                }
            ).model_dump(mode="json")
        )
        repo.save_computation_result(result)
        stage = Stage(
            stage_type="SenatePosition",
            stage_name="Stanowisko Senatu",
            position="wniósł poprawki",
            print_number="3213",
        )
        change = StatusChange(
            term=10,
            number="3039",
            old_fingerprint=None,
            new_fingerprint="correlation",
            new_stages=[stage],
            amendments=record,
            detected_at=NOW,
        )
        change_id = repo.add_status_change(change)
        assert change_id is not None, "the fresh status-change identity is inserted once"
        change = change.model_copy(update={"id": change_id})
        record_secondary_application(
            repo,
            10,
            "3039",
            identity,
            target=ResultApplicationTarget(kind="status_change", ref=str(change_id)),
            now=NOW,
        )
        publication = Publication(
            term=10,
            number="3039",
            kind=PublicationKind.STATUS_UPDATE,
            channel_id="offline",
            status=PublicationStatus.QUEUED,
            status_change_id=change_id,
            created_at=NOW,
            delivery=DeliveryPlan(
                bill_json=case.bill.model_dump_json(),
                change_json=change.model_dump_json(),
                rendered_text="PRIVATE_RENDERED_TEXT",
            ),
        )
        pub_id = repo.create_publication(publication)
        publication = publication.model_copy(update={"id": pub_id})
        content = record_publication_content(repo, FixedClock(NOW), publication)
        assert content is not None, "the standalone Senate amendment update has immutable content"
        repo.mark_publication(pub_id, PublicationStatus.SENT, message_id=77, sent_at=NOW)
    events = {
        record.getMessage(): correlation_fields(getattr(record, "correlation", None))
        for record in caplog.records
    }
    assert events["local extraction result write"]["extraction_id"] == receipt.extraction_id
    assert events["local extraction result write"]["artifact_sha256"] == receipt.artifact_sha256
    assert (
        events["local selection extraction binding"]["selection_id"] == receipt.source_selection_id
    )
    assert events["local selection extraction binding"]["extraction_id"] == receipt.extraction_id
    assert events["local artifact metadata write"]["artifact_sha256"] == receipt.artifact_sha256
    for name in ("local batch intent write", "local batch item write"):
        assert events[name]["extraction_id"] == receipt.extraction_id
        assert events[name]["selection_id"] == receipt.source_selection_id
        assert events[name]["artifact_sha256"] == receipt.artifact_sha256
        assert events[name]["request_sha256"] == intent.request.payload_sha256
    assert events["local computation result write"]["computation_id"] == result.result_id
    (application,) = repo.list_result_applications(10, "3039")
    assert application.target is not None, "the projection records its saved status-change target"
    applied = events["local result application write"]
    assert applied["application_target_ref"] == application.target.ref == str(change_id)
    assert applied["application_target_kind"] == application.target.kind
    assert applied["computation_id"] == application.result_id
    assert applied["application_phase"] == application.phase
    assert events["local content version write"]["content_id"] == content.content_id
    assert events["local publication write"]["publication_id"] == pub_id
    assert events["local publication status write"]["publication_id"] == pub_id
    assert repo.get_extraction_result(receipt.extraction_id or "") is not None
    assert repo.get_document_selection(receipt.source_selection_id or "") is not None
    assert repo.get_publication_content(pub_id) == content
    assert all(fields["run_id"] == 31 for fields in events.values())
    encoded = "\n".join(
        JsonFormatter().format(record)
        for record in caplog.records
        if record.name == "lexinform.adapters.sqlite_repo"
    )
    assert "PRIVATE_RENDERED_TEXT" not in encoded and "https://" not in encoded
    assert current_log_fields() == {}


def test_failed_extraction_write_never_emits_a_successful_identity(
    repo: SqliteBillRepository, caplog: pytest.LogCaptureFixture
) -> None:
    _, source = captured()
    caplog.set_level(logging.DEBUG, logger="lexinform.adapters.sqlite_repo")
    with log_context(run_id=41), pytest.raises(ValueError, match="complete-body"):
        repo.save_extraction_result(source.extractions[0], recorded_at=NOW)
    assert not caplog.records
    assert current_log_fields() == {}


@pytest.mark.parametrize("cancel", [False, True])
def test_new_write_traces_do_not_claim_commit_or_change_transaction_cancellation(
    repo: SqliteBillRepository,
    process_3039: ProcessDetail,
    caplog: pytest.LogCaptureFixture,
    cancel: bool,
) -> None:
    _, source = captured()
    caplog.set_level(logging.DEBUG, logger="lexinform.adapters.sqlite_repo")
    repo.upsert_summary(process_3039, now=NOW)
    before = repo.dump()
    failure = KeyboardInterrupt("cancel") if cancel else ValueError("rollback")
    with pytest.raises(type(failure)) as raised, log_context(run_id=51), repo.atomic():
        record_source_read(
            repo, source, term=10, number="3039", purpose="analysis", recorded_at=NOW
        )
        raise failure
    assert raised.value is failure
    assert repo.dump() == before
    assert any(r.getMessage() == "local extraction result write" for r in caplog.records)
    assert current_log_fields() == {}


@pytest.mark.parametrize("known", [False, True])
def test_intent_request_and_parent_ids_are_only_logged_when_actually_available(
    repo: SqliteBillRepository,
    caplog: pytest.LogCaptureFixture,
    known: bool,
) -> None:
    selected, source = captured()
    text = selected.parts[0].text
    frozen = request(text)
    receipt = (
        capture_primary_input_receipt(
            frozen, selected, project_pieces(text, (SourcePiece(text, 0),)), source
        )
        if known
        else None
    )
    metadata = meta(receipt).model_copy(update={"input_chars": len(text)})
    if not known:
        frozen = frozen.model_copy(update={"payload_json": None, "payload_sha256": None})
    intent = BatchIntent.from_request(
        request=frozen, meta=metadata, provider="openai", created_at=NOW
    )
    caplog.set_level(logging.DEBUG, logger="lexinform.adapters.sqlite_repo")
    before = intent.model_dump_json()
    with log_context(
        selection_id="unrelated-parent",
        artifact_sha256="unrelated-body",
        extraction_id="unrelated-extraction",
    ):
        repo.save_batch_intent(intent)
    fields = correlation_fields(getattr(caplog.records[-1], "correlation", None))
    assert intent.model_dump_json() == before
    if known:
        assert receipt is not None, "the supported primary fixture supplies captured parents"
        assert fields["request_sha256"] == frozen.payload_sha256
        assert fields["extraction_id"] == receipt.extraction_id
        assert fields["artifact_sha256"] == receipt.artifact_sha256
        assert fields["selection_id"] == receipt.source_selection_id
    else:
        assert not fields.keys() & {
            "extraction_id",
            "artifact_sha256",
            "selection_id",
            "request_sha256",
        }
    assert "run_id" not in fields


def test_applicability_event_preserves_its_actual_identity_without_source_inference(
    repo: SqliteBillRepository, process_3039: ProcessDetail, caplog: pytest.LogCaptureFixture
) -> None:
    repo.upsert_summary(process_3039, now=NOW)
    result = retained_result("analysis")
    repo.save_computation_result(result)
    assert isinstance(result.record, AnalysisRecord), "the retained fixture has primary analysis"
    observation = ResultApplicabilityObservation.from_record(
        result.record.model_copy(
            update={"source_url": "https://private.test/source", "text_sha256": "a" * 64}
        ),
        term=10,
        number="3039",
        now=NOW,
    )
    assert observation is not None, "the fixture supplies an explicit source fingerprint"
    caplog.set_level(logging.DEBUG, logger="lexinform.adapters.sqlite_repo")
    repo.save_applicability_observation(observation)
    fields = correlation_fields(getattr(caplog.records[-1], "correlation", None))
    assert fields["observation_id"] == observation.observation_id
    assert fields["computation_id"] == result.result_id
    assert repo.get_applicability_observation(observation.observation_id) == observation
    assert "https://" not in JsonFormatter().format(caplog.records[-1])


def test_publication_upsert_logs_the_retained_identity_not_a_new_sequence_candidate(
    repo: SqliteBillRepository, caplog: pytest.LogCaptureFixture
) -> None:
    original = content_publication(repo)
    caplog.set_level(logging.DEBUG, logger="lexinform.adapters.sqlite_repo")
    repeated = content_publication(repo)
    assert repeated == original
    fields = correlation_fields(getattr(caplog.records[-1], "correlation", None))
    assert fields["publication_id"] == original
    assert repo.publication_by_id(original) is not None


def test_ignored_intent_collision_never_logs_the_unretained_request_or_parents(
    repo: SqliteBillRepository, caplog: pytest.LogCaptureFixture
) -> None:
    selected, source = captured()
    text = selected.parts[0].text
    frozen = request(text)
    receipt = capture_primary_input_receipt(
        frozen, selected, project_pieces(text, (SourcePiece(text, 0),)), source
    )
    assert receipt is not None, "the native single-part fixture provides receipt parents"
    original_text = "Original frozen request"
    original_request = request(original_text)
    original = BatchIntent.from_request(
        request=original_request,
        meta=meta().model_copy(update={"input_chars": len(original_text)}),
        provider="openai",
        created_at=NOW,
    )
    attempted = BatchIntent.from_request(
        request=frozen,
        meta=meta(receipt).model_copy(update={"input_chars": len(text)}),
        provider="openai",
        created_at=NOW,
    )
    assert attempted.request.custom_id == original.request.custom_id
    repo.save_batch_intent(original)
    caplog.clear()
    caplog.set_level(logging.DEBUG, logger="lexinform.adapters.sqlite_repo")
    repo.save_batch_intent(attempted)
    assert repo.list_queued_batch_intents() == [original]
    assert caplog.records == []


def test_publication_collision_never_labels_the_retained_row_with_a_supplied_other_owner(
    repo: SqliteBillRepository, process_3039: ProcessDetail, caplog: pytest.LogCaptureFixture
) -> None:
    repo.upsert_summary(process_3039, now=NOW)
    change_id = repo.add_status_change(
        StatusChange(
            term=10,
            number="3039",
            old_fingerprint=None,
            new_fingerprint="pub-owner",
            new_stages=[],
            detected_at=NOW,
        )
    )
    assert change_id is not None, "the fixture creates an actual event identity"
    original = Publication(
        term=10,
        number="3039",
        kind=PublicationKind.STATUS_UPDATE,
        channel_id="offline",
        status=PublicationStatus.QUEUED,
        status_change_id=change_id,
        created_at=NOW,
    )
    identity = repo.create_publication(original)
    caplog.set_level(logging.DEBUG, logger="lexinform.adapters.sqlite_repo")
    with log_context(bill_term=99, bill_number="unrelated-scope"):
        actual = repo.create_publication(
            original.model_copy(update={"term": 99, "number": "other-owner"})
        )
    assert actual == identity
    retained = repo.publication_by_id(identity)
    assert retained is not None, "the upsert retains its physical row"
    assert (retained.term, retained.number) == (10, "3039")
    fields = correlation_fields(getattr(caplog.records[-1], "correlation", None))
    assert fields["publication_id"] == identity
    assert not fields.keys() & {"bill_term", "bill_number"}


def test_missing_publication_update_does_not_emit_a_retained_status(
    repo: SqliteBillRepository, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="lexinform.adapters.sqlite_repo")
    before = repo.dump()
    repo.mark_publication(999999, PublicationStatus.SENT, message_id=77, sent_at=NOW)
    assert caplog.records == []
    assert repo.dump() == before


def test_logging_does_not_turn_an_unvalidated_optional_receipt_into_a_new_write_error(
    repo: SqliteBillRepository, caplog: pytest.LogCaptureFixture
) -> None:
    frozen = request()
    metadata = meta().model_copy(update={"prepared_input_receipt": {"future": "opaque"}})
    intent = BatchIntent.model_construct(
        request=frozen, meta=metadata, provider="openai", created_at=NOW
    )
    caplog.set_level(logging.DEBUG, logger="lexinform.adapters.sqlite_repo")
    with pytest.warns(UserWarning, match="PydanticSerializationUnexpectedValue"):
        repo.save_batch_intent(intent)
    fields = correlation_fields(getattr(caplog.records[-1], "correlation", None))
    assert fields["request_sha256"] == frozen.payload_sha256
    assert not fields.keys() & {"selection_id", "artifact_sha256", "extraction_id"}
