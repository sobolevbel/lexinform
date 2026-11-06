import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from tools.replay_print_representation import official_original
from tools.source_history_doctor import audit

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    ArtifactReadIdentity,
    DocumentPart,
    DocumentSelection,
    SourceObservation,
)
from lexinform.models.candidate_trace import CandidateDispositionTrace, SejmPrintRepresentation
from lexinform.models.document_history import DocumentSelectionRecord
from lexinform.services.document_history import record_artifact_reads, record_document_source
from tests.unit.test_input_receipts import NOW
from tests.unit.test_physical_extraction import docx, extractor
from tests.unit.test_replay_print_representation import DOCX, ROW
from tests.unit.test_sejm_proposal_trace_reader import BASE_SHA, trace_of


def test_representation_round_trip_requires_the_exact_word_read(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    document, word_url = official_original([ROW], term=10, number="1608")
    body = docx(DOCX)
    result = extractor().extract_document_with_provenance(body).result
    selection = DocumentSelectionRecord.from_selection(
        DocumentSelection(
            version=document.version_basis,
            parts=(
                DocumentPart(role="bill", url=document.url, sha256=BASE_SHA, text_sha256="a" * 64),
            ),
        )
    )
    observation = SourceObservation(
        term=10,
        number="1608",
        purpose="analysis",
        document=document,
        selection_id=selection.selection_id,
        recorded_at=NOW,
    )
    legacy = trace_of(document)
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        record_document_source(
            repo,
            selection,
            observation,
            (),
            reads=(
                ArtifactReadIdentity(requested_url=document.url, sha256=BASE_SHA, byte_size=100),
            ),
        )
        edition = repo.get_selection_edition(selection.selection_id)
        assert edition is not None, "the selected PDF has one candidate edition member"
        proof = SejmPrintRepresentation(
            term=10,
            number="1608",
            listing_sha256="b" * 64,
            pdf_sha256=BASE_SHA,
            docx_sha256=result.artifact_sha256,
            pdf_text_sha256="a" * 64,
            docx_text_sha256=result.output_text_sha256,
            core_sha256="c" * 64,
            core_chars=200,
            selection_id=selection.selection_id,
            edition_id=edition.edition_id,
            docx_extraction_id=result.result_id,
        )
        trace = CandidateDispositionTrace.model_validate(
            {**legacy.model_dump(), "sejm_print_representation": proof}
        )
        record_artifact_reads(
            repo,
            (
                ArtifactReadIdentity(
                    requested_url=word_url, sha256=result.artifact_sha256, byte_size=len(body)
                ),
            ),
            recorded_at=NOW,
        )
        repo.save_extraction_result(result, recorded_at=NOW)
        repo.save_candidate_disposition_trace(observation.observation_id, legacy)
        repo.save_candidate_disposition_trace(observation.observation_id, trace)
        checkpoint = repo.dump()

        repo.restore(checkpoint)

        assert repo.get_candidate_disposition_trace(trace.trace_id) == trace
        assert repo.dump() == checkpoint
        assert legacy.is_routine_singleton and not trace.is_routine_singleton

        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("DELETE FROM artifact_reads WHERE requested_url=?", (word_url,))
        with pytest.raises(ValueError, match="candidate_trace_representation_parent_mismatch"):
            repo.restore(repo.dump())
        assert repo.get_candidate_disposition_trace(legacy.trace_id) == legacy
    findings = audit(path, include_candidate_traces=True)["findings"]
    assert any(
        item["code"] == "candidate_trace_representation_parent_mismatch" for item in findings
    )
