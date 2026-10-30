import datetime as dt
import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest
from tools.source_history_doctor import audit

from lexinform.adapters.source_omissions import SourceOmissionRowError
from lexinform.adapters.sqlite_repo import MIGRATIONS, SqliteBillRepository
from lexinform.metadata_omissions import MetadataUrlOmissionCollector
from lexinform.models import (
    ArtifactMetadata,
    ArtifactReadReceipt,
    DocumentPart,
    DocumentSelection,
    DocumentSelectionRecord,
    ProcessSummary,
    SelectionArtifactReference,
    SelectionExtractionReference,
    SourceObservation,
    TextDocument,
)
from lexinform.models.source_omissions import (
    BillOmissionOwner,
    RclOmissionOwner,
    SenateOmissionOwner,
    SourceMetadataOmission,
    SourceOmissionParent,
    source_omission_canonical,
    source_omission_sha256,
)
from tests.unit.test_physical_extraction import docx, extractor

NOW = dt.datetime(2026, 10, 30, tzinfo=dt.UTC)
BODY = "a" * 64


def omission(
    owner: BillOmissionOwner | RclOmissionOwner | SenateOmissionOwner | None = None,
    *,
    parents: tuple[SourceOmissionParent, ...] = (),
) -> SourceMetadataOmission:
    collector = MetadataUrlOmissionCollector()
    collector.observe("invalid_unicode", requested_url_sha256=None)
    diagnostics = collector.snapshot()
    assert diagnostics is not None, "one observed omission produces diagnostics"
    return SourceMetadataOmission.of(
        owner=owner or BillOmissionOwner(term=10, number="123", purpose="analysis"),
        source_view={"url": "https://source.test/\ud800?token=PRIVATE"},
        diagnostics=diagnostics,
        parents=parents,
    )


@pytest.fixture
def repository(tmp_path: Path) -> Iterator[SqliteBillRepository]:
    repo = SqliteBillRepository(tmp_path / "state.db")
    repo.migrate()
    repo.upsert_summary(
        ProcessSummary(
            term=10, number="123", title="Projekt", document_type="projekt ustawy", change_date=NOW
        ),
        now=NOW,
    )
    repo.save_artifact_metadata(ArtifactMetadata(sha256=BODY, byte_size=100, recorded_at=NOW))
    yield repo
    repo.close()


@pytest.mark.parametrize(
    "owner",
    [
        BillOmissionOwner(term=10, number="123", purpose="prefilter"),
        SenateOmissionOwner(term=10, number="123", resolution_term=10, resolution_number="456"),
        RclOmissionOwner(project_id=12, stage_id=None, stage_number=None, letter_id=None),
    ],
)
def test_owner_omission_exact_first_time_repeat_restore(
    repository: SqliteBillRepository,
    owner: BillOmissionOwner | RclOmissionOwner | SenateOmissionOwner,
) -> None:
    fact = omission(owner, parents=(SourceOmissionParent(artifact_sha256=BODY),))
    repository.save_source_metadata_omission(fact, recorded_at=NOW)
    stored = repository.get_source_metadata_omission(fact.omission_id)
    assert stored is not None, "saving the owner-bound omission retains its record"
    assert stored.recorded_at == NOW and stored.parents[0].source_relation == "unknown"
    assert (
        "PRIVATE" not in stored.model_dump_json() and "source.test" not in stored.model_dump_json()
    )
    before = repository.dump()
    repository.save_source_metadata_omission(fact, recorded_at=NOW + dt.timedelta(days=365))
    assert repository.dump() == before
    repository.restore(before)
    repository.restore(before)
    assert repository.dump() == before
    assert repository.list_source_metadata_omissions() == [stored]


