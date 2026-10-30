import datetime as dt
import hashlib
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

import pytest
from tools.source_history_doctor import audit

from lexinform.adapters.source_catalog_attempts import SourceCatalogRowError
from lexinform.adapters.sqlite_repo import MIGRATIONS, SqliteBillRepository
from lexinform.metadata_urls import METADATA_URL_POLICY_VERSION
from lexinform.models import ArtifactMetadata, ArtifactReadReceipt, ProcessSummary, TextDocument
from lexinform.models.source_catalog_attempts import SourceCatalogAttempt, SourceCatalogSnapshot
from lexinform.models.source_omissions import (
    BillOmissionOwner,
    SourceOmissionParent,
    SourceReferenceOmissionOwner,
)
from tests.unit.test_physical_extraction import docx, extractor

NOW = dt.datetime(2026, 10, 30, tzinfo=dt.UTC)
OWNER = SourceReferenceOmissionOwner(term=10, number="RPW/29075/2026", purpose="analysis")
BODY = "a" * 64
OTHER = "b" * 64


def snapshot() -> SourceCatalogSnapshot:
    return SourceCatalogSnapshot.of(TextDocument(url="https://source.test/bill.pdf", kind="print"))


def populated(repo: SqliteBillRepository) -> tuple[SourceCatalogSnapshot, SourceCatalogAttempt]:
    catalog = snapshot()
    repo.save_source_catalog_snapshot(catalog, recorded_at=NOW)
    repo.save_artifact_metadata(ArtifactMetadata(sha256=BODY, byte_size=100, recorded_at=NOW))
    receipt = ArtifactReadReceipt(
        sha256=BODY, byte_size=100, requested_url="https://source.test/bill.pdf", recorded_at=NOW
    )
    repo.save_artifact_read(receipt)
    attempt = SourceCatalogAttempt.of(
        OWNER,
        catalog.catalog_id,
        "ValueError",
        (SourceOmissionParent(artifact_sha256=BODY, artifact_read_id=receipt.read_id),),
    )
    repo.save_source_catalog_attempt(attempt, recorded_at=NOW)
    return catalog, attempt


def test_catalog_retains_complete_ordered_safe_tree_and_quiet_shared_attempts(
    repo: SqliteBillRepository,
) -> None:
    doc = TextDocument(
        url="https://source.test/root",
        kind="print",
        alternatives=(
            TextDocument(
                url="https://source.test/a",
                kind="print",
                alternatives=(TextDocument(url="https://source.test/aa", kind="print"),),
            ),
            TextDocument(url="https://source.test/b", kind="print"),
        ),
    )
    catalog = SourceCatalogSnapshot.of(doc)
    assert [d.address for d in catalog.descriptors] == [(), (0,), (0, 0), (1,)]
    assert catalog.coverage == "complete"
    repo.save_source_catalog_snapshot(catalog, recorded_at=NOW)
    for error in ("ValueError", "TimeoutError"):
        repo.save_source_catalog_attempt(
            SourceCatalogAttempt.of(OWNER, catalog.catalog_id, error), recorded_at=NOW
        )
    before = repo.dump()
    repo.save_source_catalog_snapshot(catalog, recorded_at=NOW + dt.timedelta(days=365))
    for attempt in repo.list_source_catalog_attempts():
        repo.save_source_catalog_attempt(attempt, recorded_at=NOW + dt.timedelta(days=365))
    assert repo.dump() == before
    assert all(
        a.recorded_at == NOW and a.selection == "unknown"
        for a in repo.list_source_catalog_attempts()
    )
    repo.restore(before)
    repo.restore(repo.dump())
    assert repo.dump() == before


def test_catalog_prefix_identity_binds_budget_and_exact_full_view() -> None:
    doc = TextDocument(
        url="https://source.test/root",
        kind="print",
        alternatives=tuple(
            TextDocument(url=f"https://source.test/{i}", kind="print") for i in range(100)
        ),
    )
    catalog = SourceCatalogSnapshot.of(doc)
    assert catalog.descriptor_count == 101 and catalog.coverage == "partial"
    assert len(catalog.descriptors) <= 32 and catalog.budget_bytes <= 8192
    different = SourceCatalogSnapshot.of(
        doc.model_copy(
            update={
                "alternatives": (
                    *doc.alternatives[:-1],
                    TextDocument(url="https://source.test/changed", kind="print"),
                )
            }
        )
    )
    assert (
        different.catalog_id != catalog.catalog_id and different.descriptors == catalog.descriptors
    )
    with pytest.raises(ValueError, match="identity"):
        SourceCatalogSnapshot.model_validate(
            catalog.model_dump() | {"descriptors": catalog.descriptors[:-1]}
        )


