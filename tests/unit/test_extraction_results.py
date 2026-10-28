import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lexinform import extraction_records
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    ArtifactMetadata,
    DocumentSelectionRecord,
    ExtractionResult,
    SelectionExtractionReference,
    SourceObservation,
    TextDocument,
)
from lexinform.models.extractions import MAX_EXTRACTION_METADATA_BYTES
from lexinform.services.document_history import DocumentSourceRead, record_source_read
from lexinform.services.documents import TextLoader
from tests.fakes import FakeSejmGateway, FakeTextExtractor
from tests.unit.test_physical_extraction import archive, docx, extractor

NOW = datetime(2026, 10, 28, tzinfo=UTC)
URL = "https://api.test/draft.docx"
TEXT = "USTAWA\nArt. 1. Cudzoziemiec składa wniosek osobiście. " * 30


def repository(tmp_path: Path) -> SqliteBillRepository:
    repo = SqliteBillRepository(tmp_path / "state.db")
    repo.migrate()
    return repo


def test_provenance_preserves_legacy_text_without_retaining_block_bodies() -> None:
    data = docx(TEXT)
    reader = extractor()

    old = reader.extract_document(data)
    observed = reader.extract_document_with_provenance(data)

    assert observed.document == old
    result = observed.result
    assert result.artifact_sha256 == hashlib.sha256(data).hexdigest()
    assert result.output_text_sha256 == hashlib.sha256(old.text.encode()).hexdigest()
    assert result.selected_parts[0].coordinates == "word-blocks-v1"
    assert result.selected_parts[0].block_count == 1
    assert result.bodies == "not_retained" and result.input_mapping == "unmapped"
    assert result.physical_coordinates == "manifest_not_retained"
    assert result.completeness == "not_asserted"
    assert TEXT[:100] not in result.model_dump_json()
    assert all(part.source_file_sha256 for part in result.components)
    assert ExtractionResult.model_validate_json(result.model_dump_json()) == result


def test_thousands_of_blocks_change_counts_without_expanding_persisted_metadata() -> None:
    paragraphs = "</w:t></w:r></w:p><w:p><w:r><w:t>".join([TEXT] * 3000)
    reader = extractor()
    small = reader.extract_document_with_provenance(docx(TEXT)).result
    large = reader.extract_document_with_provenance(docx(paragraphs)).result

    assert large.selected_parts[0].block_count == 3000
    assert large.output_chars > 1_000_000
    assert len(large.model_dump_json().encode()) - len(small.model_dump_json().encode()) < 32
    assert len(large.model_dump_json().encode()) < MAX_EXTRACTION_METADATA_BYTES
    assert large.physical_manifest_sha256 != small.physical_manifest_sha256


def test_archive_summaries_are_capped_without_losing_selected_document_text() -> None:
    data = archive(
        {
            "projekt.docx": docx(TEXT),
            **{
                f"zalacznik-{i}.docx": docx(f"Załącznik nr {i} do ustawy\nDane {i}")
                for i in range(40)
            },
        }
    )
    reader = extractor()

    observed = reader.extract_document_with_provenance(data)

    assert observed.document == reader.extract_document(data)
    assert observed.result.physical_part_count == 41
    assert observed.result.selected_part_count == 41
    assert observed.result.omitted_selected_parts == 25
    assert len(observed.result.selected_parts) == 16
    assert len(observed.result.model_dump_json().encode()) <= MAX_EXTRACTION_METADATA_BYTES
    assert "projekt.docx" not in observed.result.model_dump_json()


def test_unknown_custom_coordinates_do_not_become_pdf_page_origins() -> None:
    observed = extractor().extract_document_with_provenance(b"%PDF-fake")

    assert observed.result.selected_parts[0].coordinates == "unknown"
    assert observed.result.selected_parts[0].block_count is None


def test_metadata_byte_budget_omits_summaries_without_changing_the_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = archive({"projekt.docx": docx(TEXT), "uzasadnienie.docx": docx("UZASADNIENIE\n" + TEXT)})
    reader = extractor()
    before = reader.extract_document(data)
    monkeypatch.setattr(extraction_records, "MAX_EXTRACTION_METADATA_BYTES", 3000)

    observed = reader.extract_document_with_provenance(data)

    assert observed.document == before
    assert observed.result.omitted_selected_parts > 0
    assert len(observed.result.model_dump_json().encode()) <= 3000


def test_new_limits_create_a_distinct_result_without_overwriting_physical_identity() -> None:
    data = docx(TEXT)
    original = extractor().extract_document_with_provenance(data)
    limited = extractor(max_member_bytes=100_000).extract_document_with_provenance(data)

    assert original.document == limited.document
    assert original.result.result_id != limited.result.result_id
    assert original.result.physical_manifest_sha256 == limited.result.physical_manifest_sha256


