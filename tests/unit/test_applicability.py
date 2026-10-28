import datetime as dt

import pytest
from pydantic import ValidationError

from lexinform.models import (
    AnalysisRecord,
    ApplicabilitySource,
    DocumentPart,
    DocumentSelection,
    ResultApplicabilityObservation,
    ScanInputManifest,
)
from lexinform.models.computations import computation_id
from tests.fakes import make_analysis

NOW = dt.datetime(2026, 10, 27, tzinfo=dt.UTC)
URL = "https://api.test/current.pdf"
MARKER = "NEVER-STORE-THE-PAID-ANSWER-OR-SOURCE-TEXT"


def source_record() -> AnalysisRecord:
    return AnalysisRecord(
        analysis=make_analysis(),
        model="gpt-5.1",
        prompt_version="recorded",
        input_chars=100,
        truncated=False,
        text_source="pdf",
        created_at=NOW,
        computation_id=computation_id("openai", "provider-batch", "analysis"),
        request_sha256="a" * 64,
        source_url=URL,
        text_sha256="text-v2:" + "b" * 64,
    )


def captured(record: AnalysisRecord | None = None) -> ResultApplicabilityObservation:
    observation = ResultApplicabilityObservation.from_record(
        record or source_record(),
        term=10,
        number="3039",
        now=NOW,
    )
    assert observation is not None, "the fixture supplies a known paid ID, URL and text fingerprint"
    return observation


def manifest(char: str) -> ScanInputManifest:
    return ScanInputManifest(
        source_sha256=char * 64,
        source_bytes=10,
        source_pages=1,
        input_sha256=char * 64,
        input_bytes=10,
        first_page=1,
        page_count=1,
    )


def test_check_time_and_paid_answer_do_not_create_a_new_source_association() -> None:
    first = captured()
    record = source_record().model_copy(
        update={
            "analysis": make_analysis().model_copy(update={"summary": MARKER}),
            "source_checked_at": NOW + dt.timedelta(days=30),
        }
    )
    later = ResultApplicabilityObservation.from_record(
        record,
        term=10,
        number="3039",
        now=NOW + dt.timedelta(days=30),
    )
    assert later is not None, "the same known source remains eligible for metadata capture"
    assert later.observation_id == first.observation_id
    assert later.created_at != first.created_at
    assert MARKER not in later.model_dump_json()
    assert ResultApplicabilityObservation.model_validate_json(first.model_dump_json()) == first


@pytest.mark.parametrize("field", ["url", "kind", "text_sha256", "law_sha256", "selection_sha256"])
def test_recorded_source_identity_fields_remain_distinct(field: str) -> None:
    first = captured()
    values: dict[str, object] = {
        "url": URL + "?version=2",
        "kind": "rcl",
        "text_sha256": "text-v2:" + "c" * 64,
        "law_sha256": "law-v999:opaque-recorded-value",
        "selection_sha256": "d" * 64,
    }
    source = first.source.model_copy(update={field: values[field]})
    second = first.model_copy(update={"source": source})
    assert second.observation_id != first.observation_id


@pytest.mark.parametrize("field", ["result_id", "term", "number"])
def test_association_identity_keeps_paid_result_and_target_scope(field: str) -> None:
    first = captured()
    values: dict[str, object] = {"result_id": "result-v1:" + "d" * 64, "term": 11, "number": "3040"}
    second = first.model_copy(update={field: values[field]})
    assert second.observation_id != first.observation_id


def test_digest_namespaces_are_preserved_without_current_policy_recomputation() -> None:
    record = source_record().model_copy(
        update={
            "text_sha256": "legacy-opaque-fingerprint",
            "law_sha256": "law-v999:recorded-value",
        }
    )
    observation = captured(record)
    assert observation.source.text_sha256 == "legacy-opaque-fingerprint"
    assert observation.source.law_sha256 == "law-v999:recorded-value"


@pytest.mark.parametrize("unknown", ["paid_id", "url", "fingerprints"])
def test_unknown_legacy_input_does_not_acquire_an_association(unknown: str) -> None:
    updates: dict[str, object] = (
        {"computation_id": None}
        if unknown == "paid_id"
        else {"source_url": None}
        if unknown == "url"
        else {"text_sha256": None, "law_sha256": None, "selection": DocumentSelection()}
    )
    record = source_record().model_copy(update=updates)
    assert (
        ResultApplicabilityObservation.from_record(record, term=10, number="3039", now=NOW) is None
    )


def test_original_paid_scan_manifest_never_claims_a_newly_observed_source() -> None:
    record = source_record().model_copy(
        update={
            "text_sha256": None,
            "law_sha256": None,
            "selection": DocumentSelection(scan_input=manifest("a")),
        }
    )
    assert ApplicabilitySource.from_record(record) is None
    parts = (DocumentPart(url=URL, sha256="c" * 64, text=MARKER),)
    one = record.model_copy(
        update={"selection": DocumentSelection(parts=parts, scan_input=manifest("a"))}
    )
    two = record.model_copy(
        update={"selection": DocumentSelection(parts=parts, scan_input=manifest("b"))}
    )
    first, second = captured(one), captured(two)
    assert first.observation_id == second.observation_id
    assert first.source.selection_sha256 is not None
    assert MARKER not in first.model_dump_json()


def test_an_empty_or_unknown_fingerprint_is_not_a_source_identity() -> None:
    with pytest.raises(ValidationError, match="recorded fingerprint"):
        ApplicabilitySource(url=URL, kind="print")
    with pytest.raises(ValidationError):
        ApplicabilitySource(url=URL, kind="print", text_sha256="")


def test_recording_time_has_an_explicit_timezone() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        ResultApplicabilityObservation.from_record(
            source_record(), term=10, number="3039", now=NOW.replace(tzinfo=None)
        )