@pytest.mark.parametrize(
    "url",
    [
        "https://source.test/f?token=PRIVATE",
        "https://source.test/%0Afile",
        "https://source.test/\ud800",
    ],
)
def test_catalog_rejects_unsafe_fields_even_beyond_retained_prefix(url: str) -> None:
    doc = TextDocument(
        url="https://source.test/root",
        kind="print",
        alternatives=tuple(
            TextDocument(url=f"https://source.test/{i}", kind="print") for i in range(33)
        )
        + (TextDocument(url=url, kind="print"),),
    )
    with pytest.raises(ValueError, match="not admitted"):
        SourceCatalogSnapshot.of(doc)


def test_catalog_oversized_single_descriptor_is_explicit_hash_only() -> None:
    catalog = SourceCatalogSnapshot.of(
        TextDocument(url="https://source.test/root", kind="print", version_basis=("x" * 20000,))
    )
    assert (
        catalog.coverage == "partial" and catalog.descriptor_count == 1 and not catalog.descriptors
    )
    assert catalog.budget_bytes <= 8192


def test_attempt_known_parents_are_bounded_and_incomplete_manifest_remains_honest() -> None:
    parents = tuple(
        SourceOmissionParent(artifact_sha256=hashlib.sha256(str(i).encode()).hexdigest())
        for i in range(100)
    )
    attempt = SourceCatalogAttempt.of(OWNER, snapshot().catalog_id, "TimeoutError", parents)
    assert attempt.parent_count == 100 and attempt.parent_coverage == "partial"
    assert len(attempt.parents) <= 32 and attempt.budget_bytes <= 8192
    assert (
        SourceCatalogAttempt.of(OWNER, snapshot().catalog_id, "TimeoutError", parents[::-1])
        == attempt
    )
    with pytest.raises(ValueError):
        SourceCatalogAttempt.of(OWNER, snapshot().catalog_id, "PRIVATE error prose")


def test_attempt_requires_real_tracked_bill_but_never_enrols_reference(
    repo: SqliteBillRepository,
) -> None:
    catalog = snapshot()
    repo.save_source_catalog_snapshot(catalog, recorded_at=NOW)
    tracked = BillOmissionOwner(term=10, number="123", purpose="analysis")
    attempt = SourceCatalogAttempt.of(tracked, catalog.catalog_id, "ValueError")
    before = repo.dump()
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        repo.save_source_catalog_attempt(attempt, recorded_at=NOW)
    assert repo.dump() == before
    repo.upsert_summary(
        ProcessSummary(
            term=10, number="123", title="Project", document_type="projekt ustawy", change_date=NOW
        ),
        now=NOW,
    )
    repo.save_source_catalog_attempt(attempt, recorded_at=NOW)
    repo.save_source_catalog_attempt(
        SourceCatalogAttempt.of(OWNER, catalog.catalog_id, "TimeoutError"), recorded_at=NOW
    )
    assert repo.get(10, OWNER.number) is None and len(repo.list_source_catalog_attempts()) == 2


