import hashlib
import json
import zipfile
from pathlib import Path
from typing import IO, Literal
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from tools.replay_word_blocks import run, snapshot, verify_structure

from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.document_candidates import select_document_parts
from lexinform.models import (
    WordSemanticStructureExtraction,
    WordStructuredExtraction,
    WordStructuredParts,
)
from lexinform.models.word_structure import (
    SEMANTIC_BYTE_QUOTAS,
    semantic_event_quotas,
    word_envelope_bytes,
)
from lexinform.word_structure import WordStructureCollector
from tests.fakes import FakeTextExtractor
from tests.unit.test_physical_extraction import archive, docx
from tests.unit.test_word_structure import structured


def finish(collector: WordStructureCollector) -> WordSemanticStructureExtraction:
    legacy = DocxTextExtractor().extract_with_notes(docx("USTAWA"))
    value = collector.finish(legacy, {}, set())
    assert isinstance(value, WordSemanticStructureExtraction), "The semantic collector returns v2"
    return value


def test_recognized_flood_does_not_spend_semantic_prefixes_or_claim_full_stream() -> None:
    collector = WordStructureCollector(retention="semantic-first-v2")
    with collector.scope():
        for index in range(20000):
            collector.support((index,), "text", 0, "text")
        collector.block("paragraph", (20001,), "500 zł")
        collector.call("footnote", "absent", (20001, 0), (20001,), expanded=False)
    value = finish(collector)
    assert value.support_count == value.recognized_support[0].count == 20000
    assert len(value.blocks) == len(value.calls) == 1 and not value.support
    assert (value.blocks[0].order, value.calls[0].order) == (20000, 20001)
    assert value.blocks[0].ordinal == value.calls[0].ordinal == 0
    assert value.coverage == "semantic_complete" and value.support_coverage == "aggregated"
    assert value.observed_stream_sha256 is None
    assert WordSemanticStructureExtraction.model_validate_json(value.model_dump_json()) == value


@pytest.mark.parametrize("kind", ["block", "call", "support"])
def test_each_byte_limited_prefix_preserves_other_lanes_and_actual_totals(kind: str) -> None:
    collector = WordStructureCollector(retention="semantic-first-v2", max_events=65536)
    with collector.scope():
        for index in range(2500):
            if kind == "block":
                collector.block("paragraph", (index,), "block")
            elif kind == "call":
                collector.call("footnote", "x" * 256, (index, 0), (index,), expanded=False)
            else:
                collector.support((index,), "unsupported" * 20, 0, "ignored_leaf")
        collector.block("paragraph", (9000,), "later block")
        collector.call("footnote", "later", (9000, 0), (9000,), expanded=False)
        collector.support((9001,), "unknown", 0, "recursed", recognized=False)
    value = finish(collector)
    assert getattr(value, kind + "_limit") == "bytes"
    assert word_envelope_bytes(value.model_dump(mode="json")) <= 524288
    for lane, rows in (("block", value.blocks), ("call", value.calls), ("support", value.support)):
        assert (
            word_envelope_bytes([row.model_dump(mode="json") for row in rows])
            <= SEMANTIC_BYTE_QUOTAS[lane]
        )
        assert [row.ordinal for row in rows] == list(range(len(rows)))
        if lane != kind:
            assert len(rows) == 1 and getattr(value, lane + "_limit") == "none"
    assert value.block_count + value.call_count + value.support_count == 2503


@pytest.mark.parametrize("kind", ["block", "call", "support"])
def test_reserved_event_caps_are_independent_and_fit_global_cap(kind: str) -> None:
    collector = WordStructureCollector(retention="semantic-first-v2", max_events=3)
    with collector.scope():
        for index in range(4):
            collector.block("paragraph", (index,), "x")
            collector.call("footnote", "x", (index, 0), (index,), expanded=False)
            collector.support((index,), "x", 0, "skipped_block")
    value = finish(collector)
    assert semantic_event_quotas(3) == {"block": 1, "call": 1, "support": 1}
    assert len(value.blocks) == len(value.calls) == len(value.support) == 1
    assert getattr(value, kind + "_limit") == "events"
    assert value.coverage == "semantic_partial" and value.support_coverage == "partial"


@pytest.mark.parametrize("kind", ["identity", "escaped_identity", "path", "paragraph", "tag"])
def test_oversized_event_closes_only_its_prefix(kind: str) -> None:
    collector = WordStructureCollector(retention="semantic-first-v2")
    with collector.scope():
        with patch(
            "lexinform.word_structure.word_envelope_bytes",
            side_effect=AssertionError("oversized serializer entry"),
        ):
            if kind == "identity":
                collector.call("footnote", "x" * 524289, (0, 0), (0,), expanded=False)
            elif kind == "escaped_identity":
                collector.call("footnote", "\U0001f680" * 10000, (0, 0), (0,), expanded=False)
            elif kind == "paragraph":
                collector.call("footnote", "x", (0, 0), tuple(range(100000)), expanded=False)
            elif kind == "path":
                collector.block("paragraph", tuple(range(100000)), "x")
            else:
                collector.support((0,), "x" * 4097, 0, "ignored_leaf")
        collector.block("paragraph", (1,), "later")
        collector.call("footnote", "later", (1, 0), (1,), expanded=False)
        collector.support((2,), "later", 0, "ignored_leaf")
    value = finish(collector)
    limits = [value.block_limit, value.call_limit, value.support_limit]
    assert limits.count("bytes") == 1
    assert [len(value.blocks), len(value.calls), len(value.support)].count(1) == 2


