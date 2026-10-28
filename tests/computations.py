import datetime as dt

from lexinform.analysis_records import RecordFactory, UsageFields
from lexinform.models import (
    AmendmentsRecord,
    AnalysisRecord,
    ComputationResult,
    DocumentReview,
    DocumentReviewRecord,
    JointRecord,
    NameRecord,
    ReviewContext,
    SupplementRecord,
    TokenUsage,
    computation_id,
)
from lexinform.models.batch import BatchKind
from tests.fakes import make_amendments, make_analysis, make_comparison, make_digest, make_name

Record = (
    AnalysisRecord
    | AmendmentsRecord
    | SupplementRecord
    | JointRecord
    | NameRecord
    | DocumentReviewRecord
)
KINDS: tuple[BatchKind, ...] = (
    "analysis",
    "reanalysis",
    "explanation",
    "amendments",
    "supplement",
    "joint",
    "name",
    "consultation_review",
    "commission_review",
    "act_application",
)
NOW = dt.datetime(2026, 10, 27, tzinfo=dt.UTC)
SHA = "a" * 64


def result_record(kind: BatchKind) -> Record:
    factory = RecordFactory(
        "gpt-5.1",
        "pinned-prompt",
        NOW,
        UsageFields(
            input_tokens=50,
            output_tokens=3,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
        request_sha256=SHA,
    )
    if kind in ("analysis", "reanalysis", "explanation"):
        return factory.analysis(
            make_analysis(), input_chars=100, truncated=False, text_source="pdf"
        )
    if kind == "amendments":
        return factory.amendments(make_amendments(), source_kind="committee_amendments")
    if kind == "supplement":
        return factory.supplement(make_digest(), title="OSR", source_kind="impact_assessment")
    if kind == "joint":
        return factory.joint(make_comparison(), compared_with=["3040"])
    if kind == "name":
        return factory.name(make_name())
    review = DocumentReview(
        purpose=kind,
        complete=False,
        summary="",
        consultation_findings=(),
        effective_rules=(),
        unresolved=("No reviewed source in this schema fixture",),
    )
    return DocumentReviewRecord(
        review=review,
        context=ReviewContext(number="3039", title="Projekt", purpose=kind, documents=()),
        **factory.fields(),
    )


def retained_result(kind: BatchKind) -> ComputationResult:
    record = result_record(kind).model_copy(
        update={"computation_id": computation_id("openai", "provider-batch", kind)}
    )
    return ComputationResult(
        schema_version=1 if kind in ("analysis", "reanalysis") else 2,
        provider="openai",
        batch_id="provider-batch",
        custom_id=kind,
        purpose=kind,
        term=10,
        number="3039",
        generation=0 if kind in ("analysis", "reanalysis") else None,
        collected_at=NOW,
        validation="validated",
        record=record,
        model="gpt-5.1",
        request_sha256=SHA,
        usage=TokenUsage(batch_input=50, batch_output=3),
    )
