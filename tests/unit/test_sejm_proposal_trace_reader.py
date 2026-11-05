import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from tools.source_history_doctor import audit

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.document_editions import candidate_edition
from lexinform.models import (
    ArtifactMetadata,
    ArtifactReadIdentity,
    DocumentPart,
    DocumentSelection,
    DocumentSelectionRecord,
    SelectionArtifactReference,
    SourceObservation,
    TextDocument,
)
from lexinform.models.candidate_trace import (
    CandidateDisposition,
    CandidateDispositionTrace,
    SejmOriginalProposal,
)
from lexinform.models.sejm import SejmProposalOrigin
from lexinform.services.document_history import record_document_source
from tests.unit.test_input_receipts import NOW

BASE_URL = "https://api.sejm.gov.pl/sejm/term10/prints/2320/2320.pdf"
PROPOSAL_URL = "https://api.sejm.gov.pl/sejm/term10/prints/2320-A/2320-A.pdf"
BASE_SHA = "4bb074013ffcd456f57352641fd753edc3a34666e37591c517f0d2e353bb67e5"
PROPOSAL_SHA = "b30ef9eb351b355597dd031d312ffe30716c8cdadf822d83c166fd0e000edc45"


def original() -> TextDocument:
    return TextDocument(
        url=BASE_URL,
        kind="print",
        amendment_urls=(PROPOSAL_URL,),
        version_basis=("sejm/10/print/2320",),
        compare_versions=True,
    )


def trace_of(document: TextDocument) -> CandidateDispositionTrace:
    return CandidateDispositionTrace.from_decisions(
        document,
        (
            CandidateDisposition(
                phase="version_comparison",
                index=0,
                outcome="selected",
                reason="version_choice",
                observed_sha256=BASE_SHA,
            ),
        ),
        result="selected",
        reason="version_choice",
        selection_state="unverified",
    )


def known_source(
    repo: SqliteBillRepository,
) -> tuple[SourceObservation, CandidateDispositionTrace, CandidateDispositionTrace]:
    repo.migrate()
    document = original()
    selection = DocumentSelectionRecord(
        selection=DocumentSelection(
            version=document.version_basis,
            parts=(
                DocumentPart(role="bill", url=BASE_URL, sha256=BASE_SHA, text_sha256="a" * 64),
                DocumentPart(
                    role="self_amendment",
                    url=PROPOSAL_URL,
                    sha256=PROPOSAL_SHA,
                    text_sha256="b" * 64,
                ),
            ),
        )
    )
    observation = SourceObservation(
        term=10,
        number="2320",
        purpose="analysis",
        document=document,
        selection_id=selection.selection_id,
        recorded_at=NOW,
    )
    repo.save_source_observation(selection, observation)
    edition = candidate_edition(selection, recorded_at=NOW)
    assert edition is not None, "the original bill is the sole candidate edition member"
    repo.save_selection_edition(selection.selection_id, edition)
    for index, part in enumerate(selection.selection.parts):
        repo.save_artifact_metadata(
            ArtifactMetadata(sha256=part.sha256, byte_size=1, recorded_at=NOW)
        )
        repo.save_selection_artifact(
            SelectionArtifactReference(
                selection_id=selection.selection_id,
                part_scope="parts",
                part_index=index,
                artifact_sha256=part.sha256,
            )
        )
    legacy = trace_of(document)
    proof = SejmOriginalProposal(
        term=10,
        parent_print="2320",
        proposal_print="2320-A",
        official_process_prints=("2320",),
        base_sha256=BASE_SHA,
        proposal_sha256=PROPOSAL_SHA,
        selection_id=selection.selection_id,
        edition_id=edition.edition_id,
    )
    new = CandidateDispositionTrace.model_validate(
        {**legacy.model_dump(), "sejm_original_proposal": proof}
    )
    return observation, legacy, new


def test_sejm_proposal_trace_round_trips_with_original_trace(tmp_path: Path) -> None:
    path = tmp_path / "source.db"
    with closing(SqliteBillRepository(path)) as repo:
        observation, legacy, trace = known_source(repo)
        repo.save_candidate_disposition_trace(observation.observation_id, legacy)
        repo.save_candidate_disposition_trace(observation.observation_id, trace)
        before = repo.dump()

        repo.restore(before)

        assert set(repo.list_candidate_disposition_traces(observation.observation_id)) == {
            legacy,
            trace,
        }
        assert repo.get_candidate_disposition_trace(trace.trace_id) == trace
        assert repo.dump() == before
        assert "sejm_original_proposal" not in legacy.model_dump_json(exclude_defaults=True)
        assert legacy.is_routine_singleton and not trace.is_routine_singleton
    assert audit(path, include_candidate_traces=True)["status"] == "recorded"


