import hashlib
import json
from dataclasses import replace
from typing import Literal

import pytest
from pydantic import ValidationError

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import BatchIntent, TextDocument
from lexinform.models.block_input import (
    BlockCopyRun,
    BlockCopyWitness,
    PreparedBlockCompanion,
    PreparedBlockCopy,
)
from lexinform.models.extractions import ExtractedPartSummary, ExtractionResult
from lexinform.models.input_receipts import PrimaryInputReceipt
from lexinform.pdf_projection import raw_pdf_page_span
from lexinform.services.document_history import DocumentSourceRead, record_source_read
from lexinform.services.documents import TextLoader
from lexinform.services.input_receipts import capture_primary_input_receipt
from lexinform.text_projection import SourcePiece, project_pieces
from tests.conftest import FIXTURES
from tests.fakes import FakeSejmGateway
from tests.unit.test_input_receipts import NOW, meta, request
from tests.unit.test_odt_blocks import odt_document, router


def native_intent(
    repo: SqliteBillRepository, kind: Literal["pdf", "odt"]
) -> tuple[BatchIntent, ExtractedPartSummary]:
    body = (
        (FIXTURES / "print_3039.pdf").read_bytes()
        if kind == "pdf"
        else odt_document(
            "<text:p>USTAWA Art. 1. Cudzoziemiec otrzymuje 300 zł. "
            + "Przepis ustawy. " * 80
            + "</text:p>"
        )
    )
    reader = router()
    physical = reader.extract_parts(body)
    gateway = FakeSejmGateway(files={"https://api.test/native": body})
    loader = TextLoader({"api.test": gateway.download}, reader, max_bytes=2_000_000)
    document = TextDocument(url="https://api.test/native", kind="print")
    chosen, loaded = loader.select_document(document)
    source = loader.capture_source_read(document, chosen, loaded)
    assert isinstance(source, DocumentSourceRead), "known native fixture has a source observation"
    assert loaded.text is not None, "native fixtures have readable law text"
    (extraction,) = source.extractions
    (summary,) = extraction.selected_parts
    if kind == "pdf":
        native = physical.pdf_pages[0].extraction
        page = native.pages[0]
        span = raw_pdf_page_span(page.text, 0, 1)
        assert span is not None and page.text[span[0] : span[1]] == loaded.text[:1]
        start, end = span
        block_id = page.block_id
        block_text = page.text
        copied = loaded.text[:1]
        xml = None
    else:
        odt = physical.odt_blocks[0].extraction
        block = odt.blocks[0]
        start, end = 0, len(block.text)
        block_id = block.block_id
        block_text = block.text
        copied = block.text
        xml = odt.xml_sha256
    witness = BlockCopyWitness(
        profile="native-block-copies-v1",
        coordinates=summary.coordinates,
        text_view=summary.text_view,
        manifest_sha256=summary.manifest_sha256,
        notes_manifest_sha256=None,
        xml_sha256=xml,
        part_sha256=summary.sha256,
        text_sha256=summary.text_sha256,
        text_chars=summary.text_chars,
        runs=(
            BlockCopyRun(
                source_start=0,
                source_end=len(copied),
                block_id=block_id,
                block_start=start,
                block_end=end,
                block_chars=len(block_text),
                block_text_sha256=hashlib.sha256(block_text.encode()).hexdigest(),
                text_sha256=hashlib.sha256(copied.encode()).hexdigest(),
            ),
        ),
    )
    enriched = ExtractionResult.model_validate(
        {
            **extraction.model_dump(),
            "selected_parts": (summary.model_copy(update={"block_copy_witness": witness}),),
        }
    )
    source = replace(source, extractions=(enriched,))
    record_source_read(repo, source, term=10, number="3039", purpose="analysis", recorded_at=NOW)
    frozen = request(loaded.text)
    projection = project_pieces(loaded.text, (SourcePiece(loaded.text, 0),))
    receipt = capture_primary_input_receipt(frozen, loaded.selection, projection, source)
    assert receipt is not None, "native fixture provides the known extraction and body parents"
    row = PreparedBlockCopy(
        start=0,
        end=len(copied),
        part_index=0,
        extraction_id=enriched.result_id,
        physical_index=summary.physical_index,
        witness_id=witness.witness_id,
        run_index=0,
        text_sha256=witness.runs[0].text_sha256,
    )
    companion = PreparedBlockCompanion(
        retention="complete",
        observed_rows=1,
        rows_sha256=hashlib.sha256(("[" + row.model_dump_json() + "]").encode()).hexdigest(),
        rows=(row,),
    )
    receipt = PrimaryInputReceipt.model_validate(
        {**receipt.model_dump(), "block_companion": companion}
    )
    intent = BatchIntent.from_request(
        request=frozen,
        provider="openai",
        created_at=NOW,
        meta=meta().model_copy(
            update={
                "source_url": document.url,
                "input_chars": len(loaded.text),
                "text": loaded.text,
                "selection": loaded.selection.without_text(),
                "prepared_input_receipt": receipt,
            }
        ),
    )
    return intent, enriched.selected_parts[0]


