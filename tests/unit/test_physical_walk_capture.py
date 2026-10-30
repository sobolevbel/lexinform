import hashlib
import io
import threading
import warnings
import zipfile
from contextvars import copy_context
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, NoReturn

import pytest

from lexinform import physical_walk_capture
from lexinform.adapters import document_text
from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.concurrency import fan_out
from lexinform.models.artifacts import ArtifactIdentity, ArtifactMetadata
from lexinform.models.documents import DocumentPart, ExtractedDocument, ExtractedParts
from lexinform.models.extractions import ExtractionResult
from lexinform.models.physical_inventory import (
    PhysicalCaptureRecipe,
    PhysicalContainerExecution,
    PhysicalGraph,
    PhysicalHeaders,
    PhysicalMetadata,
    PhysicalRootOrder,
)
from lexinform.physical_assembly_inputs import (
    ObservedPhysicalOrder,
    ObservedPhysicalPrefix,
    ObservedPhysicalTotals,
)
from lexinform.physical_root_assembly import (
    PhysicalAssemblyGap,
    PhysicalAssemblyPacket,
    PhysicalRootAssembler,
)
from lexinform.physical_walk_capture import (
    PhysicalWalkCapture,
    PhysicalWalkContext,
    PhysicalWalkObservation,
    current_physical_walk_capture,
)
from tests.fakes import FakeTextExtractor
from tests.unit.test_document_text import _docx, _odt
from tests.unit.test_physical_inventory import recipe
from tests.unit.test_physical_root_assembly import CHILD, ROOT, accept_packet, headers, leaf


def package(items: tuple[tuple[str, bytes], ...]) -> bytes:
    output = io.BytesIO()
    with warnings.catch_warnings(), zipfile.ZipFile(output, "w") as archive:
        warnings.simplefilter("ignore", UserWarning)
        for name, data in items:
            archive.writestr(name, data)
    return output.getvalue()


def extractor(*, limit: int | None = None) -> DocumentTextExtractor:
    return DocumentTextExtractor(
        PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor(), max_member_bytes=limit
    )


def packet(capture: PhysicalWalkCapture) -> PhysicalAssemblyPacket:
    observation = capture.observation
    assert observation is not None, "scope exit always seals its separate observation"
    assert observation.unavailable is None, "these controls stay within the captured input profile"
    assert isinstance(observation.assembly, PhysicalAssemblyPacket), (
        "complete cooperative controls produce structurally closed metadata"
    )
    return observation.assembly


def test_actual_nested_word_duplicate_headers_order_and_restoration(
    repo: SqliteBillRepository,
) -> None:
    first = _docx("<w:p><w:r><w:t>first</w:t></w:r></w:p>")
    second = _docx("<w:p><w:r><w:t>second</w:t></w:r></w:p>")
    data = package((("same.docx", first), ("same.docx", second)))
    runtime = extractor()
    expected = runtime.extract_document_with_provenance(data)
    capture = PhysicalWalkCapture()
    with capture.scope():
        actual = runtime.extract_document_with_provenance(data)
    result = packet(capture)
    assert actual == expected
    assert result.capture.extraction_id == actual.result.result_id
    recipe = next(
        row.metadata for row in result.metadata if isinstance(row.metadata, PhysicalCaptureRecipe)
    )
    assert recipe.options.max_member_bytes == 32_000_000
    units = [
        row.metadata
        for row in result.metadata
        if isinstance(row.metadata, PhysicalContainerExecution)
    ]
    outer = next(row for row in units if row.route == "archive")
    assert [row[2:4] for row in outer.requests] == [(0, 0), (1, 1)]
    assert [row[2:4] for row in outer.opens] == [(0, 0), (1, 1)]
    order = next(
        row.metadata for row in result.metadata if isinstance(row.metadata, PhysicalRootOrder)
    )
    assert [row[:3] for row in order.contexts] == [(None, None, None), (0, 0, 0), (0, 1, 1)]
    assert order.requests == ((0, 0), (1, 0), (0, 1), (2, 0))
    root_header = next(
        row.metadata
        for row in result.metadata
        if isinstance(row.metadata, PhysicalHeaders)
        and row.body_sha256 == actual.result.artifact_sha256
    )
    start, end = root_header.central_directory_span
    assert (
        root_header.central_directory_sha256
        == hashlib.sha256(memoryview(data)[start:end]).hexdigest()
    )
    now = datetime(2026, 10, 30, tzinfo=UTC)
    repo.save_artifact_metadata(
        ArtifactMetadata(sha256=actual.result.artifact_sha256, byte_size=len(data), recorded_at=now)
    )
    repo.save_extraction_result(actual.result, recorded_at=now)
    accept_packet(repo, result)


