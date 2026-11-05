import hashlib
from contextvars import copy_context
from dataclasses import replace
from threading import Thread

import pytest

from lexinform.archive_dispositions import enrich_archive_trace
from lexinform.document_admissibility import review_admission
from lexinform.errors import SejmApiUnavailableError
from lexinform.models import (
    CandidateDisposition,
    CandidateDispositionTrace,
    DocumentPart,
    DocumentSelection,
    TextDocument,
)
from lexinform.models.candidate_trace import MAX_CANDIDATE_TRACE_BYTES
from lexinform.models.document_reads import LoadedFile
from lexinform.required_part_capture import RequiredPartCapture
from lexinform.services.document_history import DocumentSourceRead
from lexinform.services.documents import TextLoader
from tests.fakes import FakeSejmGateway, FakeTextExtractor
from tests.unit.test_archive_member_dispositions import ROOT
from tests.unit.test_archive_member_dispositions import row as archive_row

MAIN = "https://source.test/main"
REQUIRED = "https://source.test/required"
BODY = (
    "Projekt z dnia 1 września 2026 r.\nUSTAWA\nz dnia …\nArt. 1. Termin wynosi 30 dni.\nArt. 2. Ustawa wchodzi w życie po upływie 14 dni od dnia ogłoszenia.\n"
    + "Cudzoziemiec składa wniosek. " * 30
)
APPENDIX = "Projekt\nROZPORZĄDZENIE\nMINISTRA ...\n" + "r" * 500


def native(required_count: int = 1) -> tuple[TextLoader, FakeSejmGateway, TextDocument]:
    urls = tuple(REQUIRED + str(index) for index in range(required_count))
    inputs = {MAIN: BODY.encode(), **{url: APPENDIX.encode() for url in urls}}
    gateway = FakeSejmGateway(files=inputs)
    loader = TextLoader(
        {"source.test": gateway.download},
        FakeTextExtractor(
            by_content={data: data.decode() for data in inputs.values()}, page_count=0
        ),
        max_bytes=100000,
    )
    return loader, gateway, TextDocument(url=MAIN, kind="print", required_urls=urls)


def test_native_required_exclusion_has_actual_body_free_scope_without_selection_changes() -> None:
    loader, gateway, document = native()
    _, loaded = loader.select_document(document)
    assert (
        loaded.text == BODY
        and not loaded.selection.rejected
        and not loaded.selection.observed_parts
    )
    trace = loaded.candidate_trace
    assert trace is not None and trace.required_exclusions is not None, (
        "the actual required-part exclusion is durable trace evidence"
    )
    section = trace.required_exclusions
    assert section.observed_rows == 1 and section.retention == "complete"
    row = section.rows[0]
    assert (row.phase, row.index, row.required_index) == ("fallback", 0, 0)
    assert (
        row.kind == "regulation"
        and row.file_sha256 == hashlib.sha256(APPENDIX.encode()).hexdigest()
    )
    assert row.parts and all(not part.text for part in row.parts)
    assert row.parts[0].url == document.required_urls[0]
    assert len(gateway.calls) == 2
    direct = loader.read_document(document)
    assert direct.text == loaded.text and direct.candidate_trace is None
    assert not direct.selection.rejected and not direct.selection.observed_parts


def test_optional_capture_fault_preserves_exact_existing_source_and_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, _, document = native()
    chosen, loaded = first.select_document(document)
    original_capture = first.capture_source_read(document, chosen, loaded)
    assert isinstance(original_capture, DocumentSourceRead), (
        "the native technical URLs admit metadata"
    )
    second, _, same = native()

    def broken(
        self: RequiredPartCapture,
        phase: str,
        index: int,
        position: int,
        kind: str,
        file: LoadedFile,
    ) -> None:
        raise RuntimeError("optional capture failed")

    monkeypatch.setattr(RequiredPartCapture, "observe", broken)
    baseline_chosen, baseline = second.select_document(same)
    baseline_capture = second.capture_source_read(same, baseline_chosen, baseline)
    assert replace(loaded, candidate_trace=None) == baseline
    assert replace(original_capture, candidate_trace=None) == baseline_capture
    assert (
        review_admission(
            loaded.selection,
            loaded.text or "",
            pages=loaded.pages,
            min_chars=200,
            oversize=False,
            role="project_text",
        )
        == "complete"
    )


