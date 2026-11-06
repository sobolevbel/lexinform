from contextlib import closing

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    ArtifactReadIdentity,
    DocumentPart,
    DocumentSelection,
    DocumentSelectionRecord,
    SourceObservation,
    TextDocument,
)
from lexinform.models.candidate_trace import (
    CandidateDisposition,
    CandidateDispositionTrace,
    SejmAdoptedText,
)
from lexinform.services.document_history import record_document_source
from tests.unit.test_input_receipts import NOW


def test_adoption_trace_round_trips_and_cannot_bind_to_another_print() -> None:
    url = "https://api.sejm.gov.pl/sejm/term10/processes/2111/attachment/2111_u3.pdf"
    document = TextDocument(url=url, kind="text_after3")
    selection = DocumentSelectionRecord.from_selection(
        DocumentSelection(
            parts=(DocumentPart(role="bill", url=url, sha256="a" * 64, text_sha256="b" * 64),)
        )
    )
    source = SourceObservation(
        term=10,
        number="2111",
        purpose="analysis",
        document=document,
        selection_id=selection.selection_id,
        recorded_at=NOW,
    )
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        record_document_source(
            repo,
            selection,
            source,
            (),
            reads=(ArtifactReadIdentity(requested_url=url, sha256="a" * 64, byte_size=100),),
        )
        edition = repo.get_selection_edition(selection.selection_id)
        assert edition is not None, "the selected PDF is a known edition member"
        proof = SejmAdoptedText(
            term=10,
            number="2111",
            stage_name="III czytanie",
            decision="uchwalono",
            stage_date=NOW.date(),
            pdf_sha256="a" * 64,
            pdf_text_sha256="b" * 64,
            selection_id=selection.selection_id,
            edition_id=edition.edition_id,
        )
        legacy = CandidateDispositionTrace.from_decisions(
            document,
            (
                CandidateDisposition(
                    phase="fallback",
                    index=0,
                    outcome="selected",
                    reason="first_acceptable_candidate",
                    observed_sha256="a" * 64,
                ),
            ),
            result="selected",
            reason="first_acceptable_candidate",
            selection_state=selection.selection.state,
        )
        trace = CandidateDispositionTrace.model_validate(
            {**legacy.model_dump(), "sejm_adopted_text": proof}
        )
        repo.save_candidate_disposition_trace(source.observation_id, trace)
        checkpoint = repo.dump()
        repo.restore(checkpoint)
        assert repo.get_candidate_disposition_trace(trace.trace_id) == trace
        assert repo.dump() == checkpoint

        other = source.model_copy(update={"number": "2112"})
        repo.save_source_observation(selection, other)
        before = repo.dump()
        with pytest.raises(ValueError, match="candidate_trace_adoption_parent_mismatch"):
            repo.save_candidate_disposition_trace(other.observation_id, trace)
        assert repo.dump() == before