def test_canonical_ascii_hash_is_not_a_fictional_utf8_url_hash() -> None:
    fact = omission()
    assert fact.source_view_sha256 == source_omission_sha256(
        {"url": "https://source.test/\ud800?token=PRIVATE"}
    )
    assert fact.source_view_sha256 != source_omission_sha256(
        {"url": "https://source.test/\ud801?token=PRIVATE"}
    )
    assert fact.diagnostics.examples[0].requested_url_sha256 is None
    assert fact.source_view_encoding == "canonical-json-ascii-v1"
    assert fact.parent_scope == "known_available" and fact.parent_count == 0


def test_immutable_identity_binds_owner_reason_and_retained_parents() -> None:
    fact = omission()
    for key, value in (
        ("source_view_sha256", "b" * 64),
        ("owner", {"kind": "bill", "term": 10, "number": "456", "purpose": "analysis"}),
        ("parent_count", 1),
        ("parents_sha256", "c" * 64),
        ("version", True),
    ):
        payload = fact.model_dump(mode="json")
        payload[key] = value
        with pytest.raises(ValueError):
            SourceMetadataOmission.model_validate(payload)
    with pytest.raises(ValueError, match="full observed manifest"):
        SourceMetadataOmission.of(
            owner=fact.owner, source_view={}, diagnostics=fact.diagnostics, parent_count=1
        )


def test_missing_owner_and_parent_roll_back_without_partial_rows(
    repository: SqliteBillRepository,
) -> None:
    before = repository.dump()
    for fact in (
        omission(BillOmissionOwner(term=10, number="missing", purpose="analysis")),
        omission(parents=(SourceOmissionParent(artifact_sha256="b" * 64),)),
        omission(
            parents=(
                SourceOmissionParent(
                    artifact_sha256=BODY, extraction_id="extraction-v1:" + "b" * 64
                ),
            )
        ),
    ):
        with pytest.raises((ValueError, sqlite3.IntegrityError)):
            repository.save_source_metadata_omission(fact, recorded_at=NOW)
        assert repository.dump() == before


@pytest.mark.parametrize("corruption", ["owner", "parent", "artifact", "record"])
def test_get_doctor_restore_reject_corruption_preserving_target(
    tmp_path: Path, corruption: str
) -> None:
    path = tmp_path / "corrupt.db"
    repo = SqliteBillRepository(path)
    repo.migrate()
    fact = omission(
        RclOmissionOwner(project_id=12, stage_id=None, stage_number=0, letter_id=None),
        parents=(SourceOmissionParent(artifact_sha256=BODY),),
    )
    repo.save_artifact_metadata(ArtifactMetadata(sha256=BODY, byte_size=100, recorded_at=NOW))
    repo.save_source_metadata_omission(fact, recorded_at=NOW)
    good = repo.dump()
    repo.close()
    with closing(sqlite3.connect(path)) as connection, connection:
        if corruption == "owner":
            connection.execute("UPDATE source_metadata_omissions SET project_id=13")
        elif corruption == "parent":
            connection.execute("DELETE FROM source_omission_parents")
        elif corruption == "artifact":
            connection.execute("UPDATE artifacts SET byte_size='corrupt'")
        else:
            payload = json.loads(
                connection.execute("SELECT record_json FROM source_metadata_omissions").fetchone()[
                    0
                ]
            )
            payload["source_view_sha256"] = "d" * 64
            connection.execute(
                "UPDATE source_metadata_omissions SET record_json=?", (json.dumps(payload),)
            )
    damaged = SqliteBillRepository(path)
    with pytest.raises(SourceOmissionRowError):
        damaged.get_source_metadata_omission(fact.omission_id)
    broken = damaged.dump()
    damaged.close()
    assert audit(path)["status"] != "recorded"
    target = SqliteBillRepository(":memory:")
    target.migrate()
    target.restore(good)
    before = target.dump()
    with pytest.raises(SourceOmissionRowError):
        target.restore(broken)
    assert target.dump() == before
    target.close()


def test_reader_migration_is_append_only_and_inert(repository: SqliteBillRepository) -> None:
    assert len(MIGRATIONS) >= 63
    assert repository.list_source_metadata_omissions() == []
    assert repository.get_source_metadata_omission("source-omission-v1:" + "a" * 64) is None
    assert "source_metadata_omissions" not in "".join(MIGRATIONS[:62])