def test_actual_odt_xml_observation_and_plain_facade(repo: SqliteBillRepository) -> None:
    data = _odt("<text:p>actual</text:p>")
    capture = PhysicalWalkCapture()
    with capture.scope():
        assert extractor().extract(data) == "actual"
    result = packet(capture)
    assert result.capture.extraction_id is None
    unit = next(
        row.metadata
        for row in result.metadata
        if isinstance(row.metadata, PhysicalContainerExecution)
    )
    assert unit.route == "odt"
    assert unit.requests[0][2:5] == ("content.xml", 1, "complete")
    accept_packet(repo, result)


def test_depth_and_member_limit_are_actual_branch_observations() -> None:
    data = _docx("<w:p/>")
    for _ in range(5):
        data = package((("nested.zip", data),))
    capture = PhysicalWalkCapture()
    with capture.scope():
        result = extractor().extract_parts(data)
    assert result.parts == ()
    assert any("archive depth limit" in text for text in result.rejected)
    units = [
        row.metadata
        for row in packet(capture).metadata
        if isinstance(row.metadata, PhysicalContainerExecution)
    ]
    assert any("not_visited_depth_limit" in row.statuses for row in units)
    capture = PhysicalWalkCapture()
    with capture.scope():
        extractor(limit=1).extract_parts(package((("large.docx", data), ("skip.bin", b"x"))))
    unit = next(
        row.metadata
        for row in packet(capture).metadata
        if isinstance(row.metadata, PhysicalContainerExecution)
    )
    assert unit.statuses == ("unpacking_limit_source_pinned_inference", "readability_rejected")
    assert not unit.requests


def test_xml_failure_and_unreadable_zip_retain_known_root(repo: SqliteBillRepository) -> None:
    for data in (package((("word/document.xml", b"<bad>"),)), b"PK\x03\x04broken"):
        runtime = extractor()
        expected = runtime.extract_parts(data)
        capture = PhysicalWalkCapture()
        with capture.scope():
            actual = runtime.extract_parts(data)
        assert actual == expected
        result = packet(capture)
        unit = next(
            row.metadata
            for row in result.metadata
            if isinstance(row.metadata, PhysicalContainerExecution)
        )
        assert unit.format_error in {"ParseError", "BadZipFile"}
        accept_packet(repo, result)


class InterruptedExtractor(FakeTextExtractor):
    def extract(self, data: bytes) -> str:
        raise KeyboardInterrupt("original")


def test_baseexception_survives_and_scope_does_not_revive() -> None:
    runtime = DocumentTextExtractor(InterruptedExtractor(), DocxTextExtractor(), DocTextExtractor())
    capture = PhysicalWalkCapture()
    with pytest.raises(KeyboardInterrupt, match="original"), capture.scope():
        saved = copy_context()
        runtime.extract_parts(b"%PDF-1.0")
    assert current_physical_walk_capture() is None
    assert saved.run(current_physical_walk_capture) is None
    unit = next(
        row.metadata
        for row in packet(capture).metadata
        if isinstance(row.metadata, PhysicalContainerExecution)
    )
    assert unit.propagated_error == "KeyboardInterrupt"


