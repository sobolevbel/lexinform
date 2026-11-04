import hashlib
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from typing import Never

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.input_receipts import supplement_input_receipt
from lexinform.models import (
    AnalysisRecord,
    BatchIntent,
    BatchRequest,
    Bill,
    BillStatus,
    DigestItemMeta,
    ProcessDetail,
    ScannedDocument,
    SupplementContext,
    SupplementQuestion,
    TextDocument,
)
from lexinform.sections import TextBudget
from lexinform.services import analysis as analysis_module
from lexinform.services.analysis import AnalysisOptions, AnalysisService, Waiting
from lexinform.services.document_history import PhysicalSourceRead, SourceReadCapture
from lexinform.services.documents import TextLoader
from lexinform.services.input_receipts import capture_supplement_input_receipt
from lexinform.services.sources import MetadataOnlySource, TextSources
from lexinform.text_projection import SourcePiece, TextProjection, project_pieces
from tests.fakes import FakeBatchBackend, FakeLlm, FixedClock, make_analysis
from tests.unit.test_input_receipts import NOW, SOURCE
from tests.unit.test_physical_extraction import docx, extractor
from tests.unit.test_primary_input_receipt_capture import captured

URL = "https://example.test/supplement.docx"


def supplement_request(text: str) -> BatchRequest:
    ctx = SupplementContext(
        number="3039",
        title="Projekt",
        document_title="Stanowisko Rządu",
        source_kind="government_position",
        text=text,
        truncated=False,
        previous_summary="Сохранённое описание",
        previous_key_changes=["Предыдущие изменения"],
    )
    return BatchRequest(
        custom_id="frozen-supplement", term=10, number="3039", question=SupplementQuestion(ctx=ctx)
    ).freeze(
        payload_json=ctx.model_dump_json(),
        model="fake-batch",
        prompt_version="fixed",
        estimated_cost_usd=0.1,
    )


class CountingBatch(FakeBatchBackend):
    def __init__(self) -> None:
        super().__init__()
        self.prepared: list[BatchRequest] = []
        self.failure: BaseException | None = None

    def prepare_request(self, request: BatchRequest) -> BatchRequest:
        self.prepared.append(request)
        if self.failure is not None:
            raise self.failure
        return super().prepare_request(request)


@dataclass(frozen=True)
class Case:
    service: AnalysisService
    batch: CountingBatch
    bill: Bill
    document: TextDocument


def supplement_case(
    repo: SqliteBillRepository, process: ProcessDetail, *, budget: int = 60000
) -> Case:
    repo.upsert_summary(process, now=NOW)
    record = AnalysisRecord(
        analysis=make_analysis(),
        model="previous",
        prompt_version="previous",
        input_chars=1,
        truncated=False,
        text_source="pdf",
        created_at=NOW,
    )
    repo.save_analysis(process.term, process.number, record)
    repo.set_status(process.term, process.number, BillStatus.ANALYZED)
    bill = repo.get(process.term, process.number)
    assert bill is not None and bill.analysis is not None, "the fixture enrolls an analysed owner"
    data = docx(SOURCE * 40)
    batch = CountingBatch()

    def read(url: str, *, max_bytes: int | None = None) -> bytes:
        return data

    service = AnalysisService(
        repo,
        TextSources(MetadataOnlySource()),
        TextLoader({"example.test": read}, extractor(), max_bytes=len(data)),
        FakeLlm(),
        FixedClock(NOW),
        AnalysisOptions(
            require_batch=True, batch_provider="openai", batch_models={"supplement": "fake"}
        ),
        text_budget=TextBudget(budget),
        batch=batch,
    )
    return Case(service, batch, bill, TextDocument(url=URL, kind="government_position"))