def test_partial_known_parent_manifest_is_explicit_and_bound() -> None:
    fact = omission()
    partial = SourceMetadataOmission.of(
        owner=fact.owner,
        source_view={"original": "\ud800"},
        diagnostics=fact.diagnostics,
        parent_count=2,
        parents_sha256="b" * 64,
    )
    assert partial.parent_coverage == "partial" and partial.parents == ()
    assert partial.parent_scope == "known_available" and partial.parent_count == 2
    assert partial.parents_sha256 == "b" * 64
    with pytest.raises(ValueError):
        SourceOmissionParent(artifact_sha256=BODY, source_relation="known")
    with pytest.raises(ValueError):
        SourceMetadataOmission.of(
            owner=fact.owner,
            source_view={},
            diagnostics=fact.diagnostics,
            parent_count=1,
            parents_sha256="",
        )


def test_parent_limit_and_naive_time_fail_before_any_write(
    repository: SqliteBillRepository,
) -> None:
    before = repository.dump()
    with pytest.raises(ValueError):
        omission(
            parents=tuple(SourceOmissionParent(artifact_sha256=f"{n:064x}") for n in range(33))
        )
    with pytest.raises(ValueError):
        repository.save_source_metadata_omission(omission(), recorded_at=NOW.replace(tzinfo=None))
    assert repository.dump() == before


def test_exact_envelope_repeat_uses_validated_first_timestamp(
    repository: SqliteBillRepository,
) -> None:
    parents = []
    for index in range(32):
        digest = f"{index:064x}"
        repository.save_artifact_metadata(
            ArtifactMetadata(sha256=digest, byte_size=100, recorded_at=NOW)
        )
        receipt = ArtifactReadReceipt(
            sha256=digest,
            byte_size=100,
            requested_url=f"https://source.test/{index}",
            recorded_at=NOW,
        )
        if index < 6:
            repository.save_artifact_read(receipt)
        parents.append(
            SourceOmissionParent(
                artifact_sha256=digest,
                artifact_read_id=receipt.read_id if index < 6 else None,
            )
        )
    fact = omission(parents=tuple(parents))
    payload = fact.model_dump(mode="json")
    payload["recorded_at"] = NOW.isoformat()
    difference = 8192 - len(source_omission_canonical(payload))
    assert 0 <= difference <= 125, "six known reads place this fixture near the envelope cap"
    number = "123" + "0" * difference
    payload["owner"]["number"] = number
    payload["omission_id"] = "source-omission-v1:" + source_omission_sha256(
        {key: value for key, value in payload.items() if key not in ("omission_id", "recorded_at")}
    )
    exact = SourceMetadataOmission.model_validate(payload)
    assert exact.budget_bytes == 8192
    repository.upsert_summary(
        ProcessSummary(
            term=10, number=number, title="Projekt", document_type="projekt ustawy", change_date=NOW
        ),
        now=NOW,
    )
    repository.save_source_metadata_omission(exact, recorded_at=NOW)
    before = repository.dump()
    repository.save_source_metadata_omission(exact, recorded_at=NOW.replace(microsecond=123456))
    assert repository.dump() == before


def test_known_extraction_parent_requires_its_actual_body(repository: SqliteBillRepository) -> None:
    data = docx("USTAWA Art. 1. Cudzoziemcy. " * 30)
    result = extractor().extract_document_with_provenance(data).result
    repository.save_artifact_metadata(
        ArtifactMetadata(sha256=result.artifact_sha256, byte_size=len(data), recorded_at=NOW)
    )
    repository.save_extraction_result(result, recorded_at=NOW)
    parent = SourceOmissionParent(
        artifact_sha256=result.artifact_sha256, extraction_id=result.result_id
    )
    fact = omission(parents=(parent,))
    repository.save_source_metadata_omission(fact, recorded_at=NOW)
    assert repository.get_source_metadata_omission(fact.omission_id) is not None
    before = repository.dump()
    wrong = omission(
        parents=(SourceOmissionParent(artifact_sha256=BODY, extraction_id=result.result_id),)
    )
    with pytest.raises(SourceOmissionRowError):
        repository.save_source_metadata_omission(wrong, recorded_at=NOW)
    assert repository.dump() == before


