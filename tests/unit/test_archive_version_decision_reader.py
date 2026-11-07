import json
import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest

from lexinform.adapters.candidate_trace_history import CandidateTraceRowError
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import CandidateDispositionTrace, TextDocument
from lexinform.models.candidate_trace import (
    ArchiveMemberDispositions,
    ArchiveVersionDecisionEvidence,
    PackedArchiveMemberDispositions,
    archive_member_rows_sha256,
)
from lexinform.physical_observations import PhysicalObservationCollector
from lexinform.services.document_history import DocumentSourceRead, record_source_read
from lexinform.services.documents import TextLoader
from lexinform.services.physical_history import PhysicalHistoryPersistence
from tests.fakes import FakeSejmGateway
from tests.unit.test_candidate_disposition_trace import NOW
from tests.unit.test_document_candidates import BILL
from tests.unit.test_physical_extraction import archive, docx, extractor

URL = "https://api.test/archive.zip"


def captured(
    repo: SqliteBillRepository,
) -> tuple[CandidateDispositionTrace, ArchiveVersionDecisionEvidence, str]:
    content = BILL + "Art. 2. Przepis stosuje się w postępowaniu. " * 12
    body = archive({"a.docx": docx(content), "b.docx": docx(content)})
    gateway = FakeSejmGateway(files={URL: body})
    loader = TextLoader(
        {"api.test": gateway.download},
        extractor(),
        max_bytes=100_000,
        physical_factory=PhysicalObservationCollector,
    )
    request = TextDocument(url=URL, kind="rcl", compare_versions=True)
    selected, file = loader.select_document(request)
    read = loader.capture_source_read(request, selected, file)
    assert isinstance(read, DocumentSourceRead), "the native fixture retains a document selection"
    trace = read.candidate_trace
    assert trace is not None and trace.archive_members is not None, (
        "fixture captures archive policy"
    )
    main, peer = trace.archive_members.rows[:2]
    assert main.physical_part_index is not None, "the selected main retains its exact slot"
    assert peer.reason == "version_preference", "the controlled second bill is a comparison peer"
    physical = PhysicalHistoryPersistence(loader)
    physical.persist_source_history(repo, recorded_at=NOW)
    first_dump = repo.dump()
    physical.persist_source_history(repo, recorded_at=NOW + timedelta(days=1))
    assert repo.dump() == first_dump
    (capture,) = repo.iter_physical_captures(main.root_sha256)
    record_source_read(
        repo, read, term=10, number="archive-fixture", purpose="analysis", recorded_at=NOW
    )
    (observation,) = repo.list_source_observations(10, "archive-fixture")
    return (
        trace,
        ArchiveVersionDecisionEvidence(
            capture_id=capture.capture_id,
            selected_slot=main.physical_part_index,
            peer=peer,
            order="selected_first",
        ),
        observation.observation_id,
    )


def attach(
    trace: CandidateDispositionTrace, evidence: ArchiveVersionDecisionEvidence
) -> CandidateDispositionTrace:
    return CandidateDispositionTrace.model_validate(
        {**json.loads(trace.model_dump_json()), "archive_version_decision": evidence}
    )


def test_archive_comparison_requires_durable_exact_member_parents(tmp_path: Path) -> None:
    with closing(SqliteBillRepository(tmp_path / "archive.db")) as repo:
        repo.migrate()
        trace, evidence, observation_id = captured(repo)

        version = attach(trace, evidence)
        repo.save_candidate_disposition_trace(observation_id, version)

        assert repo.get_candidate_disposition_trace(trace.trace_id) == trace
        assert repo.get_candidate_disposition_trace(version.trace_id) == version
        assert version.archive_members == trace.archive_members
        dump = repo.dump()
        repo.restore(dump)
        repo.restore(dump)
        assert repo.dump() == dump


@pytest.mark.parametrize(
    "change",
    [
        "DELETE FROM selection_extractions",
        "DELETE FROM physical_captures",
        "DELETE FROM artifact_reads",
    ],
)
def test_archive_comparison_parent_loss_is_detected_during_lookup_and_restore(
    tmp_path: Path, change: str
) -> None:
    path = tmp_path / "archive.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        trace, evidence, observation_id = captured(repo)
        version = attach(trace, evidence)
        repo.save_candidate_disposition_trace(observation_id, version)
        good = repo.dump()
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute(change)
        bad = repo.dump()
        with pytest.raises(CandidateTraceRowError, match="archive_version_parent_mismatch"):
            repo.get_candidate_disposition_trace(version.trace_id)
        repo.restore(good)
        with pytest.raises(CandidateTraceRowError, match="archive_version_parent_mismatch"):
            repo.restore(bad)
        assert repo.dump() == good


