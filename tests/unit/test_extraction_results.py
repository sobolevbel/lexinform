import hashlib
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tools.source_history_doctor import audit

from lexinform import extraction_records
from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.input_receipts import project_pdf_page_ranges
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    ArtifactMetadata,
    DocumentSelectionRecord,
    ExtractionResult,
    SelectionExtractionReference,
    SourceObservation,
    TextDocument,
)
from lexinform.models.block_input import MAX_BLOCK_INPUT_BYTES, MAX_BLOCK_INPUT_ROWS
from lexinform.models.extractions import (
    MAX_EXTRACTION_METADATA_BYTES,
    ObservedMemberBlockCopy,
    PdfPageMap,
    PdfPageRange,
)
from lexinform.models.prepared_input import PreparedTextSegment
from lexinform.services.document_history import DocumentSourceRead, record_source_read
from lexinform.services.documents import TextLoader
from tests.conftest import FIXTURES
from tests.fakes import FakeSejmGateway, FakeTextExtractor
from tests.unit.test_physical_extraction import archive, docx, extractor

NOW = datetime(2026, 10, 28, tzinfo=UTC)
URL = "https://api.test/draft.docx"
TEXT = "USTAWA\nArt. 1. Cudzoziemiec składa wniosek osobiście. " * 30


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[SqliteBillRepository]:
    with closing(repository(tmp_path)) as instance:
        yield instance


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


def test_thousands_of_blocks_preserve_metadata_and_witness_bounds() -> None:
    paragraphs = "</w:t></w:r></w:p><w:p><w:r><w:t>".join([TEXT] * 3000)
    reader = extractor()
    small = reader.extract_document_with_provenance(docx(TEXT)).result
    large = reader.extract_document_with_provenance(docx(paragraphs)).result

    assert large.selected_parts[0].block_count == 3000
    assert large.output_chars > 1_000_000
    exclude = {"selected_parts": {"__all__": {"block_copy_witness"}}}
    assert (
        len(large.model_dump_json(exclude=exclude).encode())
        - len(small.model_dump_json(exclude=exclude).encode())
        < 32
    )
    witness = large.selected_parts[0].block_copy_witness
    assert witness is not None, "the native Word paragraphs supply copy evidence"
    assert len(witness.runs) <= MAX_BLOCK_INPUT_ROWS
    assert len(witness.model_dump_json().encode()) <= MAX_BLOCK_INPUT_BYTES
    assert len(large.model_dump_json().encode()) < MAX_EXTRACTION_METADATA_BYTES
    assert large.physical_manifest_sha256 != small.physical_manifest_sha256