def test_known_source_parent_requires_exact_owner_purpose_and_body_relation(
    repository: SqliteBillRepository,
) -> None:
    selection = DocumentSelectionRecord.from_selection(
        DocumentSelection(
            parts=(
                DocumentPart(url="https://source.test/draft", sha256=BODY, container_sha256=BODY),
            )
        )
    )
    observation = SourceObservation(
        term=10,
        number="123",
        purpose="analysis",
        document=TextDocument(url="https://source.test/draft", kind="print"),
        selection_id=selection.selection_id,
        recorded_at=NOW,
    )
    repository.save_source_observation(selection, observation)
    parent = SourceOmissionParent(
        artifact_sha256=BODY,
        source_observation_id=observation.observation_id,
        source_relation="known",
    )
    fact = omission(parents=(parent,))
    with pytest.raises(SourceOmissionRowError):
        repository.save_source_metadata_omission(fact, recorded_at=NOW)
    repository.save_selection_artifact(
        SelectionArtifactReference(
            selection_id=selection.selection_id,
            part_scope="parts",
            part_index=0,
            artifact_sha256=BODY,
        )
    )
    repository.save_source_metadata_omission(fact, recorded_at=NOW)
    assert repository.get_source_metadata_omission(fact.omission_id) is not None
    before = repository.dump()
    for owner in (
        BillOmissionOwner(term=10, number="123", purpose="prefilter"),
        RclOmissionOwner(project_id=123, stage_id=None, stage_number=None, letter_id=None),
    ):
        with pytest.raises(SourceOmissionRowError):
            repository.save_source_metadata_omission(
                omission(owner, parents=(parent,)), recorded_at=NOW
            )
        assert repository.dump() == before


