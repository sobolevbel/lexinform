import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from typing import IO, Literal, Never
from unittest.mock import patch

import pytest

from lexinform import extraction_records
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.models import WordDetailedExtraction
from lexinform.models.documents import ExtractedParts
from lexinform.models.word_structure import WordStructureView
from lexinform.models.word_view_capture import WordPartViewStatus
from lexinform.word_structure import WordStructureCollector, current_word_structure
from tests.fakes import FakeTextExtractor
from tests.unit.test_physical_extraction import archive, docx
from tests.unit.test_word_structure import structured


def extractor(word: DocxTextExtractor | FakeTextExtractor | None = None) -> DocumentTextExtractor:
    return DocumentTextExtractor(
        FakeTextExtractor("PDF", page_count=1),
        word or DocxTextExtractor(),
        FakeTextExtractor("DOC"),
    )


def forbidden(*args: object, **kwargs: object) -> Never:
    raise AssertionError("status-only cannot allocate events, coordinates, hashes or JSON")


class ForbiddenHash:
    sha256 = staticmethod(forbidden)


class ForbiddenJson:
    dumps = staticmethod(forbidden)


def test_status_only_shortcuts_before_events_hashes_json_and_coordinate_capture() -> None:
    data = structured()
    reader = DocxTextExtractor()
    before = reader.extract_with_notes(data)
    paths: list[tuple[int, ...]] = []
    original = WordStructureCollector.report_support

    def reported(
        self: WordStructureCollector,
        path: tuple[int, ...],
        tag: str,
        child_count: int,
        view: WordStructureView,
        *,
        recognized: bool = True,
    ) -> None:
        paths.append(path)
        original(self, path, tag, child_count, view, recognized=recognized)

    with (
        patch("lexinform.word_structure._Event", forbidden),
        patch("lexinform.word_structure.hashlib", ForbiddenHash),
        patch("lexinform.word_structure.json", ForbiddenJson),
        patch.object(WordStructureCollector, "block", forbidden),
        patch.object(WordStructureCollector, "call", forbidden),
        patch.object(WordStructureCollector, "report_support", reported),
    ):
        observed = reader.extract_with_view_status(data)
    assert observed.legacy.model_dump_json() == before.model_dump_json()
    assert observed.status is not None, (
        "The native status observer completes without allocating records"
    )
    assert observed.status.observed_skipped_blocks == 1
    assert observed.status.observed_unclassified_nodes >= 1
    assert paths and all(path == () for path in paths)
    assert (
        observed.status.known_walk == "finished"
        and observed.status.xml_completeness == "not_asserted"
    )


def test_default_provenance_records_status_without_altering_legacy_parts_selection_or_losses() -> (
    None
):
    data = archive({"a.docx": structured(), "nested.zip": archive({"b.docm": structured()})})
    reader = extractor()
    before_parts = reader.extract_parts(data)
    before = reader.extract_document(data)
    observed = reader.extract_document_with_provenance(data)
    assert (
        observed.document == before
        and reader.extract_parts(data).model_dump_json() == before_parts.model_dump_json()
    )
    assert all(part.word_view_status is not None for part in observed.result.selected_parts)
    assert all(
        part.word_view_status is None
        for part in extraction_records.summarize_extraction(
            before_parts,
            before,
            components=observed.result.components,
            options=observed.result.options,
        ).selected_parts
    )
    assert all(
        part.losses == before_parts.parts[index].losses
        for index, part in enumerate(reader.extract_parts(data).parts)
    )
    assert len(observed.result.model_dump_json().encode()) <= 16384


def test_status_capture_adds_no_zip_opens_or_second_word_walk() -> None:
    data = structured()
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

    reader = DocxTextExtractor()
    with patch.object(zipfile.ZipFile, "open", opened):
        old = reader.extract_with_notes(data)
    before = tuple(opens)
    opens.clear()
    with patch.object(zipfile.ZipFile, "open", opened):
        observed = reader.extract_with_view_status(data)
    assert tuple(opens) == before and observed.legacy == old