@pytest.mark.parametrize("field", ["capture_id", "peer_ordinal", "peer_leaf", "peer_text"])
def test_archive_comparison_rejects_a_validly_shaped_foreign_parent(
    tmp_path: Path, field: str
) -> None:
    with closing(SqliteBillRepository(tmp_path / "archive.db")) as repo:
        repo.migrate()
        trace, evidence, observation_id = captured(repo)
        members = trace.archive_members
        assert members is not None, "the original archive includes the fixture policy prefix"
        main_only = ArchiveMemberDispositions(
            walk=members.walk,
            retention="prefix",
            observed_rows=members.observed_rows,
            omitted_rows=(members.observed_rows or 0) - 1,
            rows_sha256=members.rows_sha256,
            unvisited=members.unvisited,
            rows=members.rows[:1],
        )
        trace = trace.model_copy(update={"archive_members": main_only})
        if field == "capture_id":
            evidence = evidence.model_copy(update={"capture_id": "b" * 64})
        else:
            changes: dict[str, dict[str, object]] = {
                "peer_ordinal": {"ordinals": (3,)},
                "peer_leaf": {"leaf_sha256": "b" * 64},
                "peer_text": {"text_sha256": "b" * 64},
            }
            evidence = evidence.model_copy(
                update={"peer": evidence.peer.model_copy(update=changes[field])}
            )
        version = attach(trace, evidence)
        before = repo.dump()

        with pytest.raises(CandidateTraceRowError, match="archive_version_parent_mismatch"):
            repo.save_candidate_disposition_trace(observation_id, version)

        assert repo.dump() == before


@pytest.mark.parametrize(
    "changes",
    [
        {"order": "peer_first"},
        {"date_filter": ("2026-11-03", "2026-11-03")},
        {"date_filter": ("2026-11-02", "2026-11-03")},
        {"selected_slot": 9},
    ],
)
def test_archive_comparison_rejects_inconsistent_rank_or_dates(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    with closing(SqliteBillRepository(tmp_path / "archive.db")) as repo:
        repo.migrate()
        trace, evidence, _ = captured(repo)
        with pytest.raises(ValueError):
            attach(trace, evidence.model_copy(update=changes))


@pytest.mark.parametrize("representation", ["prefix", "packed"])
def test_archive_comparison_cannot_reclassify_a_retained_member(
    tmp_path: Path, representation: str
) -> None:
    with closing(SqliteBillRepository(tmp_path / "archive.db")) as repo:
        repo.migrate()
        trace, evidence, _ = captured(repo)
        members = trace.archive_members
        assert members is not None, "the fixture retains its policy prefix"
        rows = tuple(
            row.model_copy(update={"reason": "rank_preference"}) if row == evidence.peer else row
            for row in members.rows
        )
        changed_members: ArchiveMemberDispositions | PackedArchiveMemberDispositions
        if representation == "packed":
            assert isinstance(members, PackedArchiveMemberDispositions), (
                "the native fixture retains all packed rows"
            )
            payload = members.model_dump(mode="json")
            position = members.rows.index(evidence.peer)
            # Packed v2 fixes rank_preference at reason code 3 in tuple position 7.
            payload["packed_rows"][position][7] = 3
            payload["rows_sha256"] = archive_member_rows_sha256(rows)
            changed_members = PackedArchiveMemberDispositions.model_validate(payload)
        else:
            retained = tuple(row for row in rows if row.stage == "policy")
            changed_members = ArchiveMemberDispositions(
                walk=members.walk,
                retention="prefix",
                observed_rows=members.observed_rows,
                omitted_rows=(members.observed_rows or 0) - len(retained),
                rows_sha256=members.rows_sha256,
                unvisited=members.unvisited,
                rows=retained,
            )
        assert evidence.peer not in changed_members.rows
        assert any(
            row.reason == "rank_preference" and row.ordinals == evidence.peer.ordinals
            for row in changed_members.rows
        )
        changed = trace.model_copy(update={"archive_members": changed_members})

        with pytest.raises(ValueError, match="retained member policy"):
            attach(changed, evidence)
