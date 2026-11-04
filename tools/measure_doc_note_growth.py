"""Measure one controlled native DOC extraction without processing production or calling a model."""

import argparse
import datetime as dt
import gzip
import hashlib
from pathlib import Path

from tests.unit.test_doc_note_extraction import source_notes
from tests.unit.test_doc_note_physical_parts import extractor, ole_document

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import ArtifactMetadata, DocumentSelectionRecord
from tools.corpus_artifacts import write_json


def main(output: Path) -> None:
    data = ole_document(*source_notes())
    result = extractor().extract_document_with_provenance(data)
    now = dt.datetime(2026, 11, 4, tzinfo=dt.UTC)
    repository = SqliteBillRepository(":memory:")
    try:
        repository.migrate()
        with repository.atomic():
            repository.save_artifact_metadata(
                ArtifactMetadata(
                    sha256=result.result.artifact_sha256, byte_size=len(data), recorded_at=now
                )
            )
            repository.save_extraction_result(result.result, recorded_at=now)
        sql = repository.dump().encode()
    finally:
        repository.close()
    notes = [part.doc_notes for part in result.result.selected_parts if part.doc_notes is not None]
    record = {
        "scope": (
            "one synthetic OLE DOC with one actual source-checked footnote; "
            "no production or paid calls"
        ),
        "input_sha256": hashlib.sha256(data).hexdigest(),
        "input_bytes": len(data),
        "selection_id": DocumentSelectionRecord.from_selection(
            result.document.selection
        ).selection_id,
        "selected_text_sha256": hashlib.sha256(result.document.text.encode()).hexdigest(),
        "physical_manifest_sha256": result.result.physical_manifest_sha256,
        "output_text_sha256": result.result.output_text_sha256,
        "physical_part_count": result.result.physical_part_count,
        "selected_part_count": result.result.selected_part_count,
        "omitted_selected_parts": result.result.omitted_selected_parts,
        "extraction_json_bytes": len(result.result.model_dump_json().encode()),
        "sql_bytes": len(sql),
        "gzip_bytes": len(gzip.compress(sql, mtime=0)),
        "note_summary_parts": len(notes),
        "retained_note_links": sum(len(note.links) for note in notes),
    }
    write_json(output, record)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    main(parser.parse_args().output)