@pytest.mark.parametrize(
    "corruption",
    ["artifact_body", "artifact_index", "artifact_scope", "extraction_body", "extraction_id"],
)
def test_known_source_bridge_corruption_fails_save_get_doctor_and_restore(
    tmp_path: Path, corruption: str
) -> None:
    path = tmp_path / "source-bridge.db"
    repo = SqliteBillRepository(path)
    repo.migrate()
    repo.upsert_summary(
        ProcessSummary(
            term=10, number="123", title="Projekt", document_type="projekt ustawy", change_date=NOW
        ),
        now=NOW,
    )
    results = []
    for text in ("Pierwszy projekt", "Drugi projekt"):
        data = docx(text * 50)
        result = extractor().extract_document_with_provenance(data).result
        repo.save_artifact_metadata(
            ArtifactMetadata(sha256=result.artifact_sha256, byte_size=len(data), recorded_at=NOW)
        )
        repo.save_extraction_result(result, recorded_at=NOW)
        results.append(result)
    selected, other = results
    selection = DocumentSelectionRecord.from_selection(
        DocumentSelection(
            parts=(DocumentPart(url="https://source.test/draft", sha256=selected.artifact_sha256),)
        )
    )
    observation = SourceObservation(
        term=10,
        number="123",
        purpose="analysis",
        document=TextDocument(url="https://source.test/draft", kind="print"),
        selection_id=selection.selection_id,
        recorded_at=NOW,
    )
    repo.save_source_observation(selection, observation)
    if corruption.startswith("artifact"):
        repo.save_selection_artifact(
            SelectionArtifactReference(
                selection_id=selection.selection_id,
                part_scope="parts",
                part_index=0,
                artifact_sha256=selected.artifact_sha256,
            )
        )
    else:
        repo.save_selection_extraction(
            SelectionExtractionReference(
                selection_id=selection.selection_id,
                extraction_id=selected.result_id,
                artifact_sha256=selected.artifact_sha256,
            )
        )
    fact = omission(
        parents=(
            SourceOmissionParent(
                artifact_sha256=selected.artifact_sha256,
                source_observation_id=observation.observation_id,
                source_relation="known",
            ),
        )
    )
    repo.save_source_metadata_omission(fact, recorded_at=NOW)
    good = repo.dump()
    repo.close()
    with closing(sqlite3.connect(path)) as connection, connection:
        if corruption == "artifact_body":
            connection.execute(
                "UPDATE selection_artifacts SET artifact_sha256=?", (other.artifact_sha256,)
            )
        elif corruption == "artifact_index":
            connection.execute("UPDATE selection_artifacts SET part_index=9")
        elif corruption == "artifact_scope":
            connection.execute("UPDATE selection_artifacts SET part_scope='observed_parts'")
        elif corruption == "extraction_body":
            connection.execute(
                "UPDATE extraction_results SET artifact_sha256=? WHERE extraction_id=?",
                (other.artifact_sha256, selected.result_id),
            )
        else:
            connection.execute(
                "UPDATE selection_extractions SET extraction_id=?", (other.result_id,)
            )
    damaged = SqliteBillRepository(path)
    broken = damaged.dump()
    with pytest.raises(SourceOmissionRowError):
        damaged.get_source_metadata_omission(fact.omission_id)
    with pytest.raises(SourceOmissionRowError):
        damaged.save_source_metadata_omission(fact, recorded_at=NOW)
    new_fact = omission(
        parents=(
            SourceOmissionParent(
                artifact_sha256=other.artifact_sha256,
                source_observation_id=observation.observation_id,
                source_relation="known",
            ),
        )
    )
    with pytest.raises(SourceOmissionRowError):
        damaged.save_source_metadata_omission(new_fact, recorded_at=NOW)
    assert damaged.dump() == broken
    damaged.close()
    assert audit(path)["status"] != "recorded"
    target = SqliteBillRepository(":memory:")
    target.migrate()
    target.restore(good)
    before = target.dump()
    with pytest.raises(SourceOmissionRowError):
        target.restore(broken)
    assert target.dump() == before
    target.close()


@pytest.mark.parametrize(
    ("purpose", "kind", "accepted"),
    [
        ("prefilter", "senate_amendments", False),
        ("analysis", "print", False),
        ("analysis", "senate_amendments", True),
    ],
)
def test_senate_known_source_requires_actual_analysis_resolution_role(
    repository: SqliteBillRepository,
    purpose: str,
    kind: str,
    accepted: bool,
) -> None:
    selection = DocumentSelectionRecord.from_selection(
        DocumentSelection(parts=(DocumentPart(url="https://source.test/resolution", sha256=BODY),))
    )
    observation = SourceObservation.model_validate(
        dict(
            term=10,
            number="123",
            purpose=purpose,
            document=TextDocument(url="https://source.test/resolution", kind=kind),
            selection_id=selection.selection_id,
            recorded_at=NOW,
        )
    )
    repository.save_source_observation(selection, observation)
    repository.save_selection_artifact(
        SelectionArtifactReference(
            selection_id=selection.selection_id,
            part_scope="parts",
            part_index=0,
            artifact_sha256=BODY,
        )
    )
    fact = omission(
        SenateOmissionOwner(term=10, number="123", resolution_term=10, resolution_number="456"),
        parents=(
            SourceOmissionParent(
                artifact_sha256=BODY,
                source_observation_id=observation.observation_id,
                source_relation="known",
            ),
        ),
    )
    before = repository.dump()
    if accepted:
        repository.save_source_metadata_omission(fact, recorded_at=NOW)
        assert repository.get_source_metadata_omission(fact.omission_id) is not None
    else:
        with pytest.raises(SourceOmissionRowError):
            repository.save_source_metadata_omission(fact, recorded_at=NOW)
        assert repository.dump() == before