@pytest.mark.parametrize("broken", ["official_parent", "sole_url", "observed_original"])
def test_sejm_proposal_trace_rejects_incompatible_shape(broken: str) -> None:
    with closing(SqliteBillRepository(":memory:")) as repo:
        _, _, trace = known_source(repo)
    proof = trace.sejm_original_proposal
    assert proof is not None, "the controlled proposal proof is present"
    if broken == "official_parent":
        with pytest.raises(ValueError, match="one same-term parent"):
            SejmOriginalProposal.model_validate(
                {**proof.model_dump(), "official_process_prints": ("2321",)}
            )
        return
    if broken == "sole_url":
        request = original().model_copy(update={"amendment_urls": (PROPOSAL_URL, BASE_URL)})
        values = trace_of(request).model_dump()
    else:
        values = trace.model_dump()
        values["dispositions"][0]["observed_sha256"] = PROPOSAL_SHA
    with pytest.raises(ValueError, match="Sejm proposal requires"):
        CandidateDispositionTrace.model_validate({**values, "sejm_original_proposal": proof})


def test_sejm_proposal_trace_rejects_wrong_owner_at_save() -> None:
    with closing(SqliteBillRepository(":memory:")) as repo:
        source, _, trace = known_source(repo)
        selection = repo.get_document_selection(source.selection_id)
        assert selection is not None, "the original selection is retained"
        other = source.model_copy(update={"number": "2321"})
        repo.save_source_observation(selection, other)
        before = repo.dump()

        with pytest.raises(ValueError, match="candidate_trace_proposal_parent_mismatch"):
            repo.save_candidate_disposition_trace(other.observation_id, trace)

        assert repo.dump() == before


@pytest.mark.parametrize("broken", ["artifact", "edition", "source_link", "source_table"])
def test_sejm_proposal_trace_parent_corruption_is_visible_and_restore_is_atomic(
    tmp_path: Path, broken: str
) -> None:
    path = tmp_path / "source.db"
    with closing(SqliteBillRepository(path)) as repo:
        observation, _, trace = known_source(repo)
        repo.save_candidate_disposition_trace(observation.observation_id, trace)
        before = repo.dump()
        with closing(sqlite3.connect(path)) as connection, connection:
            if broken == "artifact":
                connection.execute(
                    "UPDATE selection_artifacts SET artifact_sha256=? WHERE part_index=1",
                    (BASE_SHA,),
                )
            elif broken == "edition":
                connection.execute("DELETE FROM selection_editions")
            elif broken == "source_link":
                connection.execute("DELETE FROM source_candidate_traces")
            else:
                connection.execute("DROP TABLE source_candidate_traces")
        bad = repo.dump()

        with pytest.raises(ValueError, match="candidate_trace_proposal_parent_mismatch"):
            repo.get_candidate_disposition_trace(trace.trace_id)
        snapshot = tmp_path / "snapshot.db"
        with closing(sqlite3.connect(snapshot)) as inspection:
            inspection.executescript(bad)
        findings = audit(snapshot, include_candidate_traces=True)["findings"]
        assert any(item["code"] == "candidate_trace_proposal_parent_mismatch" for item in findings)
        repo.restore(before)
        with pytest.raises(ValueError, match="candidate_trace_proposal_parent_mismatch"):
            repo.restore(bad)
        assert repo.dump() == before


@pytest.mark.parametrize("current_read", [True, False])
def test_native_writer_requires_exact_current_proposal_read(
    tmp_path: Path, current_read: bool
) -> None:
    with closing(SqliteBillRepository(tmp_path / "state.db")) as repo:
        observation, legacy, proof = known_source(repo)
        selection = repo.get_document_selection(observation.selection_id)
        assert selection is not None, "known_source stores the selection"
        artifacts = (
            ArtifactReadIdentity(sha256=BASE_SHA, byte_size=1, requested_url=BASE_URL),
            ArtifactReadIdentity(sha256=PROPOSAL_SHA, byte_size=1, requested_url=PROPOSAL_URL),
        )

        record_document_source(
            repo,
            selection,
            observation,
            artifacts,
            reads=artifacts if current_read else artifacts[:1],
            candidate_trace=legacy,
            sejm_proposal=SejmProposalOrigin(10, "2320", "2320-A", ("2320",)),
        )

        assert repo.list_candidate_disposition_traces(observation.observation_id) == (
            [proof] if current_read else []
        )
