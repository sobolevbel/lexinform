import hashlib
from contextlib import closing

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.document_editions import candidate_edition
from lexinform.models import (
    ArtifactMetadata,
    ArtifactReadReceipt,
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
    RclOriginalProposal,
)
from tests.unit.test_input_receipts import NOW
from tests.unit.test_physical_extraction import docx, extractor

BASE_URL = "https://legislacja.rcl.gov.pl/docs/2/12389752/1/2/dokument684251.zip"
PROPOSAL_URL = "https://legislacja.rcl.gov.pl/docs/2/12389752/1/2/dokument684485.zip"
BASE = "1" * 64
BASE_MEMBER = "2" * 64
PROPOSAL = "3" * 64
PROPOSAL_MEMBER = "4" * 64


@pytest.mark.parametrize(
    "mode",
    ["valid", "wrong_declared_part", "wrong_member", "wrong_edition", "wrong_original_read"],
)
def test_rcl_proposal_trace_binds_retained_parents(mode: str) -> None:
    document = TextDocument(
        url=BASE_URL,
        kind="rcl",
        compare_versions=True,
        amendment_urls=(PROPOSAL_URL,),
        version_basis=("rcl/12389752/stage/1", "Projekt ustawy"),
    )
    legacy = CandidateDispositionTrace.from_decisions(
        document,
        (
            CandidateDisposition(
                phase="version_comparison",
                index=0,
                outcome="selected",
                reason="version_choice",
                observed_sha256=BASE,
            ),
        ),
        result="selected",
        reason="version_choice",
        selection_state="unverified",
    )
    assert "rcl_original_proposal" not in legacy.model_dump()
    record = DocumentSelectionRecord.from_selection(
        DocumentSelection(
            version=document.version_basis,
            parts=(
                DocumentPart(
                    url=BASE_URL,
                    role="bill",
                    members=("ustawa.docx",),
                    container_sha256=BASE,
                    sha256=BASE_MEMBER,
                    text_sha256="a" * 64,
                ),
                DocumentPart(
                    url=PROPOSAL_URL,
                    role="self_amendment",
                    members=("autopoprawka.docx",),
                    container_sha256=PROPOSAL,
                    sha256=PROPOSAL_MEMBER,
                    text_sha256="b" * 64,
                ),
            ),
        )
    )
    edition = candidate_edition(record, recorded_at=NOW)
    assert edition is not None, "the fixture has one addressable bill member"
    original_bytes = docx("USTAWA\nArt. 1. Termin wynosi 30 dni.")
    original = extractor().extract_document_with_provenance(original_bytes).result
    proof = RclOriginalProposal(
        index=0,
        amendment_index=1 if mode == "wrong_declared_part" else 0,
        project_id=12389752,
        rm_number="RM-0610-73-24",
        term=10,
        original_print="672",
        original_extraction_id=original.result_id,
        base_sha256=BASE,
        base_member_sha256=BASE_MEMBER,
        proposal_sha256=PROPOSAL,
        proposal_member_sha256="d" * 64 if mode == "wrong_member" else PROPOSAL_MEMBER,
        comparison_text_sha256=hashlib.sha256(b"USTAWA\nArt. 1. Termin wynosi 30 dni.").hexdigest(),
        edition_id="edition-v1:" + "e" * 64 if mode == "wrong_edition" else edition.edition_id,
    )
    payload = {**legacy.model_dump(), "rcl_original_proposal": proof}
    if mode == "wrong_declared_part":
        with pytest.raises(ValueError, match="declared amendment"):
            CandidateDispositionTrace.model_validate(payload)
        return
    trace = CandidateDispositionTrace.model_validate(payload)
    source = SourceObservation(
        term=10,
        number="RCL/12389752",
        purpose="analysis",
        document=document,
        selection_id=record.selection_id,
        recorded_at=NOW,
    )
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        repo.save_source_observation(record, source)
        repo.save_selection_edition(record.selection_id, edition)
        for index, sha in enumerate((BASE, PROPOSAL)):
            repo.save_artifact_metadata(
                ArtifactMetadata(sha256=sha, byte_size=100, recorded_at=NOW)
            )
            repo.save_selection_artifact(
                SelectionArtifactReference(
                    selection_id=record.selection_id,
                    part_scope="parts",
                    part_index=index,
                    artifact_sha256=sha,
                )
            )
        repo.save_artifact_metadata(
            ArtifactMetadata(
                sha256=original.artifact_sha256, byte_size=len(original_bytes), recorded_at=NOW
            )
        )
        repo.save_extraction_result(original, recorded_at=NOW)
        repo.save_artifact_read(
            ArtifactReadReceipt(
                requested_url=proof.original_url
                + ("?other=1" if mode == "wrong_original_read" else ""),
                sha256=original.artifact_sha256,
                byte_size=len(original_bytes),
                recorded_at=NOW,
            )
        )
        repo.save_candidate_disposition_trace(source.observation_id, legacy)
        if mode != "valid":
            with pytest.raises(ValueError, match="candidate_trace_rcl_proposal_parent_mismatch"):
                repo.save_candidate_disposition_trace(source.observation_id, trace)
            return
        repo.save_candidate_disposition_trace(source.observation_id, trace)
        dump = repo.dump()
        repo.restore(dump)
        assert repo.dump() == dump
        assert repo.get_candidate_disposition_trace(trace.trace_id) == trace
        assert repo.get_candidate_disposition_trace(legacy.trace_id) == legacy
        broken = dump.replace(proof.original_url, proof.original_url + "?other=1")
        with pytest.raises(ValueError, match="candidate_trace_rcl_proposal_parent_mismatch"):
            repo.restore(broken)
        assert repo.dump() == dump
