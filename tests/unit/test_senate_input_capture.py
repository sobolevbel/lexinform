import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Never

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    AmendmentInputReceipt,
    DigestItemMeta,
    DocumentSelection,
    DocumentSelectionRecord,
    ProcessDetail,
    ScannedDocument,
    SenateResolutionIdentity,
    TextDocument,
)
from lexinform.sections import TextBudget
from lexinform.senate_documents import inspect_senate_resolution
from lexinform.services import analysis as analysis_module
from lexinform.services.analysis import AnalysisOptions, AnalysisService, Waiting
from lexinform.services.document_history import DocumentSourceRead, SourceReadCapture
from lexinform.services.documents import TextLoader
from lexinform.services.senate_documents import resolution_source_read
from lexinform.services.sources import MetadataOnlySource, TextSources
from tests.fakes import FakeLlm, FixedClock
from tests.unit.test_amendment_input_capture import amendment_case
from tests.unit.test_input_receipts import NOW, SOURCE
from tests.unit.test_physical_extraction import docx, extractor
from tests.unit.test_supplement_input_capture import Case, CountingBatch

URL = "https://example.test/senate.docx"


def senate_case(
    repo: SqliteBillRepository, process: ProcessDetail, *, prefix: bool = False
) -> tuple[Case, TextLoader, list[str], str]:
    bill = amendment_case(repo, process).bill
    resolution = (
        Path(__file__).parents[1] / "fixtures/sejm/senate_3213_resolution.txt"
    ).read_text()
    if prefix:
        resolution = (
            "Warszawa, dnia 14 października 2026 r.\nMarszałek Senatu\nMam zaszczyt przekazać uchwałę Senatu.\n"
            + resolution
        )
    data = docx(resolution.replace("\f", "\n"))
    reader = extractor()
    raw = reader.extract(data)
    evidence = inspect_senate_resolution(raw)
    assert evidence.complete and evidence.adopted_on is not None, (
        "the fixture contains an admitted Senate resolution"
    )
    calls: list[str] = []

    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        calls.append(url)
        return data if url == URL else docx(SOURCE * 40)

    loader = TextLoader({"example.test": download}, reader, max_bytes=32_000_000)
    batch = CountingBatch()
    service = AnalysisService(
        repo,
        TextSources(MetadataOnlySource()),
        loader,
        FakeLlm(),
        FixedClock(NOW),
        AnalysisOptions(
            require_batch=True, batch_provider="openai", batch_models={"amendments": "fake"}
        ),
        batch=batch,
        text_budget=TextBudget(60000),
    )
    document = TextDocument(
        url=URL,
        kind="senate_amendments",
        senate=SenateResolutionIdentity(
            sha256=hashlib.sha256(data).hexdigest(),
            subject=evidence.subject,
            adopted_on=evidence.adopted_on,
        ),
    )
    return Case(service, batch, bill, document), loader, calls, raw


@pytest.mark.parametrize("cached", [False, True])
def test_senate_identity_receipt_uses_actual_read_even_when_recommendation_is_last(
    repo: SqliteBillRepository, process_3039: ProcessDetail, cached: bool
) -> None:
    case, loader, calls, raw = senate_case(repo, process_3039)
    if cached:
        loader.read(URL)
    result = case.service.summarize_amendments(
        case.bill,
        case.document,
        recommendation=TextDocument(
            url="https://example.test/recommendation.docx", kind="committee_amendments"
        ),
        may_wait=True,
    )
    assert isinstance(result, Waiting)
    (intent,) = repo.list_queued_batch_intents()
    assert isinstance(intent.meta, DigestItemMeta), "amendments use digest metadata"
    receipt = intent.meta.prepared_input_receipt
    assert isinstance(receipt, AmendmentInputReceipt), (
        "the admitted identity fixture emits an amendment receipt"
    )
    assert case.document.senate is not None, "the fixture supplies the admitted raw identity"
    assert receipt.artifact_sha256 == case.document.senate.sha256
    assert receipt.text_sha256 == hashlib.sha256(raw.encode()).hexdigest()
    assert receipt.source_selection_id is not None and receipt.extraction_id is not None
    assert receipt.request_sha256 == intent.request.payload_sha256
    assert calls == [URL, "https://example.test/recommendation.docx"]
    assert receipt.source_part.chars == len(raw)
    repo.restore(repo.dump())


def test_senate_cover_prefix_maps_the_exact_suffix_of_the_original_source(
    repo: SqliteBillRepository, process_3039: ProcessDetail
) -> None:
    case, _, calls, raw = senate_case(repo, process_3039, prefix=True)
    result = case.service.summarize_amendments(case.bill, case.document, may_wait=True)
    assert isinstance(result, Waiting)
    (intent,) = repo.list_queued_batch_intents()
    receipt = intent.meta.prepared_input_receipt
    assert isinstance(receipt, AmendmentInputReceipt)
    text = intent.request.ctx.model_dump()["text"]
    start = len(raw) - len(text)
    assert start > 0 and raw[start:] == text
    assert receipt.source_part.text_sha256 == hashlib.sha256(raw.encode()).hexdigest()
    assert receipt.source_part.chars == len(raw)
    assert receipt.text_sha256 == hashlib.sha256(text.encode()).hexdigest()
    assert receipt.request_sha256 == intent.request.payload_sha256
    assert receipt.source_selection_id is not None and receipt.extraction_id is not None
    (segment,) = receipt.segments
    assert (segment.source_start, segment.source_end) == (start, len(raw))
    assert (segment.start, segment.end) == (0, len(text))
    assert calls == [URL]
    repo.restore(repo.dump())