@pytest.mark.parametrize("error", [KeyboardInterrupt, SystemExit])
def test_capture_cancellation_propagates_and_invocation_scope_is_restored(
    monkeypatch: pytest.MonkeyPatch, error: type[BaseException]
) -> None:
    loader, _, document = native()

    def cancel(
        self: RequiredPartCapture,
        phase: str,
        index: int,
        position: int,
        kind: str,
        file: LoadedFile,
    ) -> None:
        raise error("stop")

    with monkeypatch.context() as patcher:
        patcher.setattr(RequiredPartCapture, "observe", cancel)
        with pytest.raises(error):
            loader.select_document(document)
    _, loaded = loader.select_document(document)
    assert (
        loaded.candidate_trace is not None
        and loaded.candidate_trace.required_exclusions is not None
    )
    assert loaded.candidate_trace.required_exclusions.observed_rows == 1


@pytest.mark.parametrize("padding", [0, 2600, 3500])
def test_combined_archive_and_required_bounds_preserve_both_full_summaries(padding: int) -> None:
    document = TextDocument(url=MAIN + "x" * padding, kind="print", required_urls=(REQUIRED,))
    trace = CandidateDispositionTrace.from_decisions(
        document,
        (
            CandidateDisposition(
                phase="fallback",
                index=0,
                outcome="selected",
                reason="first_acceptable_candidate",
                observed_sha256=ROOT,
            ),
        ),
        result="selected",
        reason="first_acceptable_candidate",
        selection_state="unverified",
    )

    def archive(value: CandidateDispositionTrace) -> CandidateDispositionTrace:
        return enrich_archive_trace(
            value,
            (archive_row(stage="walk"), archive_row()),
            walk="finished",
            unvisited="none_known",
        )

    trace = archive(trace)
    original = trace.archive_members
    capture = RequiredPartCapture()
    capture.observe("fallback", 0, 0, "regulation", LoadedFile(APPENDIX))
    enriched = capture.enrich(trace, archive)
    members, required = enriched.archive_members, enriched.required_exclusions
    assert members is not None and required is not None
    assert members.observed_rows == 2
    if original is not None:
        assert members.rows_sha256 == original.rows_sha256
    assert required.observed_rows == 1
    assert (
        len(enriched.model_dump_json(exclude_defaults=True).encode()) <= MAX_CANDIDATE_TRACE_BYTES
    )
    if enriched.coverage != "complete":
        assert not members.rows and not required.rows


def test_overlimit_required_stream_has_an_exact_omission_digest() -> None:
    loader, _, document = native(30)
    _, loaded = loader.select_document(document)
    trace = loaded.candidate_trace
    assert trace is not None and trace.required_exclusions is not None, (
        "all actual excluded relations retain a bounded summary"
    )
    section = trace.required_exclusions
    assert section.observed_rows == section.omitted_rows == 30 and not section.rows
    expected = RequiredPartCapture()
    for index, url in enumerate(document.required_urls):
        expected.observe("fallback", 0, index, "regulation", loader.read(url))
    same = expected.enrich(
        CandidateDispositionTrace.from_decisions(
            document,
            (
                CandidateDisposition(
                    phase="fallback",
                    index=0,
                    outcome="selected",
                    reason="first_acceptable_candidate",
                ),
            ),
            result="selected",
            reason="first_acceptable_candidate",
            selection_state="unverified",
        )
    )
    assert (
        same.required_exclusions is not None
        and same.required_exclusions.rows_sha256 == section.rows_sha256
    )