def test_supplement_builder_maps_source_and_generated_spans_without_changing_frozen_request() -> (
    None
):
    selected, source = captured()
    text = selected.parts[0].text
    projected = project_pieces(
        text, (SourcePiece(text[:60], 0), SourcePiece(" OMIT "), SourcePiece(text[90:], 90))
    )
    request = supplement_request(projected.text)
    before = request.model_dump_json()
    receipt = supplement_input_receipt(
        request,
        selected,
        projection=projected,
        known_selection=source.selection,
        body=source.artifacts[0],
        extraction=source.extractions[0],
    )
    assert receipt.text_sha256 == hashlib.sha256(projected.text.encode()).hexdigest()
    assert [segment.origin for segment in receipt.segments] == ["source", "generated", "source"]
    assert receipt.segments[1].source_start is None
    assert receipt.segments[1].system_origin == "input_transform"
    assert receipt.source_selection_id == source.selection.selection_id
    assert receipt.extraction_id == source.extractions[0].result_id
    assert request.model_dump_json() == before


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "body",
        "extraction",
        "no_extraction",
        "physical",
        "scan",
        "multipart",
        "projection",
        "package",
    ],
)
def test_supplement_capture_leaves_unsupported_or_ambiguous_sources_unstamped(fault: str) -> None:
    selected, source = captured()
    text = selected.parts[0].text
    projection: TextProjection | None = project_pieces(text, (SourcePiece(text, 0),))
    actual: SourceReadCapture | None = source
    request = supplement_request(text)
    if fault == "missing":
        actual = None
    elif fault == "no_extraction":
        actual = replace(source, extractions=())
    elif fault == "physical":
        actual = PhysicalSourceRead(source.artifacts, extractions=source.extractions)
    elif fault == "scan":
        assert isinstance(request.question, SupplementQuestion), "the fixture builds a supplement"
        scan = ScannedDocument(data="ZmlsZQ==", pages=1, of_pages=1, sha256="a" * 64)
        request = request.model_copy(
            update={
                "question": SupplementQuestion(
                    ctx=request.question.ctx.model_copy(update={"scan": scan})
                )
            }
        )
    elif fault == "body":
        actual = replace(
            source,
            artifacts=(*source.artifacts, source.artifacts[0].model_copy(update={"byte_size": 1})),
        )
    elif fault == "extraction":
        different = source.extractions[0].model_copy(
            update={"package_policy_source_sha256": "a" * 64}
        )
        actual = replace(source, extractions=(*source.extractions, different))
    elif fault == "multipart":
        selected = selected.model_copy(update={"parts": selected.parts * 2})
    elif fault == "projection":
        projection = None
    else:
        selected = selected.model_copy(
            update={"parts": (selected.parts[0].model_copy(update={"members": ("a.docx",)}),)}
        )
    assert capture_supplement_input_receipt(request, selected, projection, actual) is None


@pytest.mark.parametrize("budget", [60000, 350])
def test_public_supplement_queues_once_and_retains_exact_request_and_known_parents(
    repo: SqliteBillRepository, process_3039: ProcessDetail, budget: int
) -> None:
    case = supplement_case(repo, process_3039, budget=budget)
    result = case.service.digest_supplement(
        case.bill, case.document, number="3039-s", title="Stanowisko Rządu"
    )
    assert isinstance(result, Waiting) and len(case.batch.prepared) == 1
    intent = repo.list_queued_batch_intents()[0]
    assert isinstance(intent.meta, DigestItemMeta), "supplements queue secondary metadata"
    receipt = intent.meta.prepared_input_receipt
    assert receipt is not None, "a native single loose text source is supported"
    assert receipt.artifact_sha256 and receipt.extraction_id and receipt.source_selection_id
    assert receipt.request_sha256 == intent.request.payload_sha256
    assert isinstance(intent.request.question, SupplementQuestion), (
        "the queued input is a supplement"
    )
    assert receipt.text_chars == len(intent.request.question.ctx.text)
    assert receipt.text_chars <= budget
    assert intent.request.payload_json == intent.request.ctx.model_dump_json()
    assert intent.request.custom_id == intent.meta.memo_key
    case.service.digest_supplement(
        case.bill, case.document, number="3039-s", title="Stanowisko Rządu"
    )
    assert len(case.batch.prepared) == 1 and len(repo.list_queued_batch_intents()) == 1


@pytest.mark.parametrize(
    "hook", ["capture_supplement_input_receipt", "enrich_supplement_input_intent"]
)
@pytest.mark.parametrize("error", [ValueError("metadata"), KeyboardInterrupt(), SystemExit(4)])
def test_optional_capture_failures_preserve_intent_but_cancellation_propagates(
    repo: SqliteBillRepository,
    process_3039: ProcessDetail,
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
    hook: str,
) -> None:
    case = supplement_case(repo, process_3039)

    def fail(*args: object, **kwargs: object) -> Never:
        raise error

    monkeypatch.setattr(analysis_module, hook, fail)
    if isinstance(error, Exception):
        result = case.service.digest_supplement(
            case.bill, case.document, number="3039-s", title="Stanowisko Rządu"
        )
        assert isinstance(result, Waiting)
        assert repo.list_queued_batch_intents()[0].meta.prepared_input_receipt is None
    else:
        with pytest.raises(type(error)) as caught:
            case.service.digest_supplement(
                case.bill, case.document, number="3039-s", title="Stanowisko Rządu"
            )
        assert caught.value is error and not repo.list_queued_batch_intents()
    assert len(case.batch.prepared) == 1


