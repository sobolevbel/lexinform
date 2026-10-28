from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.errors import BatchNotSubmittedError
from lexinform.models import (
    AnalysisQuestion,
    BatchIntent,
    BatchItemMeta,
    BatchProvider,
    BatchRequest,
    BillContext,
    BillStatus,
    ProcessDetail,
)
from lexinform.ports import StateCheckpoint
from lexinform.sections import TextBudget
from lexinform.services.analysis import AnalysisOptions, AnalysisService
from lexinform.services.documents import TextLoader
from lexinform.services.sources import MetadataOnlySource, TextSources
from tests.fakes import FakeBatchBackend, FakeLlm, FakeTextExtractor, FixedClock


@dataclass(frozen=True)
class BatchCase:
    service: AnalysisService
    backend: FakeBatchBackend
    intent: BatchIntent


def queued_case(
    repo: SqliteBillRepository,
    process: ProcessDetail,
    now: datetime,
    *,
    provider: BatchProvider = "openai",
    checkpoint: StateCheckpoint | None = None,
) -> BatchCase:
    repo.upsert_summary(process, now=now)
    repo.set_status(process.term, process.number, BillStatus.BATCH_PENDING)
    bill = repo.get(process.term, process.number)
    assert bill is not None, "upsert_summary stores the primary batch target"
    backend = FakeBatchBackend()
    context = BillContext(
        number=bill.number,
        title=bill.summary.title,
        description=None,
        document_date=None,
        applicant_type=bill.summary.applicant_type,
        text="Art. 1. Cudzoziemiec składa wniosek osobiście.",
        text_source="pdf",
        truncated=False,
    )
    request = backend.prepare_request(
        BatchRequest(
            custom_id="primary-question",
            term=bill.term,
            number=bill.number,
            question=AnalysisQuestion(kind="analysis", ctx=context),
        )
    )
    intent = BatchIntent.from_request(
        request=request,
        meta=BatchItemMeta(
            input_chars=len(context.text),
            truncated=False,
            text_source="pdf",
            source_kind="print",
            source_url="https://api.test/bill.pdf",
            revision=1,
            text_sha256="saved-text",
            generation=bill.analysis_generation,
            memo_key="primary-question",
        ),
        provider=provider,
        created_at=now,
    )
    repo.save_batch_intent(intent)
    service = AnalysisService(
        repo,
        TextSources(MetadataOnlySource()),
        TextLoader({}, FakeTextExtractor(), max_bytes=1024),
        FakeLlm(),
        FixedClock(now),
        AnalysisOptions(batch_provider=provider, require_batch=True),
        text_budget=TextBudget(1000),
        batch=backend,
        batch_checkpoint=checkpoint,
    )
    return BatchCase(service, backend, intent)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_configured_backend_without_a_resolver_submits_the_original_provider_and_payload(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime, provider: BatchProvider
) -> None:
    case = queued_case(repo, process_3039, now, provider=provider)

    case.service.submit_queued_batches()

    assert case.backend.submitted == [case.intent.request]
    assert repo.list_queued_batch_intents() == repo.list_submitting_batch_intents() == []
    batch = repo.list_open_llm_batches()[0]
    assert batch.provider == provider and batch.request_count == 1
    assert batch.estimated_cost_usd == case.intent.request.estimated_cost_usd


@pytest.mark.parametrize("interruption", ["status", "generation"])
def test_an_obsolete_queued_intent_is_removed_without_reaching_the_provider(
    repo: SqliteBillRepository,
    process_3039: ProcessDetail,
    now: datetime,
    interruption: Literal["status", "generation"],
) -> None:
    case = queued_case(repo, process_3039, now)
    if interruption == "status":
        repo.set_status(10, "3039", BillStatus.LINKED)
    else:
        repo.reset_bill(10, "3039", BillStatus.BATCH_PENDING)

    case.service.submit_queued_batches()

    assert not case.backend.submitted
    assert not repo.list_queued_batch_intents() and not repo.list_submitting_batch_intents()
    assert not repo.list_open_llm_batches()


@pytest.mark.parametrize("certain", [False, True])
def test_only_a_definite_pre_acceptance_refusal_makes_the_intent_automatically_retryable(
    repo: SqliteBillRepository,
    process_3039: ProcessDetail,
    now: datetime,
    monkeypatch: pytest.MonkeyPatch,
    certain: bool,
) -> None:
    case = queued_case(repo, process_3039, now)
    attempts: list[tuple[BatchRequest, ...]] = []

    def refuse(requests: Sequence[BatchRequest]) -> str:
        attempts.append(tuple(requests))
        if certain:
            raise BatchNotSubmittedError("rejected before upload")
        raise TimeoutError("receipt lost")

    with monkeypatch.context() as patch:
        patch.setattr(case.backend, "submit", refuse)
        with pytest.raises(BatchNotSubmittedError if certain else TimeoutError):
            case.service.submit_queued_batches()
    assert attempts == [(case.intent.request,)]
    assert len(repo.list_queued_batch_intents()) == int(certain)
    assert len(repo.list_submitting_batch_intents()) == int(not certain)

    case.service.submit_queued_batches()

    assert case.backend.submitted == ([case.intent.request] if certain else [])
    assert len(repo.list_submitting_batch_intents()) == int(not certain)


def test_batch_checkpoint_precedes_the_provider_and_confirms_its_exact_receipt_afterwards(
    repo: SqliteBillRepository,
    process_3039: ProcessDetail,
    now: datetime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshots: list[str] = []

    def checkpoint() -> None:
        snapshots.append(repo.dump())

    case = queued_case(repo, process_3039, now, checkpoint=checkpoint)
    original = case.backend.submit

    def submit(requests: Sequence[BatchRequest]) -> str:
        assert len(snapshots) == 1
        assert repo.list_submitting_batch_intents() == [
            case.intent.model_copy(update={"state": "submitting"})
        ]
        assert not repo.list_open_llm_batches()
        return original(requests)

    monkeypatch.setattr(case.backend, "submit", submit)

    case.service.submit_queued_batches()

    assert len(snapshots) == 2
    assert repo.list_open_llm_batches()[0].batch_id == "batch-1"
    assert not repo.list_submitting_batch_intents()