def test_alternative_and_required_indices_are_actual_not_inferred() -> None:
    loader, gateway, second = native(2)
    document = TextDocument(
        url="https://foreign.test/blocked",
        kind="print",
        official_link_conflict=True,
        alternatives=(second,),
    )
    _, loaded = loader.select_document(document)
    trace = loaded.candidate_trace
    assert trace is not None and trace.required_exclusions is not None
    assert [(row.index, row.required_index) for row in trace.required_exclusions.rows] == [
        (1, 0),
        (1, 1),
    ]
    assert len(gateway.calls) == 3


def test_ambiguous_version_never_fabricates_required_read_evidence() -> None:
    newer = MAIN + "-new"
    inputs = {MAIN: BODY.encode(), newer: BODY.replace("30 dni", "40 dni").encode()}
    gateway = FakeSejmGateway(files=inputs)
    loader = TextLoader(
        {"source.test": gateway.download},
        FakeTextExtractor(
            by_content={data: data.decode() for data in inputs.values()}, page_count=0
        ),
        max_bytes=100000,
    )
    _, loaded = loader.select_document(
        TextDocument(
            url=MAIN,
            kind="print",
            required_urls=(REQUIRED,),
            alternatives=(TextDocument(url=newer, kind="print"),),
            compare_versions=True,
        )
    )
    assert loaded.selection.state == "ambiguous"
    assert loaded.candidate_trace is not None and loaded.candidate_trace.required_exclusions is None
    assert len(gateway.calls) == 2


def test_direct_other_loader_and_inherited_thread_scope_do_not_pollute_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader, _, document = native()
    other, _, other_document = native()
    original = loader.read_document

    def read(selected: TextDocument) -> LoadedFile:
        other.read_document(other_document)
        context = copy_context()
        thread = Thread(target=lambda: context.run(original, document))
        thread.start()
        thread.join()
        return original(selected)

    monkeypatch.setattr(loader, "read_document", read)
    _, loaded = loader.select_document(document)
    assert (
        loaded.candidate_trace is not None
        and loaded.candidate_trace.required_exclusions is not None
    )
    assert loaded.candidate_trace.required_exclusions.observed_rows == 1


@pytest.mark.parametrize("privacy", [False, True])
def test_actual_unknown_or_private_returned_metadata_is_not_inferred(
    monkeypatch: pytest.MonkeyPatch, privacy: bool
) -> None:
    loader, _, document = native()
    original = loader.read

    def read(url: str) -> LoadedFile:
        if url in document.required_urls:
            return LoadedFile(
                APPENDIX,
                selection=DocumentSelection(
                    parts=(DocumentPart(url="https://user:SECRET@source.test/private"),)
                )
                if privacy
                else DocumentSelection(),
            )
        return original(url)

    monkeypatch.setattr(loader, "read", read)
    _, loaded = loader.select_document(document)
    if privacy:
        assert loaded.candidate_trace is None
    else:
        trace = loaded.candidate_trace
        assert trace is not None and trace.required_exclusions is not None
        row = trace.required_exclusions.rows[0]
        assert row.file_sha256 is None and not row.parts
    assert (
        loaded.text == BODY
        and not loaded.selection.rejected
        and not loaded.selection.observed_parts
    )
    assert "SECRET" not in repr(loaded.candidate_trace)


def test_source_failure_keeps_its_exception_and_discards_prior_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader, _, document = native(2)
    original = loader.read

    def read(url: str) -> LoadedFile:
        if url == document.required_urls[1]:
            raise SejmApiUnavailableError("source unavailable")
        return original(url)

    with monkeypatch.context() as patcher:
        patcher.setattr(loader, "read", read)
        with pytest.raises(SejmApiUnavailableError):
            loader.select_document(document)
    _, loaded = loader.select_document(document)
    assert (
        loaded.candidate_trace is not None
        and loaded.candidate_trace.required_exclusions is not None
    )
    assert loaded.candidate_trace.required_exclusions.observed_rows == 2