@pytest.mark.parametrize("cancel", [False, True])
def test_senate_optional_projection_fault_preserves_work_and_propagates_cancellation(
    repo: SqliteBillRepository,
    process_3039: ProcessDetail,
    monkeypatch: pytest.MonkeyPatch,
    cancel: bool,
) -> None:
    case, _, calls, _ = senate_case(repo, process_3039)
    failure = KeyboardInterrupt("stop") if cancel else ValueError("optional metadata")

    def fail(*args: object, **kwargs: object) -> Never:
        raise failure

    monkeypatch.setattr(analysis_module, "project_pieces", fail)
    if cancel:
        with pytest.raises(KeyboardInterrupt) as raised:
            case.service.summarize_amendments(case.bill, case.document, may_wait=True)
        assert raised.value is failure
        assert not repo.list_queued_batch_intents()
    else:
        assert isinstance(
            case.service.summarize_amendments(case.bill, case.document, may_wait=True), Waiting
        )
        assert repo.list_queued_batch_intents()[0].meta.prepared_input_receipt is None
    assert calls == [URL]


def test_senate_changed_raw_sha_still_rejects_before_intent(
    repo: SqliteBillRepository, process_3039: ProcessDetail
) -> None:
    case, _, calls, _ = senate_case(repo, process_3039)
    assert case.document.senate is not None, "the fixture binds a Senate identity"
    changed = case.document.model_copy(
        update={"senate": case.document.senate.model_copy(update={"sha256": "f" * 64})}
    )
    with pytest.raises(ValueError, match="изменился"):
        case.service.summarize_amendments(case.bill, changed, may_wait=True)
    assert calls == [URL] and not repo.list_queued_batch_intents()


@pytest.mark.parametrize("fault", ["text_sha", "length", "known_selection"])
def test_senate_corrupt_captured_metadata_never_stamps_an_input(
    repo: SqliteBillRepository,
    process_3039: ProcessDetail,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    case, _, calls, _ = senate_case(repo, process_3039)
    original = resolution_source_read
    record_factory = DocumentSelectionRecord.from_selection

    def corrupt_source(
        loader: TextLoader,
        document: TextDocument,
        *,
        sha256: str | None,
        pages: int,
        text: str | None,
    ) -> SourceReadCapture | None:
        source = original(loader, document, sha256=sha256, pages=pages, text=text)
        assert isinstance(source, DocumentSourceRead), "the native fixture captures source parents"
        if fault == "known_selection":
            return source
        selected = source.selection.selection
        part = selected.parts[0]
        changed = part.model_copy(
            update={"text_sha256": "0" * 64}
            if fault == "text_sha"
            else {"text_end": len(text or "") + 1}
        )
        return replace(
            source, selection=record_factory(selected.model_copy(update={"parts": (changed,)}))
        )

    def corrupt_record(
        cls: type[DocumentSelectionRecord], selection: DocumentSelection
    ) -> DocumentSelectionRecord:
        result = record_factory(selection)
        if any(part.text for part in selection.parts):
            return result.model_copy(
                update={
                    "selection": result.selection.model_copy(
                        update={"policy_version": "mismatched-capture"}
                    )
                }
            )
        return result

    monkeypatch.setattr(analysis_module, "resolution_source_read", corrupt_source)
    if fault == "known_selection":
        monkeypatch.setattr(DocumentSelectionRecord, "from_selection", classmethod(corrupt_record))
    assert isinstance(
        case.service.summarize_amendments(case.bill, case.document, may_wait=True), Waiting
    )
    assert repo.list_queued_batch_intents()[0].meta.prepared_input_receipt is None
    assert calls == [URL]


def test_senate_resumed_context_cannot_borrow_a_fresh_capture(
    repo: SqliteBillRepository, process_3039: ProcessDetail
) -> None:
    case, _, calls, _ = senate_case(repo, process_3039)
    context = case.service.amendments_context(case.bill, case.document)
    assert context is not None, "the fixture provides admitted normative text"
    assert isinstance(
        case.service.summarize_amendments(case.bill, case.document, context=context, may_wait=True),
        Waiting,
    )
    assert repo.list_queued_batch_intents()[0].meta.prepared_input_receipt is None
    assert calls == [URL]


def test_senate_scan_keeps_existing_context_without_text_receipt(
    repo: SqliteBillRepository, process_3039: ProcessDetail, monkeypatch: pytest.MonkeyPatch
) -> None:
    case, _, calls, _ = senate_case(repo, process_3039)
    identity = case.document.senate
    assert identity is not None, "the fixture supplies a Senate identity"
    identity = identity.model_copy(update={"scan": True, "pages": 1})
    document = case.document.model_copy(update={"senate": identity})
    scan = ScannedDocument(data="JVBERi0=", pages=1, of_pages=1, sha256=identity.sha256)

    def load_scan(self: TextLoader, url: str, *, cover_letter: bool = True) -> ScannedDocument:
        assert url == URL and not cover_letter
        return scan

    monkeypatch.setattr(TextLoader, "load_scan", load_scan)
    assert isinstance(
        case.service.summarize_amendments(case.bill, document, may_wait=True), Waiting
    )
    (intent,) = repo.list_queued_batch_intents()
    assert intent.meta.prepared_input_receipt is None
    assert intent.request.ctx.model_dump()["scan"] is not None
    assert calls == []