def test_capture_capacity_retains_draft_and_counts_after_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = package(tuple((f"{index}.docx", _docx("<w:p/>")) for index in range(100)))
    expected = extractor().extract_parts(data)
    monkeypatch.setattr(physical_walk_capture, "MAX_OBSERVATION_BYTES", 12000)
    capture = PhysicalWalkCapture()
    with capture.scope():
        actual = extractor().extract_parts(data)
    assert actual == expected
    observation = capture.observation
    assert observation is not None, "scope exit seals even exhausted observations"
    assert observation.unavailable is None
    assert "observation_capacity" in observation.limitations
    assert observation.artifact is not None, "actual root identity predates event-budget exhaustion"
    assert isinstance(observation.assembly, PhysicalAssemblyPacket), (
        "known totals close an honest minimum rooted packet"
    )
    draft = observation.draft
    assert draft is not None, (
        "overflow preserves an honest bounded draft for the partial input bridge"
    )
    assert draft.counts_complete
    assert draft.observed_contexts == 101
    assert draft.observed_requests == draft.observed_opens == 200
    assert draft.observed_physical == 100
    assert len(draft.order_prefix.requests) < draft.observed_requests
    assert draft.full_order_sha256 is None


def test_scope_is_explicit_and_does_not_cross_threads() -> None:
    capture = PhysicalWalkCapture()
    with capture.scope():
        saved = copy_context()
        seen: list[PhysicalWalkCapture | None] = []
        worker = threading.Thread(
            target=lambda: seen.append(saved.run(current_physical_walk_capture))
        )
        worker.start()
        worker.join()
        assert seen == [None]
    observation = capture.observation
    assert observation is not None, (
        "scope without cooperative hook still seals explicit unavailability"
    )
    assert observation.unavailable == "cooperative_invocation_unavailable"


def test_custom_word_helper_is_explicitly_opaque() -> None:
    runtime = DocumentTextExtractor(
        FakeTextExtractor(), FakeTextExtractor(), FakeTextExtractor(), odt=FakeTextExtractor()
    )
    capture = PhysicalWalkCapture()
    with capture.scope():
        runtime.extract_parts(_docx("<w:p/>"))
    observation = capture.observation
    assert observation is not None, "custom helper completion still seals its outer observation"
    assert observation.limitations == ("opaque_format_helper",)
    assert isinstance(observation.assembly, PhysicalAssemblyPacket), (
        "known outer parser evidence survives opacity"
    )
    assert any(row.coverage == "partial" for row in observation.assembly.metadata)


def test_deflate64_complete_request_has_no_invented_payload_open() -> None:
    data = (
        Path(__file__).parents[1] / "fixtures/document_selection/deflate64_771111.zip"
    ).read_bytes()
    capture = PhysicalWalkCapture()
    with capture.scope():
        actual = extractor().extract(data)
    assert "Projekt z dnia 2 kwietnia 2026 r." in actual
    result = packet(capture)
    root_header = next(
        row
        for row in result.metadata
        if row.body_sha256 == result.capture.artifact_sha256
        and isinstance(row.metadata, PhysicalHeaders)
    )
    assert isinstance(root_header.metadata, PhysicalHeaders), (
        "the selected root record retains header rows"
    )
    unit = next(
        row.metadata
        for row in result.metadata
        if isinstance(row.metadata, PhysicalContainerExecution)
        and row.header_id == root_header.metadata_id
    )
    deflate64 = {index for index, row in enumerate(root_header.metadata.rows) if row[4] == 9}
    assert deflate64
    assert any(request[3] in deflate64 and request[4] == "complete" for request in unit.requests)
    assert not any(event[3] in deflate64 for event in unit.opens)


def test_crc_failure_records_open_and_failed_request_without_complete_sha() -> None:
    data = bytearray(package((("bad.docx", b"invalid-content"),)))
    data[30 + len("bad.docx")] ^= 1
    capture = PhysicalWalkCapture()
    with capture.scope():
        result = extractor().extract_parts(bytes(data))
    assert result.parts == ()
    unit = next(
        row.metadata
        for row in packet(capture).metadata
        if isinstance(row.metadata, PhysicalContainerExecution)
    )
    assert unit.requests[0][4:] == ("failed", None, None, "BadZipFile")
    assert unit.opens[0][4:] == ("opened", None)
    assert unit.statuses == ("member_failed",)


class FailedRecipeCapture(PhysicalWalkCapture):
    def recipe(self, record: PhysicalMetadata) -> None:
        raise KeyboardInterrupt("observer")


