from collections.abc import Callable
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Never

import pytest
from tools.replay_local_timing import EventClock, enroll

from lexinform import local_timing
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    BatchIntent,
    BillStatus,
    LlmBatch,
    LlmBatchItem,
    ProcessDetail,
    ReviewSource,
    TextDocument,
)
from lexinform.models.batch import BatchKind
from lexinform.sections import TextBudget
from lexinform.services import analysis as analysis_module
from lexinform.services.analysis import AnalysisOptions, AnalysisService
from lexinform.services.documents import TextLoader
from lexinform.services.sources import MetadataOnlySource, TextSources
from tests.computations import KINDS
from tests.fakes import FakeBatchBackend, FakeLlm, FixedClock
from tests.unit.test_input_receipts import SOURCE
from tests.unit.test_local_timing_reader import NOW, timed_intent
from tests.unit.test_physical_extraction import docx, extractor
from tests.unit.test_supplement_input_capture import supplement_case


def test_actual_supplement_preparation_attaches_local_read_witness(
    process_3039: ProcessDetail,
) -> None:
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        case = supplement_case(repo, process_3039)
        case.service.digest_supplement(
            case.bill, case.document, number="1", title="OSR", may_wait=True
        )
        intent = repo.list_queued_batch_intents()[0]
        witness = intent.meta.local_timing_witness
        assert witness is not None, "newly frozen requests carry their actual local timing"
        assert witness.read_kind == "mixed"
        assert witness.read_completed_at == intent.created_at


@pytest.mark.parametrize("kind", KINDS)
def test_public_collection_copies_only_original_item_witness(
    kind: BatchKind, process_3039: ProcessDetail
) -> None:
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        repo.upsert_summary(process_3039, now=NOW)
        repo.set_status(10, "3039", BillStatus.BATCH_PENDING)
        intent = timed_intent(kind)
        backend = FakeBatchBackend()
        batch_id = backend.submit([intent.request])
        repo.save_llm_batch(
            LlmBatch(
                batch_id=batch_id,
                provider="openai",
                call_kind=kind,
                submitted_at=NOW,
                status="submitted",
                request_count=1,
                estimated_cost_usd=0,
            ),
            [LlmBatchItem.from_intent(batch_id, intent)],
        )
        service = AnalysisService(
            repo,
            TextSources(MetadataOnlySource()),
            TextLoader({}, extractor(), max_bytes=1024),
            FakeLlm(),
            FixedClock(NOW + timedelta(hours=1)),
            AnalysisOptions(require_batch=True, batch_provider="openai"),
            text_budget=TextBudget(10000),
            batch=backend,
        )
        backend.resolve()
        before_collection = repo.dump()
        service.collect_batches()
        stored = repo.list_computation_results(10, "3039", purpose=kind)
        assert len(stored) == 1
        assert stored[0].local_timing_witness == intent.meta.local_timing_witness
        original_timing = stored[0].local_timing_witness
        assert original_timing is not None and original_timing.triggered_at is not None, (
            "the first collection preserves the fixture's known original witness"
        )
        for preserve_none in (False, True):
            repo.restore(before_collection)
            retained = stored[0].model_copy(
                update={
                    "local_timing_witness": None
                    if preserve_none
                    else original_timing.model_copy(
                        update={"triggered_at": original_timing.triggered_at - timedelta(minutes=5)}
                    )
                }
            )
            repo.save_computation_result(retained)
            repeated = AnalysisService(
                repo,
                TextSources(MetadataOnlySource()),
                TextLoader({}, extractor(), max_bytes=1024),
                FakeLlm(),
                FixedClock(NOW + timedelta(hours=1)),
                AnalysisOptions(require_batch=True, batch_provider="openai"),
                text_budget=TextBudget(10000),
                batch=backend,
            )
            repeated.collect_batches()
            assert repo.get_computation_result(retained.result_id) == retained
            assert repo.list_computation_results(10, "3039", purpose=kind) == [retained]
        repo.restore(before_collection)
        repo.save_computation_result(stored[0])
        backend.secondary_script[("3039", kind)] = ValueError("Deliberate changed paid result")
        changed = AnalysisService(
            repo,
            TextSources(MetadataOnlySource()),
            TextLoader({}, extractor(), max_bytes=1024),
            FakeLlm(),
            FixedClock(NOW + timedelta(hours=1)),
            AnalysisOptions(require_batch=True, batch_provider="openai"),
            text_budget=TextBudget(10000),
            batch=backend,
        )
        with pytest.raises(ValueError, match="immutable computation result"):
            changed.collect_batches()
        assert repo.get_computation_result(stored[0].result_id) == stored[0]