def test_selected_bill_sample_is_separately_bounded_and_bound_to_its_summary() -> None:
    paragraphs = "</w:t></w:r></w:p><w:p><w:r><w:t>".join(
        f"Art. {i}. Tekst niepowtarzalnego przepisu {i}." for i in range(1, 5)
    )
    bill_bytes = docx(paragraphs)
    data = archive({"projekt.docx": bill_bytes, "osr.docx": docx("OSR " + TEXT)})
    original = extractor().extract_document_with_provenance(data).result
    bill, osr = sorted(
        original.selected_parts,
        key=lambda part: part.sha256 != hashlib.sha256(bill_bytes).hexdigest(),
    )
    native = bill.block_copy_witness
    assert native is not None, "the Word fixture supplies native block runs"
    assert len(native.runs) > 1, "distinct fixture paragraphs supply multiple copied runs"
    assert osr.block_copy_witness is not None, "the OSR fixture supplies independent evidence"
    sample = ObservedMemberBlockCopy(
        physical_index=bill.physical_index,
        witness=native.model_copy(update={"runs": native.runs[:1]}),
    )
    legacy = original.model_copy(
        update={
            "selected_parts": tuple(
                part.model_copy(update={"block_copy_witness": None}) if part == bill else part
                for part in original.selected_parts
            )
        }
    )

    admitted = ExtractionResult.model_validate(
        {**legacy.model_dump(), "selected_bill_block_sample": sample}
    )

    assert admitted.selected_parts == legacy.selected_parts
    assert ExtractionResult.model_validate_json(admitted.model_dump_json()) == admitted
    assert "selected_bill_block_sample" not in legacy.model_dump()
    for bad in (
        sample.model_copy(update={"witness": native}),
        sample.model_copy(update={"physical_index": osr.physical_index}),
    ):
        with pytest.raises(ValueError, match="one bounded selected slot without a witness"):
            ExtractionResult.model_validate(
                {**legacy.model_dump(), "selected_bill_block_sample": bad}
            )
    foreign = sample.model_copy(
        update={"witness": sample.witness.model_copy(update={"part_sha256": "0" * 64})}
    )
    with pytest.raises(ValueError, match="differs from its native extracted part"):
        ExtractionResult.model_validate(
            {**legacy.model_dump(), "selected_bill_block_sample": foreign}
        )


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
    repo: SqliteBillRepository,
    tmp_path: Path,
) -> None:
    data = docx(TEXT)
    other = URL + "?alias=1"
    gateway = FakeSejmGateway(files={URL: data, other: data})
    loader = TextLoader({"api.test": gateway.download}, extractor(), max_bytes=100_000)
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
    with closing(sqlite3.connect(tmp_path / "state.db")) as connection, connection:
        assert connection.execute("SELECT count(*) FROM extraction_results").fetchone() == (1,)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()


def test_parent_validation_and_caller_rollback_prevent_dangling_results(
    repo: SqliteBillRepository,
) -> None:
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


def test_result_link_refuses_an_unrelated_outer_body(repo: SqliteBillRepository) -> None:
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


def test_corrupt_sql_result_identity_is_not_silently_accepted(
    repo: SqliteBillRepository, tmp_path: Path
) -> None:
    result = extractor().extract_document_with_provenance(docx(TEXT)).result
    repo.save_artifact_metadata(
        ArtifactMetadata(sha256=result.artifact_sha256, byte_size=1, recorded_at=NOW)
    )
    repo.save_extraction_result(result, recorded_at=NOW)
    with closing(sqlite3.connect(tmp_path / "state.db")) as connection, connection:
        connection.execute("UPDATE extraction_results SET artifact_sha256=?", ("b" * 64,))

    with pytest.raises(ValueError, match="columns differ"):
        repo.get_extraction_result(result.result_id)


def test_pdf_page_map_round_trips_and_rejects_out_of_bounds_pages(
    repo: SqliteBillRepository,
) -> None:
    data = (FIXTURES / "print_3039.pdf").read_bytes()
    reader = DocumentTextExtractor(PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor())
    result = reader.extract_document_with_provenance(data).result
    (part,) = result.selected_parts
    part = part.model_copy(update={"pdf_page_map": None})
    result = result.model_copy(update={"selected_parts": (part,)})
    page = PdfPageRange(page=1, start=0, end=1, normalized_sha256="a" * 64)
    page_map = PdfPageMap(pages=(page,))
    mapped = ExtractionResult.model_validate(
        {
            **result.model_dump(),
            "selected_parts": (part.model_copy(update={"pdf_page_map": page_map}),),
        }
    )
    assert "pdf_page_map" not in result.model_dump_json()
    assert part.block_count is not None
    outside = page.model_copy(update={"page": part.block_count + 1})
    with pytest.raises(ValueError, match="matching retained physical page bounds"):
        ExtractionResult.model_validate(
            {
                **result.model_dump(),
                "selected_parts": (
                    part.model_copy(update={"pdf_page_map": PdfPageMap(pages=(outside,))}),
                ),
            }
        )
    with pytest.raises(ValueError, match="ordered and disjoint"):
        PdfPageMap(pages=(page, page))

    repo.save_artifact_metadata(
        ArtifactMetadata(sha256=result.artifact_sha256, byte_size=len(data), recorded_at=NOW)
    )
    repo.save_extraction_result(result, recorded_at=NOW)
    repo.save_extraction_result(mapped, recorded_at=NOW)
    dump = repo.dump()
    repo.restore(dump)
    assert repo.dump() == dump
    assert repo.get_extraction_result(result.result_id) == result
    assert repo.get_extraction_result(mapped.result_id) == mapped