@pytest.mark.parametrize("fault", ["setup", "hook", "finish", "exit"])
def test_metadata_capture_failures_preserve_single_business_read_and_omit_status(
    fault: str,
) -> None:
    data = structured()
    reader = DocxTextExtractor()
    old = reader.extract_with_notes(data)
    original_scope = WordStructureCollector.scope

    @contextmanager
    def broken_exit(self: WordStructureCollector) -> Iterator[None]:
        with original_scope(self):
            yield
        raise RuntimeError("metadata exit")

    target = {
        "setup": "for_view_status",
        "hook": "support",
        "finish": "finish_view_status",
        "exit": "scope",
    }[fault]
    arguments = (
        {"new": broken_exit} if fault == "exit" else {"side_effect": RuntimeError("metadata")}
    )
    with patch.object(WordStructureCollector, target, **arguments):
        observed = reader.extract_with_view_status(data)
    assert observed.legacy == old and observed.status is None


def test_nested_setup_failure_masks_outer_collector_instead_of_misattributing_inner_nodes() -> None:
    outer = WordStructureCollector.for_view_status()
    with (
        outer.view_scope(),
        patch.object(WordStructureCollector, "for_view_status", side_effect=RuntimeError("setup")),
    ):
        actual = DocxTextExtractor().extract_with_view_status(structured())
    assert actual.status is None
    assert outer.finish_view_status(actual.legacy) is not None
    outer_status = outer.finish_view_status(actual.legacy)
    assert outer_status is not None, "The untouched outer observer retains its own empty scope"
    assert outer_status.observed_skipped_blocks == outer_status.observed_unclassified_nodes == 0


class CountingOverride(DocxTextExtractor):
    def __init__(self) -> None:
        self.calls = 0

    def extract_with_notes(self, data: bytes) -> WordDetailedExtraction:
        self.calls += 1
        return super().extract_with_notes(data)


def test_inherited_new_capability_cannot_bypass_existing_custom_word_override() -> None:
    word = CountingOverride()
    result = extractor(word).extract_document_with_provenance(structured())
    assert word.calls == 1
    assert result.result.selected_parts[0].word_view_status is None


class OverridePackage(DocumentTextExtractor):
    def __init__(self) -> None:
        super().__init__(FakeTextExtractor(), DocxTextExtractor(), FakeTextExtractor())
        self.calls = 0

    def extract_parts(
        self,
        data: bytes,
        *,
        include_pdf_pages: bool = True,
        include_word_blocks: bool = True,
        include_word_notes: bool = True,
        include_odt_blocks: bool = True,
        include_unlocated_text: bool = True,
    ) -> ExtractedParts:
        self.calls += 1
        return super().extract_parts(
            data,
            include_pdf_pages=include_pdf_pages,
            include_word_blocks=include_word_blocks,
            include_word_notes=include_word_notes,
            include_odt_blocks=include_odt_blocks,
            include_unlocated_text=include_unlocated_text,
        )


def test_inherited_provenance_method_honors_custom_package_extract_parts_override() -> None:
    reader = OverridePackage()
    result = reader.extract_document_with_provenance(structured())
    assert reader.calls == 1 and result.result.selected_parts[0].word_view_status is None


@pytest.mark.parametrize("fault", ["index", "input", "body", "notes", "duplicate"])
def test_summary_requires_actual_physical_input_body_and_note_refs(fault: str) -> None:
    data = structured()
    reader = extractor()
    physical = reader.extract_parts(data)
    selected = reader.extract_document(data)
    known = reader.extract_document_with_provenance(data)
    capture = DocxTextExtractor().extract_with_view_status(data)
    assert capture.status is not None, "The complete native input has its observed status"
    ref = WordPartViewStatus(
        part_index=0,
        input_sha256=capture.legacy.body.input_sha256,
        body_manifest_sha256=capture.legacy.body.manifest_sha256,
        notes_manifest_sha256=capture.legacy.notes.manifest_sha256,
        status=capture.status,
    )
    if fault == "index":
        ref = ref.model_copy(update={"part_index": 1})
    elif fault != "duplicate":
        field = {
            "input": "input_sha256",
            "body": "body_manifest_sha256",
            "notes": "notes_manifest_sha256",
        }[fault]
        ref = ref.model_copy(update={field: "a" * 64})
    with pytest.raises(ValueError):
        extraction_records.summarize_extraction(
            physical,
            selected,
            components=known.result.components,
            options=known.result.options,
            word_views=(ref, ref) if fault == "duplicate" else (ref,),
        )