@pytest.mark.parametrize("fault", ["setup", "clock", "enrich"])
@pytest.mark.parametrize("cancel", [False, True])
def test_optional_timing_failure_keeps_ordinary_preparation_or_propagates_cancellation(
    monkeypatch: pytest.MonkeyPatch, process_3039: ProcessDetail, fault: str, cancel: bool
) -> None:
    error: Exception | KeyboardInterrupt = (
        KeyboardInterrupt() if cancel else ValueError("optional timing failure")
    )

    def fail_clock() -> Never:
        raise error

    class BrokenCapture(local_timing.ReadTimingCapture):
        def __init__(self, now: Callable[[], datetime]) -> None:
            if fault == "setup":
                raise error
            super().__init__(fail_clock)

    def fail_enrich(
        intent: BatchIntent,
        read: local_timing.LocalReadTiming | None,
        *,
        discovery_at: datetime | None = None,
        requested_at: datetime | None = None,
    ) -> Never:
        raise error

    if fault == "enrich":
        monkeypatch.setattr(analysis_module, "enrich_local_timing_intent", fail_enrich)
    else:
        monkeypatch.setattr(local_timing, "ReadTimingCapture", BrokenCapture)
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        case = supplement_case(repo, process_3039)
        if cancel:
            with pytest.raises(KeyboardInterrupt):
                case.service.digest_supplement(
                    case.bill, case.document, number="1", title="OSR", may_wait=True
                )
            assert not repo.list_queued_batch_intents()
        else:
            case.service.digest_supplement(
                case.bill, case.document, number="1", title="OSR", may_wait=True
            )
            intents = repo.list_queued_batch_intents()
            assert len(intents) == 1 and intents[0].meta.local_timing_witness is None
        assert len(case.batch.prepared) == (0 if cancel and fault != "enrich" else 1)


class UnsupportedClock:
    def now(self) -> datetime:
        return NOW.replace(tzinfo=timezone(timedelta(seconds=30)))


def test_public_name_omits_optional_witness_for_unsupported_endpoint_clock() -> None:
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        bill = enroll(repo, "3039", "Controlled description", first=False)
        backend = FakeBatchBackend()
        service = AnalysisService(
            repo,
            TextSources(MetadataOnlySource()),
            TextLoader({}, extractor(), max_bytes=1024),
            FakeLlm(),
            UnsupportedClock(),
            AnalysisOptions(
                require_batch=True, batch_provider="openai", batch_models={"name": "fake"}
            ),
            text_budget=TextBudget(10000),
            batch=backend,
        )
        service.name_bill(bill, may_wait=True)
        intents = repo.list_queued_batch_intents()
        assert len(intents) == 1 and intents[0].meta.local_timing_witness is None
        assert intents[0].meta.request_sha256 is None


def test_amendment_read_clock_includes_the_recommendation_loaded_last() -> None:
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        bill = enroll(repo, "3039", "Controlled amendment target", first=False)
        clock = EventClock(True)
        reads: list[str] = []

        def download(url: str, *, max_bytes: int | None = None) -> bytes:
            reads.append(url)
            clock.completed("download_complete")
            return docx("USTAWA " + SOURCE * (40 if url.endswith("main.docx") else 30))

        backend = FakeBatchBackend()
        service = AnalysisService(
            repo,
            TextSources(MetadataOnlySource()),
            TextLoader({"example.test": download}, extractor(), max_bytes=1000000),
            FakeLlm(),
            clock,
            AnalysisOptions(
                require_batch=True, batch_provider="openai", batch_models={"amendments": "fake"}
            ),
            text_budget=TextBudget(60000),
            batch=backend,
        )
        service.summarize_amendments(
            bill,
            TextDocument(url="https://example.test/main.docx", kind="committee_amendments"),
            recommendation=TextDocument(
                url="https://example.test/recommendation.docx", kind="committee_amendments"
            ),
            may_wait=True,
        )
        intent = repo.list_queued_batch_intents()[0]
        witness = intent.meta.local_timing_witness
        assert witness is not None, (
            "the new amendment request carries its complete loading invocation"
        )
        assert reads == [
            "https://example.test/main.docx",
            "https://example.test/recommendation.docx",
        ]
        assert witness.read_completed_at == NOW + timedelta(seconds=2)
        assert witness.read_kind == "mixed"
        assert intent.created_at == witness.read_completed_at


def test_review_workers_aggregate_actual_native_read_evidence_identically() -> None:
    packets: list[str] = []
    data = docx("USTAWA " + SOURCE * 40)
    for workers in (1, 4):
        with closing(SqliteBillRepository(":memory:")) as repo:
            repo.migrate()
            bill = enroll(repo, "3039", "Controlled review target", first=False)
            downloaded: list[str] = []

            def download(
                url: str, *, max_bytes: int | None = None, _downloaded: list[str] = downloaded
            ) -> bytes:
                _downloaded.append(url)
                return data

            loader = TextLoader({"example.test": download}, extractor(), max_bytes=1000000)
            documents = tuple(
                ReviewSource(
                    identity=str(i),
                    document=TextDocument(url=f"https://example.test/{i}.docx", kind="rcl"),
                    role="project_text",
                )
                for i in range(3)
            )
            loader.read(documents[0].document.url)
            backend = FakeBatchBackend()
            service = AnalysisService(
                repo,
                TextSources(MetadataOnlySource()),
                loader,
                FakeLlm(),
                FixedClock(NOW),
                AnalysisOptions(
                    require_batch=True,
                    batch_provider="openai",
                    batch_models={"consultation_review": "fake"},
                    consultation_reviews_enabled=True,
                    batch_kinds=frozenset({"consultation_review"}),
                    workers=workers,
                ),
                text_budget=TextBudget(60000),
                batch=backend,
            )
            captures: list[local_timing.LocalReadTiming | None] = []
            context = service.review_context(
                bill, "consultation_review", documents, on_read=captures.append
            )
            assert context is not None and len(captures) == 1, (
                "the accepted review returns one invocation summary"
            )
            service.request_document_review(bill, context, local_read=captures[0])
            intent = repo.list_queued_batch_intents()[0]
            witness = intent.meta.local_timing_witness
            assert witness is not None and witness.read_kind == "mixed"
            assert len(downloaded) == 3
            packets.append(intent.model_dump_json())
    assert packets[0] == packets[1]