def test_pdf_page_projection_uses_receipt_part_order_instead_of_physical_indices() -> None:
    data = (FIXTURES / "print_3039.pdf").read_bytes()
    reader = DocumentTextExtractor(PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor())
    (part,) = reader.extract_document_with_provenance(data).result.selected_parts
    page = PdfPageRange(page=1, start=2, end=7, normalized_sha256="a" * 64)
    mapped = part.model_copy(
        update={"pdf_page_map": PdfPageMap(pages=(page,)), "physical_index": 4}
    )
    unmapped = part.model_copy(update={"pdf_page_map": None, "physical_index": 7})
    segments = (
        PreparedTextSegment(
            start=0, end=4, origin="source", part_index=0, source_start=1, source_end=5
        ),
        PreparedTextSegment(start=4, end=5, origin="generated", system_origin="part_separator"),
        PreparedTextSegment(
            start=5, end=9, origin="source", part_index=1, source_start=5, source_end=9
        ),
    )

    assert project_pdf_page_ranges(segments, (unmapped, mapped)) == [
        {
            "part_index": 1,
            "page": 1,
            "request_span": [5, 7],
            "normalized_page_span": [3, 5],
            "normalized_page_sha256": "a" * 64,
        }
    ]


def test_pdf_extraction_coverage_separates_errors_omissions_and_corrupt_rows(
    tmp_path: Path,
) -> None:
    data = (FIXTURES / "print_3039.pdf").read_bytes()
    reader = DocumentTextExtractor(PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor())
    native = reader.extract_document_with_provenance(data).result
    (part,) = native.selected_parts
    page = PdfPageRange(page=1, start=0, end=1, normalized_sha256="a" * 64)
    mapped = native.model_copy(
        update={
            "selected_parts": (part.model_copy(update={"pdf_page_map": PdfPageMap(pages=(page,))}),)
        }
    )
    failed = native.model_copy(
        update={
            "selected_parts": (part.model_copy(update={"error_count": 2, "pdf_page_map": None}),)
        }
    )
    omitted = native.model_copy(update={"selected_parts": (), "omitted_selected_parts": 1})
    corrupt = native.model_copy(update={"rejected_count": native.rejected_count + 1})
    repo = repository(tmp_path)
    repo.save_artifact_metadata(
        ArtifactMetadata(sha256=native.artifact_sha256, byte_size=len(data), recorded_at=NOW)
    )
    for result in (mapped, failed, omitted, corrupt):
        repo.save_extraction_result(result, recorded_at=NOW)
    repo.close()
    path = tmp_path / "state.db"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "UPDATE extraction_results SET result_json='invalid' WHERE extraction_id=?",
            (corrupt.result_id,),
        )
    before = path.read_bytes()

    report = audit(path, include_pdf_extraction_coverage=True)

    coverage = report.pop("pdf_extraction_coverage")
    assert report == audit(path)
    assert coverage["available"] is True
    assert coverage["counts"] == {
        "valid_extraction_rows": 3,
        "invalid_extraction_rows": 1,
        "omitted_selected_parts_unknown_format": 1,
        "selected_pdf_parts": 2,
        "pdf_parts_with_reported_errors": 1,
        "pdf_parts_without_reported_errors": 1,
        "reported_pdf_extraction_errors": 2,
        "pdf_parts_with_page_map": 1,
    }
    assert path.read_bytes() == before