def test_observer_baseexception_does_not_change_business_return() -> None:
    data = _docx("<w:p><w:r><w:t>retained</w:t></w:r></w:p>")
    runtime = extractor()
    expected = runtime.extract_parts(data)
    capture = FailedRecipeCapture()
    with capture.scope():
        actual = runtime.extract_parts(data)
    assert actual == expected
    observation = capture.observation
    assert observation is not None, "guard failures still seal the separate observation"
    assert observation.artifact is not None, "root identity is recorded before optional recipe work"
    assert observation.unavailable == "capture_event_failed"
    assert observation.failure_class == "KeyboardInterrupt"


class FailedFinalizerAssembler(PhysicalRootAssembler):
    def finish(
        self,
        recipe: PhysicalMetadata,
        order: ObservedPhysicalOrder,
        *,
        complete: bool,
        extraction: ExtractionResult | None = None,
    ) -> PhysicalAssemblyPacket | PhysicalAssemblyGap:
        raise KeyboardInterrupt("finalizer")


def test_finalizer_failure_preserves_draft_and_original_business_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(physical_walk_capture, "PhysicalRootAssembler", FailedFinalizerAssembler)
    runtime = DocumentTextExtractor(InterruptedExtractor(), DocxTextExtractor(), DocTextExtractor())
    capture = PhysicalWalkCapture()
    with pytest.raises(KeyboardInterrupt, match="original"), capture.scope():
        saved = copy_context()
        runtime.extract_parts(b"%PDF-1.0")
    assert saved.run(current_physical_walk_capture) is None
    observation = capture.observation
    assert observation is not None, "failed finalization still exposes actual known evidence"
    assert observation.unavailable == "capture_finalization_failed"
    draft = observation.draft
    assert draft is not None, "finalizer failure preserves independently completed context facts"
    assert draft.observed_contexts == 1
    assert draft.containers[0].propagated_error == "KeyboardInterrupt"
    assert not draft.counts_complete
    assert draft.full_order_sha256 is None


def capture_input(data: bytes) -> tuple[ExtractedParts, PhysicalWalkObservation]:
    capture = PhysicalWalkCapture()
    with capture.scope():
        result = extractor().extract_parts(data)
    observation = capture.observation
    assert observation is not None, "each actual worker seals its own explicitly entered scope"
    return result, observation


def test_explicit_worker_scopes_match_input_order_and_single_worker() -> None:
    data = (
        _docx("<w:p><w:r><w:t>one</w:t></w:r></w:p>"),
        _odt("<text:p>two</text:p>"),
        package((("child.docx", _docx("<w:p/>")),)),
        b"PK\x03\x04broken",
    )
    one = [outcome.result() for outcome in fan_out(data, capture_input, workers=1)]
    four = [outcome.result() for outcome in fan_out(data, capture_input, workers=4)]
    assert four == one
    assert all(observation.artifact is not None for _, observation in four)


def test_finished_walk_stays_complete_when_later_selection_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = RuntimeError("selection")

    def fail_selection(
        extracted: ExtractedParts, *, max_part_chars: int | None
    ) -> ExtractedDocument:
        raise failure

    monkeypatch.setattr(document_text, "select_document_parts", fail_selection)
    capture = PhysicalWalkCapture()
    with pytest.raises(RuntimeError, match="selection") as raised, capture.scope():
        extractor().extract_document(_docx("<w:p/>"))
    assert raised.value is failure
    order = next(
        row for row in packet(capture).metadata if isinstance(row.metadata, PhysicalRootOrder)
    )
    assert order.coverage == "complete"


def test_caught_walk_error_does_not_claim_completed_root_order() -> None:
    runtime = DocumentTextExtractor(InterruptedExtractor(), DocxTextExtractor(), DocTextExtractor())
    capture = PhysicalWalkCapture()
    with capture.scope(), pytest.raises(KeyboardInterrupt, match="original"):
        runtime.extract_parts(b"%PDF-1.0")
    order = next(
        row for row in packet(capture).metadata if isinstance(row.metadata, PhysicalRootOrder)
    )
    assert order.coverage == "partial"
    assert order.omission is not None, "interrupted walks retain unknown remainder explicitly"
    assert order.omission.full_metadata_sha256 is None


