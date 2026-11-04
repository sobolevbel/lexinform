from pathlib import Path

from tools.qualify_archive_storage import measure_history, ordinary_source
from tools.replay_archive_dispositions import synthetic_inputs

from lexinform.models import TextDocument
from lexinform.services.document_history import DocumentSourceRead
from tests.unit.test_physical_callers import URL, loader_for


def test_actual_native_capture_strips_only_section_and_deduplicates_through_public_history(
    tmp_path: Path,
) -> None:
    reader, _ = loader_for(synthetic_inputs()["synthetic_duplicate_names"])
    document = TextDocument(url=URL, kind="rcl")
    selected, loaded = reader.select_document(document)
    source = reader.capture_source_read(document, selected, loaded)
    assert isinstance(source, DocumentSourceRead) and source.candidate_trace is not None, (
        "the public native loader retains this fixture's prospective archive trace"
    )
    original = source.candidate_trace.model_dump_json()
    ordinary = ordinary_source(source)
    assert isinstance(ordinary, DocumentSourceRead) and ordinary.candidate_trace is None, (
        "the public native loader retains this fixture's prospective archive trace"
    )
    assert source.candidate_trace.model_dump_json() == original
    assert ordinary.document == source.document and ordinary.selection == source.selection
    assert ordinary.artifacts == source.artifacts and ordinary.extractions == source.extractions
    actual = measure_history([source], tmp_path)
    assert actual["delta"]["sql_bytes"] > 0 and actual["delta"]["gzip_bytes"] > 0
    assert actual["archive"]["deduplicated"] and actual["archive"]["restore_exact"]
    assert actual["archive"]["doctor_counts"]["errors"] == 0
