import copy
import datetime as dt
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from tools.source_history_doctor import audit

from lexinform.adapters.candidate_trace_history import CandidateTraceRowError
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    CandidateDisposition,
    CandidateDispositionTrace,
    DocumentPart,
    DocumentSelection,
    DocumentSelectionRecord,
    SourceObservation,
    TextDocument,
)


def original_trace() -> CandidateDispositionTrace:
    return CandidateDispositionTrace.from_decisions(
        TextDocument(
            url="https://official.test/main",
            kind="print",
            required_urls=("https://official.test/required", "https://official.test/second"),
        ),
        (
            CandidateDisposition(
                phase="fallback", index=0, outcome="selected", reason="first_acceptable_candidate"
            ),
        ),
        result="selected",
        reason="first_acceptable_candidate",
        selection_state="unverified",
    )


def rows_sha(rows: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def extended_raw() -> dict[str, Any]:
    part = DocumentPart(url="https://official.test/required", sha256="a" * 64, text_sha256="b" * 64)
    rows = [
        {
            "phase": "fallback",
            "index": 0,
            "required_index": 0,
            "reason": "required_non_annex_appendix",
            "kind": "regulation",
            "parts": [part.model_dump(mode="json", exclude_defaults=True)],
        }
    ]
    return {
        **original_trace().model_dump(mode="json", exclude_defaults=True),
        "required_exclusions": {
            "retention": "complete",
            "observed_rows": 1,
            "rows_sha256": rows_sha(rows),
            "omitted_rows": 0,
            "rows": rows,
        },
    }


def test_typed_required_part_reader_preserves_exact_namespace_and_unknown_fields() -> None:
    trace = CandidateDispositionTrace.model_validate(extended_raw())
    encoded = trace.model_dump(mode="json", exclude_defaults=True)
    assert encoded["required_exclusions"]["rows"][0]["required_index"] == 0
    assert encoded["required_exclusions"]["rows"][0]["parts"][0]["sha256"] == "a" * 64
    assert not trace.is_routine_singleton
    assert CandidateDispositionTrace.model_validate(encoded) == trace


@pytest.mark.parametrize(
    "fault",
    [
        "body",
        "privacy",
        "hash",
        "annex",
        "outer",
        "required",
        "duplicate",
        "order",
        "parent",
        "bound",
    ],
)
def test_required_part_reader_rejects_false_or_unbounded_evidence(fault: str) -> None:
    raw = extended_raw()
    section = raw["required_exclusions"]
    row = section["rows"][0]
    if fault == "body":
        row["parts"][0]["text"] = "PRIVATE BODY"
    elif fault == "privacy":
        row["parts"][0]["url"] = "https://official.test/?token=SECRET"
    elif fault == "hash":
        row["parts"][0]["sha256"] = "not a SHA"
    elif fault == "annex":
        row["kind"] = "annex"
    elif fault == "outer":
        row["index"] = 1
    elif fault == "required":
        row["required_index"] = 2
    elif fault == "duplicate":
        section["rows"].append(copy.deepcopy(row))
    elif fault == "order":
        second = copy.deepcopy(row)
        second["required_index"] = 1
        section["rows"] = [second, row]
    elif fault == "parent":
        raw["dispositions"][0].update(outcome="unattempted", reason="selection_stopped")
        raw.update(result="fallback", reason="all_rejected")
    else:
        row["parts"][0]["members"] = ["x" * 5000]
    section["observed_rows"] = len(section["rows"])
    section["rows_sha256"] = rows_sha(section["rows"])
    with pytest.raises(ValueError):
        CandidateDispositionTrace.model_validate(raw)


def test_required_omission_summary_is_not_partial_membership() -> None:
    raw = extended_raw()
    section = raw["required_exclusions"]
    section.update(retention="omitted", rows=[], omitted_rows=1)
    trace = CandidateDispositionTrace.model_validate(raw)
    assert (
        trace.model_dump(mode="json", exclude_defaults=True)["required_exclusions"]["observed_rows"]
        == 1
    )
    section["rows"] = extended_raw()["required_exclusions"]["rows"]
    with pytest.raises(ValueError):
        CandidateDispositionTrace.model_validate(raw)


def test_unknown_returned_descriptors_do_not_acquire_file_identity() -> None:
    raw = extended_raw()
    section = raw["required_exclusions"]
    section["rows"][0]["parts"] = []
    section["rows_sha256"] = rows_sha(section["rows"])
    trace = CandidateDispositionTrace.model_validate(raw)
    row = trace.model_dump(mode="json", exclude_defaults=True)["required_exclusions"]["rows"][0]
    assert "file_sha256" not in row and row["parts"] == []


def test_legacy_trace_none_is_omitted_without_changing_identity() -> None:
    old = original_trace()
    before = old.model_dump_json(exclude_defaults=True)
    assert "required_exclusions" not in before and old.is_routine_singleton
    copy = CandidateDispositionTrace.model_validate(
        {**old.model_dump(), "required_exclusions": None}
    )
    assert copy.trace_id == old.trace_id and copy.model_dump_json(exclude_defaults=True) == before


@pytest.mark.parametrize("damage", ["none", "parent", "duplicate", "deep"])
def test_required_trace_shared_save_read_restore_and_doctor(tmp_path: Path, damage: str) -> None:
    path = tmp_path / "state.db"
    trace = CandidateDispositionTrace.model_validate(extended_raw())
    assert trace.requested_document is not None, "the complete technical trace retains its request"
    selection = DocumentSelectionRecord.from_selection(DocumentSelection())
    source = SourceObservation(
        term=10,
        number="technical",
        purpose="analysis",
        document=trace.requested_document,
        selection_id=selection.selection_id,
        recorded_at=dt.datetime(2026, 11, 5, tzinfo=dt.UTC),
    )
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        repo.save_source_observation(selection, source)
        repo.save_candidate_disposition_trace(source.observation_id, trace)
        assert repo.get_candidate_disposition_trace(trace.trace_id) == trace
        assert repo.list_candidate_disposition_traces(source.observation_id) == [trace]
        saved = repo.dump()
        repo.restore(saved)
        assert repo.dump() == saved
        if damage != "none":
            raw = extended_raw()
            if damage == "parent":
                raw["required_exclusions"]["rows"][0]["required_index"] = 2
            else:
                raw["required_exclusions"]["rows"][0]["parts"][0]["text"] = ""
            encoded = json.dumps(raw)
            if damage == "duplicate":
                encoded = encoded.replace('"text": ""', '"text": "PRIVATE BODY", "text": ""')
            elif damage == "deep":
                encoded = "[" * 2000 + "0" + "]" * 2000
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("UPDATE candidate_disposition_traces SET trace_json=?", (encoded,))
            with pytest.raises(CandidateTraceRowError, match="invalid_candidate_trace"):
                repo.get_candidate_disposition_trace(trace.trace_id)
            with pytest.raises(CandidateTraceRowError, match="invalid_candidate_trace"):
                repo.list_candidate_disposition_traces(source.observation_id)
            with closing(SqliteBillRepository(":memory:")) as target:
                target.migrate()
                before = target.dump()
                with pytest.raises(ValueError):
                    target.restore(repo.dump())
                assert target.dump() == before
    report = audit(path, include_candidate_traces=True)
    assert bool(report["summary"]["errors"]) == (damage != "none")
    assert "PRIVATE BODY" not in json.dumps(report)


def test_unknown_descriptor_fields_cannot_hide_a_retained_body() -> None:
    raw = extended_raw()
    raw["required_exclusions"]["rows"][0]["parts"][0]["body"] = "PRIVATE BODY"
    with pytest.raises(ValueError):
        CandidateDispositionTrace.model_validate(raw)


@pytest.mark.parametrize("version", [True, "1"])
def test_required_schema_version_is_an_actual_integer(version: object) -> None:
    raw = extended_raw()
    raw["required_exclusions"]["schema_version"] = version
    with pytest.raises(ValueError):
        CandidateDispositionTrace.model_validate(raw)