@pytest.mark.parametrize("events", [0, 1, 2, 65537, True])
def test_semantic_profile_rejects_invalid_event_limits(events: int) -> None:
    with pytest.raises(ValueError):
        WordStructureCollector(retention="semantic-first-v2", max_events=events)


def semantic_fixture() -> WordStructuredExtraction:
    return DocxTextExtractor().extract_with_semantic_structure(structured())


@pytest.mark.parametrize(
    "field",
    [
        "ordinal",
        "order",
        "count",
        "bucket",
        "limit",
        "hash",
        "coverage",
        "scope",
        "float_bytes",
        "binding",
    ],
)
def test_v2_reader_rejects_corrupt_prefixes_counters_scope_and_physical_bindings(
    field: str,
) -> None:
    result = semantic_fixture()
    payload = result.model_dump(mode="json")
    value = payload["structure"]
    if field == "ordinal":
        value["blocks"][0]["ordinal"] = 1
    elif field == "order":
        value["calls"][0]["order"] = value["blocks"][0]["order"]
    elif field == "count":
        value["support_count"] += 1
    elif field == "bucket":
        value["recognized_support"].append(value["recognized_support"][0])
    elif field == "limit":
        value["block_limit"] = "events"
    elif field == "hash":
        value["observed_stream_sha256"] = "a" * 64
    elif field == "coverage":
        value["coverage"] = "complete"
    elif field == "scope":
        value["scope"] = "legacy-visited-package-nodes"
    elif field == "float_bytes":
        value["max_bytes"] = 524288.0
    else:
        value["body_manifest_sha256"] = "b" * 64
    with pytest.raises(ValidationError):
        WordStructuredExtraction.model_validate(payload)


def test_v1_json_and_legacy_business_remain_exact_and_native_consumer_checks_xml() -> None:
    data = structured()
    reader = DocumentTextExtractor(FakeTextExtractor(), DocxTextExtractor(), FakeTextExtractor())
    old = reader.extract_parts_with_word_structure(data, include_pdf_pages=False)
    native = reader.extract_parts_with_semantic_word_structure(data, include_pdf_pages=False)
    assert native.legacy.model_dump_json() == old.legacy.model_dump_json()
    assert select_document_parts(native.legacy, max_part_chars=None) == select_document_parts(
        old.legacy, max_part_chars=None
    )
    assert WordStructuredParts.model_validate_json(native.model_dump_json()) == native
    assert (
        reader.extract_parts_with_word_structure(data, include_pdf_pages=False).model_dump_json()
        == old.model_dump_json()
    )
    structured_value = semantic_fixture()
    assert structured_value.legacy == DocxTextExtractor().extract_with_notes(data)
    verified = verify_structure(data, structured_value)
    assert verified["version"] == "word-semantic-structure-v2" and verified["independent_xml_paths"]
    core, metadata = snapshot(
        data,
        adapter=True,
        physical_parts=True,
        with_notes=True,
        with_structure=True,
        semantic_structure=True,
    )
    old_core, _ = snapshot(
        data, adapter=True, physical_parts=True, with_notes=True, with_structure=True
    )
    assert core == old_core and metadata is not None
    assert metadata["structure"] == verified


class V1OnlyWord(FakeTextExtractor):
    def extract_with_structure(self, data: bytes) -> WordStructuredExtraction:
        return DocxTextExtractor().extract_with_structure(data)


def test_custom_v1_never_receives_v2_request_and_defaults_never_activate_v2() -> None:
    custom = V1OnlyWord("custom legacy")
    reader = DocumentTextExtractor(FakeTextExtractor(), custom, FakeTextExtractor())
    with patch.object(custom, "extract_with_structure", side_effect=AssertionError("v1 hook")):
        result = reader.extract_parts_with_semantic_word_structure(structured())
    assert not result.word_structure and result.legacy.parts[0].text == "custom legacy"
    with patch.object(
        DocxTextExtractor, "extract_with_semantic_structure", side_effect=AssertionError("v2 hook")
    ):
        standard = DocumentTextExtractor(
            FakeTextExtractor(), DocxTextExtractor(), FakeTextExtractor()
        )
        standard.extract_document(structured())
        standard.extract_parts_with_word_structure(structured())