def test_missing_or_refused_xml_never_claims_finished_walk() -> None:
    data = archive({"word/document.xml": b'<!DOCTYPE x [<!ENTITY no "x">]><document/>'})
    observed = DocxTextExtractor().extract_with_view_status(data)
    assert observed.status is not None, "The observer records the actual refused known scope"
    assert observed.status.known_walk == "interrupted"


def test_v1_and_v2_optin_json_stay_literal_when_view_status_is_not_requested() -> None:
    data = structured()
    reader = DocxTextExtractor()
    v1 = reader.extract_with_structure(data).model_dump_json()
    v2 = reader.extract_with_semantic_structure(data).model_dump_json()
    reader.extract_with_view_status(data)
    assert reader.extract_with_structure(data).model_dump_json() == v1
    assert reader.extract_with_semantic_structure(data).model_dump_json() == v2


def test_status_retention_defers_to_existing_summary_prefix_under_outer_byte_cap() -> None:
    data = archive(
        {
            "projekt.docx": docx("USTAWA Art. 1. Cudzoziemiec. " * 30),
            **{
                f"zalacznik-{index}.docx": docx(f"Załącznik nr {index} do ustawy\nDane {index}")
                for index in range(40)
            },
        }
    )
    reader = extractor()
    before = reader.extract_document(data)
    observed = reader.extract_document_with_provenance(data)
    assert observed.document == before
    assert observed.result.selected_part_count == 41
    assert (
        len(observed.result.selected_parts) == 16 and observed.result.omitted_selected_parts == 25
    )
    assert any(part.word_view_status is not None for part in observed.result.selected_parts)
    assert len(observed.result.model_dump_json().encode()) <= 16384


def test_status_changes_only_optional_summary_not_legacy_physical_manifest() -> None:
    data = structured()
    reader = extractor()
    observed = reader.extract_document_with_provenance(data)
    legacy = extraction_records.summarize_extraction(
        reader.extract_parts(data),
        observed.document,
        components=observed.result.components,
        options=observed.result.options,
    )
    assert observed.result.physical_manifest_sha256 == legacy.physical_manifest_sha256
    assert all(
        a.manifest_sha256 == b.manifest_sha256
        for a, b in zip(observed.result.selected_parts, legacy.selected_parts, strict=True)
    )
    assert observed.result.result_id != legacy.result_id


def test_instance_override_is_not_bypassed_by_status_or_package_fast_path() -> None:
    word = DocxTextExtractor()
    original = word.extract_with_notes
    with patch.object(word, "extract_with_notes", wraps=original) as called:
        observed = extractor(word).extract_document_with_provenance(structured())
    assert called.call_count == 1 and observed.result.selected_parts[0].word_view_status is None
    reader = extractor()
    original_parts = reader.extract_parts
    with patch.object(reader, "extract_parts", wraps=original_parts) as parts_called:
        observed = reader.extract_document_with_provenance(structured())
    assert (
        parts_called.call_count == 1 and observed.result.selected_parts[0].word_view_status is None
    )


