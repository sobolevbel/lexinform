import tracemalloc
import xml.etree.ElementTree as ET
from dataclasses import replace

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.adapters.word_block_copies import PlainWordCopies
from lexinform.models import AnalysisQuestion
from lexinform.models.block_input import BlockCopyWitness
from lexinform.models.documents import WordDetailedExtraction
from lexinform.models.input_receipts import RclArchiveInputReceipt
from lexinform.services.input_receipts import with_block_companion
from tests.unit.test_physical_extraction import docx, extractor
from tests.unit.test_rcl_archive_input_receipt import captured_source, offered


def test_native_prefix_is_observed_without_reextracting_or_changing_native_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = extractor()
    raw = docx("USTAWA " * 100)
    physical = reader.extract_parts(raw)
    original = ET.fromstring
    calls = 0

    def parsed(text: bytes) -> ET.Element:
        nonlocal calls
        calls += 1
        return original(text)

    monkeypatch.setattr(ET, "fromstring", parsed)
    result = reader.extract_document_with_provenance(raw)
    assert result.document.text == physical.parts[0].text
    assert result.result.selected_parts[0].block_copy_witness is not None
    native = physical.word_blocks[0].extraction
    witness = result.result.selected_parts[0].block_copy_witness
    assert witness.body_manifest_sha256 == native.manifest_sha256
    assert witness.runs[0].source_end == len(result.document.text)
    assert calls == 1


def test_receipt_keeps_only_full_runs_and_omits_missing_witness(repo: SqliteBillRepository) -> None:
    intent, receipt = offered(repo)
    source, _ = captured_source()
    assert isinstance(intent.request.question, AnalysisQuestion), (
        "the fixture freezes primary input"
    )
    text = intent.request.question.ctx.text
    enriched = with_block_companion(receipt, source, text)
    assert isinstance(enriched, RclArchiveInputReceipt) and enriched.block_companion is not None
    assert enriched.request_sha256 == receipt.request_sha256
    assert enriched.block_companion.rows[0].end == len(text)
    assert with_block_companion(receipt, replace(source, extractions=()), text) is receipt
    clipped = receipt.model_copy(
        update={"segments": (receipt.segments[0].model_copy(update={"end": 20}),)}
    )
    assert with_block_companion(clipped, source, text) is clipped


def test_optional_capture_failure_keeps_native_output(monkeypatch: pytest.MonkeyPatch) -> None:
    from_value = extractor()
    raw = docx("USTAWA " * 100)
    expected = from_value.extract_parts(raw).parts[0].text

    def unavailable(
        self: PlainWordCopies, legacy: WordDetailedExtraction
    ) -> BlockCopyWitness | None:
        raise ValueError("injected optional capture failure")

    monkeypatch.setattr(PlainWordCopies, "finish", unavailable)
    actual = from_value.extract_document_with_provenance(raw)
    assert actual.document.text == expected
    assert actual.result.selected_parts[0].block_copy_witness is None


def test_fragment_fault_does_not_interrupt_legacy_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = extractor()
    raw = docx("USTAWA " * 100)
    expected = reader.extract_parts(raw).parts[0].text

    def broken(
        self: PlainWordCopies, paragraph: tuple[int, ...], text: str, *, plain: bool
    ) -> None:
        raise RuntimeError("injected fragment capture fault")

    monkeypatch.setattr(PlainWordCopies, "fragment", broken)
    result = reader.extract_document_with_provenance(raw)
    assert result.document.text == expected
    assert result.result.selected_parts[0].block_copy_witness is None


def test_large_fragment_capture_retains_only_streaming_hash_state() -> None:
    capture = PlainWordCopies()
    tracemalloc.start()
    try:
        for _ in range(100000):
            capture.fragment((0, 0), "USTAWA " * 100, plain=True)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 128_000


def test_optional_companion_failure_preserves_the_existing_receipt(
    repo: SqliteBillRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    intent, receipt = offered(repo)
    source, _ = captured_source()
    assert isinstance(intent.request.question, AnalysisQuestion), "fixture freezes primary text"

    def unavailable(self: BlockCopyWitness) -> str:
        raise RuntimeError("injected optional companion failure")

    monkeypatch.setattr(BlockCopyWitness, "witness_id", property(unavailable))
    assert with_block_companion(receipt, source, intent.request.question.ctx.text) is receipt