def test_catalog_attempt_extraction_link_requires_the_actual_body(
    repo: SqliteBillRepository,
) -> None:
    data = docx("USTAWA\nArt. 1. Cudzoziemiec uzyskuje zezwolenie na pobyt. " * 20)
    observed = extractor().extract_document_with_provenance(data)
    repo.save_artifact_metadata(
        ArtifactMetadata(
            sha256=observed.result.artifact_sha256, byte_size=len(data), recorded_at=NOW
        )
    )
    repo.save_extraction_result(observed.result, recorded_at=NOW)
    catalog = snapshot()
    repo.save_source_catalog_snapshot(catalog, recorded_at=NOW)
    parent = SourceOmissionParent(
        artifact_sha256=observed.result.artifact_sha256, extraction_id=observed.result.result_id
    )
    attempt = SourceCatalogAttempt.of(OWNER, catalog.catalog_id, "ValueError", (parent,))
    repo.save_source_catalog_attempt(attempt, recorded_at=NOW)
    assert repo.get_source_catalog_attempt(attempt.attempt_id) is not None


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE source_catalog_snapshots SET descriptor_count=99",
        "UPDATE source_catalog_snapshots SET record_json=json_set(record_json,"
        " '$.descriptors[0].document.cookie','PRIVATE')",
        "UPDATE source_catalog_attempts SET purpose='review'",
        "DELETE FROM source_catalog_snapshots",
        "UPDATE source_catalog_attempt_parents SET ordinal=1",
        "UPDATE artifacts SET byte_size='PRIVATE'",
        "UPDATE artifact_reads SET artifact_sha256='" + OTHER + "'",
        "DELETE FROM source_catalog_attempts",
        "DROP INDEX ix_source_attempt_read",
    ],
)
def test_corrupt_catalog_attempt_is_rejected_before_restore_replaces_target(
    tmp_path: Path,
    sql: str,
) -> None:
    path = tmp_path / "damaged.db"
    repo = SqliteBillRepository(path)
    repo.migrate()
    _, attempt = populated(repo)
    repo.save_artifact_metadata(ArtifactMetadata(sha256=OTHER, byte_size=100, recorded_at=NOW))
    repo.close()
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(sql)
        connection.commit()
        bad = "\n".join(connection.iterdump()) + "\nPRAGMA user_version = 65;\n"
    diagnosis = audit(path)
    assert diagnosis["status"] == "failed"
    target = SqliteBillRepository(":memory:")
    target.migrate()
    populated(target)
    before = target.dump()
    with pytest.raises(ValueError):
        target.restore(bad)
    assert target.dump() == before
    if sql not in ("DELETE FROM source_catalog_attempts", "DROP INDEX ix_source_attempt_read"):
        reader = SqliteBillRepository(path)
        with pytest.raises(SourceCatalogRowError):
            reader.get_source_catalog_attempt(attempt.attempt_id)
        reader.close()
    target.close()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d | {"descriptor_count": False},
        lambda d: d | {"version": True},
        lambda d: d | {"coverage": "partial"},
        lambda d: d | {"recorded_at": NOW.replace(tzinfo=None)},
    ],
)
def test_catalog_model_rejects_false_coverage_and_timestamp(
    mutation: Callable[[dict[str, object]], dict[str, object]],
) -> None:
    with pytest.raises(ValueError):
        SourceCatalogSnapshot.model_validate(mutation(snapshot().model_dump()))


def test_restore_declared65_missing_feature_tables_preserves_target(
    repo: SqliteBillRepository,
) -> None:
    old = sqlite3.connect(":memory:")
    for migration in MIGRATIONS[:64]:
        old.executescript(migration)
    script = "\n".join(old.iterdump()) + "\nPRAGMA user_version = 65;\n"
    before = repo.dump()
    with pytest.raises(SourceCatalogRowError, match="table_missing"):
        repo.restore(script)
    assert repo.dump() == before
    old.close()


def test_no_body_attempt_is_positive_catalog_evidence_for_doctor(tmp_path: Path) -> None:
    path = tmp_path / "catalog.db"
    repo = SqliteBillRepository(path)
    repo.migrate()
    catalog = snapshot()
    repo.save_source_catalog_snapshot(catalog, recorded_at=NOW)
    repo.save_source_catalog_attempt(
        SourceCatalogAttempt.of(OWNER, catalog.catalog_id, "TimeoutError"), recorded_at=NOW
    )
    repo.close()
    result = audit(path)
    assert result["status"] == "recorded" and not result["findings"]


def test_catalog_deep_omitted_tail_does_not_claim_retained_addresses() -> None:
    document = TextDocument(url="https://source.test/last", kind="print")
    for index in range(80):
        document = TextDocument(
            url=f"https://source.test/{index}", kind="print", alternatives=(document,)
        )
    catalog = SourceCatalogSnapshot.of(document)
    assert catalog.descriptor_count == 81 and catalog.coverage == "partial"
    assert len(catalog.descriptors) <= 32 and catalog.budget_bytes <= 8192


def test_catalog_identity_explicitly_binds_actual_url_admission_policy() -> None:
    catalog = snapshot()
    assert catalog.url_policy_version == METADATA_URL_POLICY_VERSION
    with pytest.raises(ValueError):
        SourceCatalogSnapshot.model_validate(
            catalog.model_dump() | {"url_policy_version": "metadata-url-safety-v2"}
        )
    with pytest.raises(ValueError, match="identity"):
        SourceCatalogSnapshot.model_validate(
            catalog.model_dump() | {"catalog_id": "source-catalog-v1:" + "0" * 64}
        )