@pytest.mark.parametrize("kind", ["pdf", "odt"])
def test_shared_native_companion_is_read_by_restore(
    repo: SqliteBillRepository, kind: Literal["pdf", "odt"]
) -> None:
    intent, _ = native_intent(repo, kind)
    repo.save_batch_intent(intent)
    frozen = repo.dump()
    for _ in range(2):
        repo.restore(frozen)
        assert repo.dump() == frozen and repo.list_queued_batch_intents() == [intent]


@pytest.mark.parametrize("fault", ["format", "page", "view", "word_parent", "noncanonical"])
def test_pdf_parent_rejects_foreign_coordinate_identity(
    repo: SqliteBillRepository, fault: str
) -> None:
    _, summary = native_intent(repo, "pdf")
    witness = summary.block_copy_witness
    assert witness is not None, "explicit native trial retains a block witness"
    raw = witness.model_dump()
    if fault == "format":
        with pytest.raises(ValidationError):
            ExtractedPartSummary.model_validate(
                {**summary.model_dump(), "format": "doc", "coordinates": "unknown"}
            )
        return
    if fault == "page":
        assert summary.block_count is not None, "PDF fixture retains its actual page count"
        raw["runs"] = (
            witness.runs[0].model_copy(
                update={"block_id": f"pdf-page:{summary.block_count + 1}:0"}
            ),
        )
    elif fault == "view":
        raw["text_view"] = "legacy-odt-body-v1"
    elif fault == "word_parent":
        raw["body_manifest_sha256"] = "0" * 64
    else:
        raw["runs"] = (witness.runs[0].model_copy(update={"block_id": "pdf-page:01:0"}),)
    with pytest.raises(ValidationError):
        changed = BlockCopyWitness.model_validate(raw)
        ExtractedPartSummary.model_validate({**summary.model_dump(), "block_copy_witness": changed})


def test_old_word_witness_serialization_and_id_are_byte_exact() -> None:
    copied = hashlib.sha256(b"X").hexdigest()
    values = {
        "profile": "word-plain-xml-copies-v1",
        "coordinates": "word-xml-plain-run-copy-v1",
        "manifest_sha256": "a" * 64,
        "body_manifest_sha256": "b" * 64,
        "notes_manifest_sha256": None,
        "xml_sha256": "c" * 64,
        "part_sha256": "d" * 64,
        "text_sha256": copied,
        "text_chars": 1,
        "runs": [
            {
                "source_start": 0,
                "source_end": 1,
                "block_id": "word-xml:word/document.xml:0",
                "block_start": 0,
                "block_end": 1,
                "block_text_sha256": copied,
                "block_chars": 1,
                "text_sha256": copied,
            }
        ],
    }
    encoded = json.dumps(values, separators=(",", ":"))
    loaded = BlockCopyWitness.model_validate_json(encoded)
    assert loaded.model_dump_json() == encoded
    assert loaded.witness_id == "block-copy-v1:" + hashlib.sha256(encoded.encode()).hexdigest()
    assert "text_view" not in loaded.model_dump()


@pytest.mark.parametrize(
    "block_id",
    [
        "pdf-page:0:0",
        "pdf-page:01:0",
        "pdf-page:1:1",
        "odt-xml:content.xml:body:01",
        "odt-xml:content.xml:body:",
        "doc-cp:0",
    ],
)
def test_unknown_and_noncanonical_native_ids_are_rejected(block_id: str) -> None:
    with pytest.raises(ValidationError):
        BlockCopyRun(
            source_start=0,
            source_end=1,
            block_id=block_id,
            block_start=0,
            block_end=1,
            block_chars=2,
            block_text_sha256="a" * 64,
            text_sha256="b" * 64,
        )


def test_native_block_runs_cannot_reverse_the_same_parent() -> None:
    first = BlockCopyRun(
        source_start=0,
        source_end=1,
        block_id="pdf-page:1:0",
        block_start=2,
        block_end=3,
        block_chars=4,
        block_text_sha256="a" * 64,
        text_sha256="b" * 64,
    )
    second = first.model_copy(
        update={"source_start": 1, "source_end": 2, "block_start": 0, "block_end": 1}
    )
    with pytest.raises(ValidationError, match="original order"):
        BlockCopyWitness(
            profile="native-block-copies-v1",
            coordinates="pdf-pages-v1",
            text_view="pdf-whitespace-v1",
            manifest_sha256="a" * 64,
            notes_manifest_sha256=None,
            part_sha256="b" * 64,
            text_sha256="c" * 64,
            text_chars=3,
            runs=(first, second),
        )