def test_custom_extractors_keep_the_legacy_loader_contract() -> None:
    data = docx(TEXT)
    gateway = FakeSejmGateway(files={URL: data})
    loader = TextLoader({"api.test": gateway.download}, FakeTextExtractor(TEXT), max_bytes=100_000)

    _, loaded = loader.select_document(TextDocument(url=URL, kind="print"))

    assert loaded.text == TEXT
    assert not loader.extractions_for_selection(loaded.selection)


def test_source_transaction_retains_one_result_across_urls_checks_and_restore(
    tmp_path: Path,
) -> None:
    data = docx(TEXT)
    other = URL + "?alias=1"
    gateway = FakeSejmGateway(files={URL: data, other: data})
    loader = TextLoader({"api.test": gateway.download}, extractor(), max_bytes=100_000)
    repo = repository(tmp_path)
    result_ids: set[str] = set()
    for url in (URL, other):
        document, loaded = loader.select_document(TextDocument(url=url, kind="print"))
        with repo.atomic():
            record_source_read(
                repo,
                DocumentSourceRead(
                    document,
                    DocumentSelectionRecord.from_selection(loaded.selection),
                    loader.artifacts_for_selection(loaded.selection),
                    reads=loader.reads_for_selection(loaded.selection),
                    extractions=loader.extractions_for_selection(loaded.selection),
                ),
                term=10,
                number="4100",
                purpose="analysis",
                recorded_at=NOW,
            )
        links = repo.list_selection_extractions(
            DocumentSelectionRecord.from_selection(loaded.selection).selection_id
        )
        assert len(links) == 1
        result_ids.add(links[0].extraction_id)
    assert len(result_ids) == 1
    result_id = result_ids.pop()
    retained = repo.get_extraction_result(result_id)
    assert retained is not None, "both source observations link their recorded extraction"
    before = repo.dump()
    repo.save_extraction_result(retained, recorded_at=NOW + timedelta(days=30))
    assert repo.dump() == before
    repo.restore(before)
    assert repo.get_extraction_result(result_id) == retained
    with sqlite3.connect(tmp_path / "state.db") as connection:
        assert connection.execute("SELECT count(*) FROM extraction_results").fetchone() == (1,)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()


def test_parent_validation_and_caller_rollback_prevent_dangling_results(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    observed = extractor().extract_document_with_provenance(docx(TEXT))
    result = observed.result
    before = repo.dump()

    with pytest.raises(ValueError, match="complete-body"):
        repo.save_extraction_result(result, recorded_at=NOW)
    with pytest.raises(RuntimeError, match="caller"), repo.atomic():
        repo.save_artifact_metadata(
            ArtifactMetadata(sha256=result.artifact_sha256, byte_size=1, recorded_at=NOW)
        )
        repo.save_extraction_result(result, recorded_at=NOW)
        raise RuntimeError("caller failed")

    assert repo.dump() == before


def test_result_link_refuses_an_unrelated_outer_body(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    observed = extractor().extract_document_with_provenance(docx(TEXT))
    result = observed.result
    repo.save_artifact_metadata(
        ArtifactMetadata(sha256=result.artifact_sha256, byte_size=1, recorded_at=NOW)
    )
    repo.save_extraction_result(result, recorded_at=NOW)
    selected = DocumentSelectionRecord.from_selection(observed.document.selection)
    repo.save_source_observation(
        selected,
        SourceObservation(
            term=10,
            number="4100",
            purpose="analysis",
            document=TextDocument(url=URL, kind="print"),
            selection_id=selected.selection_id,
            recorded_at=NOW,
        ),
    )
    with pytest.raises(ValueError, match="exact outer body"):
        repo.save_selection_extraction(
            SelectionExtractionReference(
                selection_id=selected.selection_id,
                extraction_id=result.result_id,
                artifact_sha256="b" * 64,
            )
        )


def test_corrupt_sql_result_identity_is_not_silently_accepted(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    result = extractor().extract_document_with_provenance(docx(TEXT)).result
    repo.save_artifact_metadata(
        ArtifactMetadata(sha256=result.artifact_sha256, byte_size=1, recorded_at=NOW)
    )
    repo.save_extraction_result(result, recorded_at=NOW)
    with sqlite3.connect(tmp_path / "state.db") as connection:
        connection.execute("UPDATE extraction_results SET artifact_sha256=?", ("b" * 64,))

    with pytest.raises(ValueError, match="columns differ"):
        repo.get_extraction_result(result.result_id)
