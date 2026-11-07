from contextlib import closing

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    CandidateDispositionTrace,
    DocumentPart,
    DocumentSelection,
    TextDocument,
)
from lexinform.models.candidate_trace import (
    AdmittedNormativeAnnex,
    AdmittedNormativeAnnexes,
    CandidateDisposition,
)
from lexinform.models.document_history import DocumentSelectionRecord, SourceObservation
from tests.unit.test_input_receipts import NOW

MAIN = "https://legislacja.rcl.gov.pl/docs/main"
ANNEX = "https://legislacja.rcl.gov.pl/docs/annex"
FILE_SHA = "a" * 64
TEXT_SHA = "b" * 64


@pytest.mark.parametrize(
    "control", ["exact", "ordinal", "excluded_kind", "wrong_owner", "hash", "duplicate", "loss"]
)
def test_admitted_normative_annex_preserves_unverified_state_and_checks_exact_parent(
    control: str,
) -> None:
    document = TextDocument(url=MAIN, kind="rcl", normative_urls=(ANNEX,))
    old = CandidateDispositionTrace.from_decisions(
        document,
        (
            CandidateDisposition(
                phase="fallback", index=0, outcome="selected", reason="first_acceptable_candidate"
            ),
        ),
        result="selected",
        reason="first_acceptable_candidate",
        selection_state="unverified",
    )
    old_json = old.model_dump_json(exclude_defaults=True)
    old_id = old.trace_id
    assert "admitted_normative_annexes" not in old_json
    if control == "excluded_kind":
        with pytest.raises(ValueError, match="excluded by the normative annex policy"):
            AdmittedNormativeAnnex(
                normative_index=0,
                observed_kind="regulation",
                file_sha256=FILE_SHA,
                text_sha256=TEXT_SHA,
            )
        return
    witness = AdmittedNormativeAnnexes(
        phase="fallback",
        index=0,
        rows=(
            AdmittedNormativeAnnex(
                normative_index=int(control == "ordinal"),
                observed_kind="unknown",
                file_sha256=FILE_SHA,
                text_sha256="c" * 64 if control == "hash" else TEXT_SHA,
            ),
        ),
    )
    payload = {**old.model_dump(), "admitted_normative_annexes": witness}
    if control == "ordinal":
        with pytest.raises(ValueError, match="exact URL namespace"):
            CandidateDispositionTrace.model_validate(payload)
        return
    trace = CandidateDispositionTrace.model_validate(payload)
    assert trace.selection_state == "unverified"
    assert not trace.is_routine_singleton
    part = DocumentPart(
        url=ANNEX,
        role="normative_annex",
        sha256=FILE_SHA,
        text_sha256=TEXT_SHA,
        text_end=20,
        losses=("truncated",) if control == "loss" else (),
    )
    selection = DocumentSelectionRecord.from_selection(
        DocumentSelection(
            state="unverified",
            parts=(part, part) if control == "duplicate" else (part,),
        )
    )
    observation = SourceObservation(
        term=10,
        number="RCL/123",
        purpose="analysis",
        document=document.model_copy(update={"url": f"{MAIN}-other"})
        if control == "wrong_owner"
        else document,
        selection_id=selection.selection_id,
        recorded_at=NOW,
    )
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        repo.save_source_observation(selection, observation)
        repo.save_candidate_disposition_trace(observation.observation_id, old)
        baseline = repo.dump()
        if control != "exact":
            with pytest.raises(
                ValueError, match="candidate_trace_admitted_normative_parent_mismatch"
            ):
                repo.save_candidate_disposition_trace(observation.observation_id, trace)
            assert repo.dump() == baseline
            return
        repo.save_candidate_disposition_trace(observation.observation_id, trace)
        checkpoint = repo.dump()
        for _ in range(2):
            repo.restore(checkpoint)
            assert repo.dump() == checkpoint
            assert repo.get_candidate_disposition_trace(old_id) == old
            assert repo.get_candidate_disposition_trace(trace.trace_id) == trace
        assert old.trace_id == old_id and old.model_dump_json(exclude_defaults=True) == old_json