@pytest.mark.parametrize("data", [_docx("<w:p/>"), _odt("<text:p/>")], ids=["word", "odt"])
def test_opaque_helper_observer_failure_does_not_escape(
    data: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = DocumentTextExtractor(
        FakeTextExtractor(), FakeTextExtractor(), FakeTextExtractor(), odt=FakeTextExtractor()
    )
    expected = runtime.extract_parts(data)
    failure = KeyboardInterrupt("opaque observer")

    def fail(capture: PhysicalWalkCapture) -> None:
        raise failure

    monkeypatch.setattr(PhysicalWalkCapture, "opaque_helper", fail)
    capture = PhysicalWalkCapture()
    with capture.scope():
        actual = runtime.extract_parts(data)
    assert actual == expected
    observation = capture.observation
    assert observation is not None, "opaque helper guard failures still seal separate evidence"
    assert observation.unavailable == "capture_event_failed"
    assert observation.failure_class == "KeyboardInterrupt"


def test_context_construction_failure_does_not_skip_business(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _docx("<w:p/>")
    expected = extractor().extract_parts(data)

    def fail(*args: object, **kwargs: object) -> NoReturn:
        raise RuntimeError("context setup")

    monkeypatch.setattr(physical_walk_capture, "PhysicalWalkContext", fail)
    capture = PhysicalWalkCapture()
    with capture.scope():
        actual = extractor().extract_parts(data)
    assert actual == expected
    observation = capture.observation
    assert observation is not None, "context construction guard failure still seals known root"
    assert observation.artifact is not None, "the actual root body predates optional context setup"
    assert observation.unavailable == "capture_context_entry_failed"
    interrupted = DocumentTextExtractor(
        InterruptedExtractor(), DocxTextExtractor(), DocTextExtractor()
    )
    with (
        pytest.raises(KeyboardInterrupt, match="original") as raised,
        PhysicalWalkCapture().scope(),
    ):
        interrupted.extract_parts(b"%PDF-1.0")
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_huge_header_keeps_actual_minimum_directory_before_row_budget(
    monkeypatch: pytest.MonkeyPatch,
    repo: SqliteBillRepository,
) -> None:
    data = package(tuple((f"{index}.bin", b"") for index in range(500)))
    expected = extractor().extract_parts(data)
    monkeypatch.setattr(physical_walk_capture, "MAX_OBSERVATION_BYTES", 9000)
    capture = PhysicalWalkCapture()
    with capture.scope():
        actual = extractor().extract_parts(data)
    assert actual == expected
    observation = capture.observation
    assert observation is not None, "budget exhaustion seals a separate partial draft"
    assert observation.unavailable is None
    assert "observation_capacity" in observation.limitations
    assert isinstance(observation.assembly, PhysicalAssemblyPacket), (
        "known root/header/totals close without a second walk"
    )
    accept_packet(repo, observation.assembly)
    draft = observation.draft
    assert draft is not None, "overflow preserves minimum root closure inputs"
    assert draft.header_universes == ((0, 500),)
    root = next(frame for frame in draft.containers if frame.index == 0)
    header = root.header
    assert header is not None, "the minimum header is reserved before row/map expansion"
    assert isinstance(header.metadata, PhysicalHeaders), (
        "minimum directory evidence uses closed header fields"
    )
    assert header.metadata.rows == ()
    assert header.total("rows", 0) == 500
    start, end = header.metadata.central_directory_span
    assert (
        header.metadata.central_directory_sha256
        == hashlib.sha256(memoryview(data)[start:end]).hexdigest()
    )
    assert header.omission is not None, "uninspected rows have truthful known omissions"
    assert header.omission.full_metadata_sha256 is None
    assert draft.recipe is not None, "overflow retains the actual invocation recipe for its bridge"
    assert observation.artifact is not None, (
        "the reserved root header keeps its actual artifact parent"
    )
    assert draft.order_prefix.legacy_rejections is not None, "the whole actual walk completed"
    assembler = PhysicalRootAssembler(observation.artifact)
    for frame in draft.containers:
        assembler.add_container(frame)
    partial = assembler.finish(
        draft.recipe,
        ObservedPhysicalOrder(
            draft.order_prefix.requests,
            draft.order_prefix.opens,
            draft.order_prefix.physical,
            draft.order_prefix.zip_failures,
            draft.order_prefix.legacy_rejections,
        ),
        complete=False,
    )
    assert isinstance(partial, PhysicalAssemblyPacket), (
        "the known minimum root closure fits existing typed storage"
    )
    accept_packet(repo, partial)


@pytest.mark.parametrize("capacity", [None, 6500], ids=["before_capacity", "after_capacity"])
def test_update_hook_failure_preserves_business_and_marks_counts_incomplete(
    monkeypatch: pytest.MonkeyPatch,
    capacity: int | None,
) -> None:
    data = _docx("<w:p/>")
    expected = extractor().extract_parts(data)

    def fail(capture: PhysicalWalkCapture, index: int, part: DocumentPart) -> None:
        raise RuntimeError("part update")

    monkeypatch.setattr(PhysicalWalkCapture, "part", fail)
    if capacity is not None:
        monkeypatch.setattr(physical_walk_capture, "MAX_OBSERVATION_BYTES", capacity)
    capture = PhysicalWalkCapture()
    with capture.scope():
        actual = extractor().extract_parts(data)
    assert actual == expected
    observation = capture.observation
    assert observation is not None, "update failures still seal actual known prefix evidence"
    assert observation.unavailable == "capture_event_failed"
    draft = observation.draft
    assert draft is not None, "update failures preserve independently completed read requests"
    if capacity is None:
        assert draft.order_prefix.requests == ((0, 0),)
    assert not draft.counts_complete


def test_nested_context_setup_failure_cannot_change_parent_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = package((("child.docx", _docx("<w:p/>")),))
    expected = extractor().extract_parts(data)
    original = physical_walk_capture.PhysicalWalkContext

    def context(
        index: int,
        parent: int | None,
        parent_ordinal: int | None,
        body: ArtifactIdentity | None,
        route: Literal["archive", "word", "odt", "non_zip", "zip_unreadable"],
        *,
        retained: bool = True,
    ) -> PhysicalWalkContext:
        if index == 1:
            raise RuntimeError("nested context")
        return original(index, parent, parent_ordinal, body, route, retained=retained)

    monkeypatch.setattr(physical_walk_capture, "PhysicalWalkContext", context)
    capture = PhysicalWalkCapture()
    with capture.scope():
        actual = extractor().extract_parts(data)
    assert actual == expected
    observation = capture.observation
    assert observation is not None, "failed child setup keeps its actual parent prefix"
    assert observation.unavailable == "capture_context_entry_failed"
    draft = observation.draft
    assert draft is not None, "the actual parent header/body/requests survive child setup failure"
    root = next(frame for frame in draft.containers if frame.index == 0)
    assert root.route == "archive"
    assert root.physical_index is None
    assert not draft.counts_complete


def test_measured_prefix_preserves_exact_totals_without_retained_child_nulls(
    repo: SqliteBillRepository,
) -> None:
    root = ROOT
    prefix = ObservedPhysicalPrefix(
        root, headers(root, 2), ObservedPhysicalTotals(3, 6, 5, 2, 1), (2, "c" * 64)
    )
    result = PhysicalRootAssembler(root).finish_prefix(recipe(), prefix)
    assert isinstance(result, PhysicalAssemblyPacket), (
        "explicit measured totals fit the existing minimum-root schema"
    )
    assert result.artifacts == (root,)
    graph = next(row for row in result.metadata if isinstance(row.metadata, PhysicalGraph))
    assert isinstance(graph.metadata, PhysicalGraph), "the retained inventory record is a graph"
    assert graph.metadata.edges == ()
    assert graph.total("edges", 0) == 2
    order = next(row for row in result.metadata if isinstance(row.metadata, PhysicalRootOrder))
    assert isinstance(order.metadata, PhysicalRootOrder), (
        "the retained execution record is root order"
    )
    assert not (order.metadata.contexts or order.metadata.requests or order.metadata.opens)
    assert [
        order.total(field, 0)
        for field in ("contexts", "requests", "opens", "physical", "zip_failures")
    ] == [3, 6, 5, 2, 1]
    assert order.omission is not None, "discarded interleaving has explicit coverage"
    assert order.omission.full_metadata_sha256 is None
    accept_packet(repo, result)


@pytest.mark.parametrize(
    "totals", [ObservedPhysicalTotals(0, 0, 0, 0, 0), ObservedPhysicalTotals(1, 0, 0, 2, 0)]
)
def test_measured_prefix_rejects_impossible_root_or_part_totals(
    totals: ObservedPhysicalTotals,
) -> None:
    with pytest.raises(ValueError, match="measured totals"):
        PhysicalRootAssembler(ROOT).finish_prefix(
            recipe(), ObservedPhysicalPrefix(ROOT, None, totals, (0, "c" * 64))
        )


def test_measured_prefix_does_not_bypass_parent_header_identity_or_span() -> None:
    prefix = ObservedPhysicalPrefix(
        ROOT, headers(CHILD), ObservedPhysicalTotals(1, 0, 0, 0, 0), (0, "c" * 64)
    )
    with pytest.raises(ValueError, match="root header"):
        PhysicalRootAssembler(ROOT).finish_prefix(recipe(), prefix)
    tiny = ArtifactIdentity(sha256=ROOT.sha256, byte_size=1)
    with pytest.raises(ValueError, match="span exceeds"):
        PhysicalRootAssembler(tiny).finish_prefix(
            recipe(),
            ObservedPhysicalPrefix(tiny, headers(ROOT), prefix.totals, prefix.legacy_rejections),
        )
    used = PhysicalRootAssembler(ROOT)
    used.add_container(leaf())
    with pytest.raises(ValueError, match="fresh root"):
        used.finish_prefix(
            recipe(), ObservedPhysicalPrefix(ROOT, None, prefix.totals, prefix.legacy_rejections)
        )


@pytest.mark.parametrize("failed_index", [0, 1], ids=["first_read", "after_completed_prefix"])
def test_terminal_observer_failure_never_exposes_a_synthetic_failed_read(
    failed_index: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    data = package(
        (
            (
                "word/document.xml",
                f"<w:document {namespace}><w:body><w:p><w:r><w:t>body</w:t></w:r></w:p></w:body></w:document>".encode(),
            ),
            (
                "word/footnotes.xml",
                f'<w:footnotes {namespace}><w:footnote w:id="1"><w:p><w:r><w:t>note</w:t></w:r></w:p></w:footnote></w:footnotes>'.encode(),
            ),
        )
    )
    expected = extractor().extract_parts(data)
    original = PhysicalWalkCapture.returned

    def returned(
        capture: PhysicalWalkCapture,
        index: int | None,
        content: bytes | None,
        declared: int | None,
        error: BaseException | None,
    ) -> None:
        if index == failed_index:
            raise KeyboardInterrupt("terminal observation")
        original(capture, index, content, declared, error)

    monkeypatch.setattr(PhysicalWalkCapture, "returned", returned)
    capture = PhysicalWalkCapture()
    with capture.scope():
        actual = extractor().extract_parts(data)
    assert actual == expected
    observation = capture.observation
    assert observation is not None, (
        "failed read telemetry still seals its independently known facts"
    )
    assert observation.unavailable == "capture_event_failed"
    assert observation.failure_class == "KeyboardInterrupt"
    assert observation.assembly is None
    draft = observation.draft
    assert draft is not None, "unknown read outcome keeps the honest completed prefix"
    root = next(frame for frame in draft.containers if frame.index == 0)
    assert len(root.requests) == len(root.opens) == failed_index
    assert root.statuses == ()
    assert all(request[4] == "complete" and request[7] is None for request in root.requests)
    expected_order = tuple((0, index) for index in range(failed_index))
    assert draft.order_prefix.requests == draft.order_prefix.opens == expected_order
    assert draft.observed_requests == draft.observed_opens == 2
    assert not draft.counts_complete