@pytest.mark.parametrize("failure", [ValueError("actual parser"), KeyboardInterrupt("actual stop")])
def test_failed_metadata_exit_cannot_replace_original_parser_object_or_cause(
    failure: BaseException,
) -> None:
    original_scope = WordStructureCollector.scope
    cause = OSError("original cause")
    failure.__cause__ = cause

    @contextmanager
    def broken_exit(self: WordStructureCollector) -> Iterator[None]:
        try:
            with original_scope(self):
                yield
        finally:
            raise RuntimeError("metadata exit")

    with (
        patch.object(WordStructureCollector, "scope", broken_exit),
        patch("lexinform.adapters.document_text.ET.fromstring", side_effect=failure) as parsed,
        pytest.raises(type(failure)) as caught,
    ):
        DocxTextExtractor().extract_with_view_status(structured())
    assert caught.value is failure and caught.value.__cause__ is cause
    assert parsed.call_count == 1


@pytest.mark.parametrize("stage", ["setup", "enter", "hook", "finish", "exit"])
def test_new_status_guards_propagate_cancellation_exactly_and_restore_outer_scope(
    stage: str,
) -> None:
    failure: BaseException = (
        KeyboardInterrupt("cancel") if stage in ("setup", "finish", "exit") else SystemExit("stop")
    )
    original_scope = WordStructureCollector.scope
    outer = WordStructureCollector()

    @contextmanager
    def cancelled_exit(self: WordStructureCollector) -> Iterator[None]:
        with original_scope(self):
            yield
        raise failure

    target = {
        "setup": "for_view_status",
        "enter": "scope",
        "hook": "support",
        "finish": "finish_view_status",
        "exit": "scope",
    }[stage]
    arguments = {"new": cancelled_exit} if stage == "exit" else {"side_effect": failure}
    with outer.scope():
        with (
            patch.object(WordStructureCollector, target, **arguments),
            pytest.raises(type(failure)) as caught,
        ):
            DocxTextExtractor().extract_with_view_status(structured())
        assert caught.value is failure
        assert current_word_structure() is outer


def test_cleanup_cancellation_preserves_already_active_parser_error_and_cause() -> None:
    original_scope = WordStructureCollector.scope
    failure = ValueError("original parser")
    cause = OSError("original cause")
    failure.__cause__ = cause

    @contextmanager
    def cancelled_exit(self: WordStructureCollector) -> Iterator[None]:
        try:
            with original_scope(self):
                yield
        finally:
            raise SystemExit("cleanup cancellation")

    with (
        patch.object(WordStructureCollector, "scope", cancelled_exit),
        patch("lexinform.adapters.document_text.ET.fromstring", side_effect=failure),
        pytest.raises(ValueError) as caught,
    ):
        DocxTextExtractor().extract_with_view_status(structured())
    assert caught.value is failure and caught.value.__cause__ is cause


def test_unrelated_handled_exception_cannot_hide_successful_walk_cleanup_cancellation() -> None:
    original_scope = WordStructureCollector.scope
    cancellation = KeyboardInterrupt("cleanup")

    @contextmanager
    def cancelled_exit(self: WordStructureCollector) -> Iterator[None]:
        with original_scope(self):
            yield
        raise cancellation

    try:
        raise ValueError("unrelated caller handler")
    except ValueError:
        with (
            patch.object(WordStructureCollector, "scope", cancelled_exit),
            pytest.raises(KeyboardInterrupt) as caught,
        ):
            DocxTextExtractor().extract_with_view_status(structured())
    assert caught.value is cancellation