def test_supplement_receipt_survives_public_collect_and_cleanup_without_new_charge(
    tmp_path: Path, process_3039: ProcessDetail
) -> None:
    with closing(SqliteBillRepository(tmp_path / "supplement.db")) as repo:
        repo.migrate()
        case = supplement_case(repo, process_3039)
        case.service.digest_supplement(
            case.bill, case.document, number="3039-s", title="Stanowisko Rządu"
        )
        original = repo.list_queued_batch_intents()[0]
        case.service.submit_queued_batches()
        assert len(case.batch.prepared) == len(case.batch.submitted) == 1
        assert case.batch.submitted[0] == original.request
        case.batch.resolve()
        case.service.collect_batches()
        paid = repo.list_computation_results(case.bill.term, case.bill.number, purpose="supplement")
        assert len(paid) == 1 and paid[0].request_sha256 == original.request.payload_sha256
        repo.prune_history(before=NOW + timedelta(days=365), memo_before=NOW + timedelta(days=365))
        script = repo.dump()
        repo.restore(script)
        assert repo.dump() == script
        assert (
            repo.list_computation_results(case.bill.term, case.bill.number, purpose="supplement")
            == paid
        )
        assert len(case.batch.prepared) == len(case.batch.submitted) == 1
        assert '"supplement-single-part-text-v1"' in script


@pytest.mark.parametrize("boundary", ["prepare", "intent"])
def test_existing_prepare_and_intent_errors_are_not_hidden_by_optional_capture(
    repo: SqliteBillRepository,
    process_3039: ProcessDetail,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    case = supplement_case(repo, process_3039)
    error = RuntimeError("existing boundary failed")
    if boundary == "prepare":
        case.batch.failure = error
    else:

        def fail(*args: object, **kwargs: object) -> Never:
            raise error

        monkeypatch.setattr(BatchIntent, "from_request", fail)
    with pytest.raises(RuntimeError) as caught:
        case.service.digest_supplement(
            case.bill, case.document, number="3039-s", title="Stanowisko Rządu"
        )
    assert caught.value is error and not repo.list_queued_batch_intents()
    assert len(case.batch.prepared) == 1


def test_supplement_builder_retains_an_honest_bounded_prefix_of_a_large_observed_map() -> None:
    selected, source = captured()
    text = selected.parts[0].text
    pieces = tuple(
        piece
        for index, char in enumerate(text)
        for piece in (SourcePiece(char, index), SourcePiece("!"))
    )
    projection = project_pieces(text, pieces)
    receipt = capture_supplement_input_receipt(
        supplement_request(projection.text), selected, projection, source
    )
    assert receipt is not None, "the complete native loose source and its projection are known"
    assert receipt.retention == "prefix" and receipt.reason == "segment_limit"
    assert receipt.segment_count == len(text) * 2 and len(receipt.segments) == 32
    assert receipt.segments[-1].end < receipt.text_chars
    assert len(receipt.model_dump_json().encode()) <= 8192


def test_post_prepare_text_change_keeps_actual_request_and_omits_uncorroborated_receipt(
    repo: SqliteBillRepository, process_3039: ProcessDetail, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = supplement_case(repo, process_3039)
    prepare = case.batch.prepare_request
    returned: list[BatchRequest] = []

    def fit(request: BatchRequest) -> BatchRequest:
        assert isinstance(request.question, SupplementQuestion), (
            "the public call prepares a supplement"
        )
        changed = request.question.ctx.model_copy(update={"text": request.question.ctx.text[:100]})
        actual = prepare(request.model_copy(update={"question": SupplementQuestion(ctx=changed)}))
        returned.append(actual)
        return actual

    monkeypatch.setattr(case.batch, "prepare_request", fit)
    result = case.service.digest_supplement(
        case.bill, case.document, number="3039-s", title="Stanowisko Rządu"
    )
    assert isinstance(result, Waiting) and len(case.batch.prepared) == len(returned) == 1
    queued = repo.list_queued_batch_intents()[0]
    assert queued.request == returned[0]
    assert isinstance(queued.request.question, SupplementQuestion), (
        "the actual prepared question is retained"
    )
    assert len(queued.request.question.ctx.text) == 100
    assert queued.request.payload_json == queued.request.question.ctx.model_dump_json()
    assert queued.meta.prepared_input_receipt is None
    case.service.submit_queued_batches()
    assert case.batch.submitted == returned and len(case.batch.prepared) == 1