def test_nested_word_parts_use_separate_actual_semantic_invocations() -> None:
    data = archive({"a.docx": structured(), "b.zip": archive({"b.docm": structured()})})
    reader = DocumentTextExtractor(FakeTextExtractor(), DocxTextExtractor(), FakeTextExtractor())
    with patch.object(
        DocxTextExtractor,
        "extract_with_semantic_structure",
        wraps=DocxTextExtractor().extract_with_semantic_structure,
    ) as semantic:
        result = reader.extract_parts_with_semantic_word_structure(data)
    assert semantic.call_count == len(result.word_structure) == 2
    assert [row.part_index for row in result.word_structure] == [0, 1]


@pytest.mark.parametrize("extra", [0, 1])
def test_support_prefix_exact_serialized_byte_boundary(extra: int) -> None:
    sample = WordStructureCollector(retention="semantic-first-v2")
    with sample.scope():
        sample.support((0,), "x", 0, "ignored_leaf")
    initial = finish(sample)
    size = word_envelope_bytes([initial.support[0].model_dump(mode="json")])
    difference = SEMANTIC_BYTE_QUOTAS["support"] - size
    path = (0,) * (1 + difference // 2)
    tag = "x" * (1 + difference % 2 + extra)
    collector = WordStructureCollector(retention="semantic-first-v2")
    with collector.scope():
        collector.support(path, tag, 0, "ignored_leaf")
        collector.block("paragraph", (0,), "retained block")
    value = finish(collector)
    assert len(value.blocks) == 1
    if extra:
        assert not value.support and value.support_limit == "bytes"
    else:
        assert (
            word_envelope_bytes([row.model_dump(mode="json") for row in value.support])
            == SEMANTIC_BYTE_QUOTAS["support"]
        )
        assert value.support_limit == "none"


@pytest.mark.parametrize("max_bytes", [4096, 524287, 524289, 1048576])
def test_v2_fixed_byte_profile_does_not_change_v1_supported_limits(max_bytes: int) -> None:
    WordStructureCollector(max_bytes=max_bytes)
    with pytest.raises(ValueError):
        WordStructureCollector(retention="semantic-first-v2", max_bytes=max_bytes)


def test_unsupported_walk_cannot_claim_semantic_completion() -> None:
    data = archive({"word/document.xml": b'<!DOCTYPE x [<!ENTITY forbidden "x">]><document/>'})
    result = DocxTextExtractor().extract_with_semantic_structure(data)
    assert isinstance(result.structure, WordSemanticStructureExtraction), (
        "The semantic API returns v2"
    )
    assert result.structure.coverage == "semantic_partial"
    assert result.structure.support_coverage == "partial"
    assert result.structure.observed_stream_sha256 is None


def test_native_semantic_profile_has_exact_same_zip_opens_as_v1() -> None:
    data = archive({"a.docx": structured(), "nested.zip": archive({"b.docm": structured()})})
    reader = DocumentTextExtractor(FakeTextExtractor(), DocxTextExtractor(), FakeTextExtractor())
    original = zipfile.ZipFile.open
    opens: list[str] = []

    def opened(
        archive: zipfile.ZipFile,
        name: str | zipfile.ZipInfo,
        mode: Literal["r", "w"] = "r",
        pwd: bytes | None = None,
        *,
        force_zip64: bool = False,
    ) -> IO[bytes]:
        opens.append(name.filename if isinstance(name, zipfile.ZipInfo) else name)
        return original(archive, name, mode, pwd, force_zip64=force_zip64)

    with patch.object(zipfile.ZipFile, "open", opened):
        old = reader.extract_parts_with_word_structure(data)
    before = tuple(opens)
    opens.clear()
    with patch.object(zipfile.ZipFile, "open", opened):
        result = reader.extract_parts_with_semantic_word_structure(data)
    assert tuple(opens) == before and result.legacy == old.legacy


def test_existing_replay_native_semantic_mode_preserves_library_business(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    file = corpus / "documents/files/sample.docx"
    file.parent.mkdir(parents=True)
    data = structured()
    file.write_bytes(data)
    catalog = tmp_path / "catalog.json"
    payload = {
        "status": "recorded",
        "suite": "external",
        "observations": {
            "word/sample.docx": {"actual": {"sha256": hashlib.sha256(data).hexdigest()}}
        },
    }
    catalog.write_text(json.dumps(payload))
    catalog_sha = hashlib.sha256(catalog.read_bytes()).hexdigest()
    baseline = tmp_path / "library.json"
    assert run(corpus, catalog, catalog_sha, baseline, mode="library") == 0
    result = tmp_path / "semantic.json"
    assert (
        run(
            corpus,
            catalog,
            catalog_sha,
            result,
            mode="semantic-structure-parts",
            reference=baseline,
            reference_sha256=hashlib.sha256(baseline.read_bytes()).hexdigest(),
        )
        == 0
    )
    recorded = json.loads(result.read_text())
    assert recorded["retention_profile"] == "semantic-first-v2"
    assert recorded["entrypoint"] == "word_semantic_structure_parts"
    assert (
        recorded["physical"]["documents/files/sample.docx"]["structure"]["version"]
        == "word-semantic-structure-v2"
    )
    assert not recorded["changes"] and not recorded["errors"]