def classifier_package(namespace: str, depth: int) -> bytes:
    body = (
        "<w:p><w:r><w:t>Nie stosuje się do art. 7; 500 zł przez 30 dni.</w:t>"
        '<w:tab/><w:br w:type="page"/><w:cr/><w:lastRenderedPageBreak/>'
        '<w:footnoteReference w:id="2"/><w:endnoteReference w:id="3"/></w:r>'
        "<w:unknownInline><w:t>zakres</w:t></w:unknownInline><w:unknownLeaf/>"
        "<mc:Fallback><w:r><w:t>NIE CZYTAĆ</w:t></w:r></mc:Fallback></w:p>"
    )
    for _ in range(depth):
        body = "<w:sdt><w:sdtContent>" + body + "</w:sdtContent></w:sdt>"
    body += (
        "<w:unknownBlock><w:p><w:r><w:t>SKIPPED</w:t></w:r></w:p></w:unknownBlock>"
        "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>art. 9</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
    )
    prefix = (
        f'<w:document xmlns:w="{namespace}" '
        'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"><w:body>'
    )
    return archive(
        {
            "word/document.xml": (prefix + body + "</w:body></w:document>").encode(),
            "word/footnotes.xml": (
                f'<w:footnotes xmlns:w="{namespace}"><w:footnote w:id="2">'
                "<w:p><w:r><w:t>30 dni</w:t></w:r></w:p></w:footnote></w:footnotes>"
            ).encode(),
            "word/endnotes.xml": (
                f'<w:endnotes xmlns:w="{namespace}"><w:endnote w:id="3">'
                "<w:p><w:r><w:t>Nie mniej niż 500 zł</w:t></w:r></w:p>"
                "</w:endnote></w:endnotes>"
            ).encode(),
        }
    )


@pytest.mark.parametrize(
    "namespace",
    [
        "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "http://purl.oclc.org/ooxml/wordprocessingml/main",
        "http://schemas.microsoft.com/office/word/2003/wordml",
    ],
)
@pytest.mark.parametrize("depth", [0, 2])
def test_status_classifier_preserves_namespace_depth_notes_fallback_and_unknown_nodes(
    namespace: str, depth: int
) -> None:
    data = classifier_package(namespace, depth)
    reader = DocxTextExtractor()
    before = reader.extract_with_notes(data)
    observed = reader.extract_with_view_status(data)
    assert observed.legacy.model_dump_json() == before.model_dump_json()
    assert observed.status is not None, "The same reached Word walk completes its counters"
    assert (
        observed.status.observed_skipped_blocks,
        observed.status.observed_unclassified_nodes,
        observed.status.observed_legacy_recursion,
        observed.status.observed_ignored_fallbacks,
    ) == (1, 2, 5, 1)
    assert "Nie stosuje się" in before.body.legacy_text and "500 zł" in before.body.legacy_text
    assert "NIE CZYTAĆ" not in before.body.legacy_text and "SKIPPED" not in before.body.legacy_text
    package_reader = extractor()
    physical = package_reader.extract_parts(data)
    current = package_reader.extract_document_with_provenance(data)
    assert current.document == package_reader.extract_document(data)
    assert (
        current.result.physical_manifest_sha256
        == extraction_records.summarize_extraction(
            physical,
            current.document,
            components=current.result.components,
            options=current.result.options,
        ).physical_manifest_sha256
    )


def unsupported_namespace_package() -> bytes:
    namespace = "urn:unsupported:" + "x" * 8192
    return archive(
        {
            "word/document.xml": (
                b'<w:document xmlns:w="http://schemas.openxmlformats.org/'
                b'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>USTAWA</w:t>'
                b'<w:footnoteReference w:id="2"/></w:r></w:p></w:body></w:document>'
            ),
            "word/footnotes.xml": (
                f'<w:footnotes xmlns:w="{namespace}"><w:footnote w:id="2">'
                "<w:p><w:r><w:t>30 dni</w:t></w:r></w:p></w:footnote></w:footnotes>"
            ).encode(),
        }
    )


def test_unsupported_note_namespace_retains_legacy_and_unknown_walk_evidence() -> None:
    data = unsupported_namespace_package()
    reader = DocxTextExtractor()
    before = reader.extract_with_notes(data)
    observed = reader.extract_with_view_status(data)
    assert observed.legacy.model_dump_json() == before.model_dump_json()
    assert observed.status is not None, "The unsupported note part retains its interrupted walk"
    assert observed.status.known_walk == "interrupted"
    assert observed.status.observed_unclassified_nodes == 2
    assert "30 dni" in observed.legacy.body.legacy_text
