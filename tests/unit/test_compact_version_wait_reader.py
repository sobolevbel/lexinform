import datetime as dt
import sqlite3
from contextlib import closing

import pytest
from tools.source_history_doctor import candidate_trace_inventory

from lexinform.adapters.candidate_trace_history import CandidateTraceRowError
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    CandidateDisposition,
    CandidateDispositionTrace,
    DocumentSelection,
    DocumentSelectionRecord,
    SourceObservation,
    TextDocument,
)
from lexinform.models.artifacts import ArtifactMetadata, ArtifactReadReceipt
from lexinform.models.candidate_trace import AmendmentPartGap

PART_URL = "https://official.test/amendment.zip"
SHA = "a" * 64
NOW = dt.datetime(2026, 11, 8, tzinfo=dt.UTC)


def waiting_trace(document: TextDocument) -> CandidateDispositionTrace:
    return CandidateDispositionTrace.from_decisions(
        document,
        (
            CandidateDisposition(
                phase="version_comparison",
                index=0,
                outcome="unclassified",
                reason="version_comparison_deferred",
                observed_sha256="b" * 64,
            ),
            CandidateDisposition(
                phase="version_comparison",
                index=1,
                outcome="unclassified",
                reason="normative_part_read_incomplete",
                observed_sha256="b" * 64,
            ),
        ),
        result="waiting",
        reason="version_input_incomplete",
        selection_state="waiting",
    )


def requested(*, long: bool = True) -> TextDocument:
    return TextDocument(
        url="https://official.test/project.zip",
        kind="rcl",
        compare_versions=True,
        version_basis=("v" * 4_096,) if long else (),
        alternatives=(
            TextDocument(
                url="https://official.test/another-project.zip",
                kind="rcl",
                amendment_urls=(PART_URL,),
            ),
        ),
    )


def amendment_gap() -> AmendmentPartGap:
    return AmendmentPartGap(
        phase="version_comparison",
        index=1,
        amendment_index=0,
        reason="returned_no_text",
        file_sha256=SHA,
    )


def with_gaps(
    trace: CandidateDispositionTrace, *gaps: AmendmentPartGap
) -> CandidateDispositionTrace:
    return CandidateDispositionTrace.model_validate(
        {**trace.model_dump(), "compact_version_part_gaps": gaps}
    )


def save_source(
    repo: SqliteBillRepository, document: TextDocument, *, waiting: bool = True
) -> SourceObservation:
    record = DocumentSelectionRecord.from_selection(
        DocumentSelection(state="waiting" if waiting else "selected")
    )
    source = SourceObservation(
        term=10,
        number="RCL/123",
        purpose="analysis",
        document=document,
        selection_id=record.selection_id,
        recorded_at=NOW,
    )
    repo.save_source_observation(record, source)
    return source


def test_compact_version_gaps_retain_no_dispositions_or_urls() -> None:
    baseline = waiting_trace(requested())
    gap = amendment_gap()
    trace = with_gaps(baseline, gap)
    assert baseline.coverage == "omitted_over_limit" and baseline.omitted_dispositions == 2
    assert trace.requested_document is None and not trace.dispositions
    assert trace.compact_version_part_gaps == (gap,)
    assert "compact_version_part_gaps" not in baseline.model_dump_json()
    assert "https://" not in trace.model_dump_json()
    assert trace.trace_id != baseline.trace_id
    with pytest.raises(ValueError, match="ordered positions in an omitted wait"):
        with_gaps(waiting_trace(requested(long=False)), gap)
    with pytest.raises(ValueError, match="ordered positions in an omitted wait"):
        with_gaps(baseline, gap, gap)
    with pytest.raises(ValueError, match="ordered positions in an omitted wait"):
        with_gaps(baseline, gap.model_copy(update={"phase": "fallback"}))
    with pytest.raises(ValueError, match="ordered positions in an omitted wait"):
        with_gaps(baseline, gap.model_copy(update={"index": 2}))


def test_compact_version_gap_links_survive_round_trip_and_fail_closed() -> None:
    document = requested()
    baseline = waiting_trace(document)
    trace = with_gaps(baseline, amendment_gap())
    code = "candidate_trace_compact_version_wait_parent_mismatch"
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        source = save_source(repo, document)
        foreign = save_source(repo, document.model_copy(update={"version_basis": ()}))
        wrong_state = save_source(repo, document, waiting=False)
        repo.save_candidate_disposition_trace(source.observation_id, baseline)
        legacy_dump = repo.dump()
        repo.restore(legacy_dump)
        assert repo.dump() == legacy_dump
        assert repo.list_candidate_disposition_traces(source.observation_id) == [baseline]
        with pytest.raises(CandidateTraceRowError, match=code):
            repo.save_candidate_disposition_trace(source.observation_id, trace)
        assert repo.dump() == legacy_dump

        repo.save_artifact_metadata(ArtifactMetadata(sha256=SHA, byte_size=12, recorded_at=NOW))
        repo.save_artifact_read(
            ArtifactReadReceipt(requested_url=PART_URL, sha256=SHA, byte_size=12, recorded_at=NOW)
        )
        for unmatched in (foreign, wrong_state):
            with pytest.raises(CandidateTraceRowError, match=code):
                repo.save_candidate_disposition_trace(unmatched.observation_id, trace)
        invalid_position = with_gaps(
            baseline, amendment_gap().model_copy(update={"amendment_index": 1})
        )
        with pytest.raises(CandidateTraceRowError, match=code):
            repo.save_candidate_disposition_trace(source.observation_id, invalid_position)
        repo.save_candidate_disposition_trace(source.observation_id, trace)
        saved = repo.dump()
        repo.restore(saved)
        assert repo.get_candidate_disposition_trace(trace.trace_id) == trace
        assert repo.dump() == saved

        mutations = (
            "DELETE FROM artifact_reads;",
            f"DELETE FROM source_candidate_traces WHERE trace_id='{trace.trace_id}';",
            "UPDATE source_candidate_traces SET observation_id="
            f"'{foreign.observation_id}' WHERE trace_id='{trace.trace_id}';",
        )
        for mutation in mutations:
            malformed = saved.replace("\nCOMMIT;", f"\n{mutation}\nCOMMIT;")
            assert malformed != saved, "a repository dump ends with a committed SQL transaction"
            with pytest.raises(CandidateTraceRowError, match=code):
                repo.restore(malformed)
            assert repo.dump() == saved

        with closing(sqlite3.connect(":memory:")) as connection:
            connection.executescript(saved)
            report = candidate_trace_inventory(connection, include_gaps=True)
        comparison = report["gap_coverage"]["version_comparison_wait"]
        assert comparison["deferred_without_dispositions"] == 2
        assert comparison["compact_gap_reasons"] == {"returned_no_text": 1}
