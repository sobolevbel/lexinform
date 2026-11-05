import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import pytest
from tools.replay_content_applications import CHANNEL, NOW, poster

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.document_fingerprints import text_digest
from lexinform.models import (
    Bill,
    BillStatus,
    DocumentReviewRecord,
    ReviewContext,
    ReviewSource,
    TextDocument,
)
from lexinform.models.batch import BatchRequest
from lexinform.models.reviews import ReviewPurpose
from lexinform.sections import TextBudget
from lexinform.services.analysis import AnalysisOptions, AnalysisService, Waiting
from lexinform.services.computation_reuse import retained_review
from lexinform.services.document_reviews import DocumentReviews
from lexinform.services.documents import TextLoader
from lexinform.services.sources import MetadataOnlySource
from lexinform.services.tracking.rollover import TermRollover
from lexinform.services.tracking_result import TrackingResult
from tests.fakes import FakeBatchBackend, FakeLlm, FakePublisher, FixedClock
from tests.harness import summary
from tests.scenario.test_commission_reviews import context as commission_context
from tests.scenario.test_document_reviews import context as consultation_context
from tests.unit.test_physical_extraction import extractor


class CountingBatch(FakeBatchBackend):
    preparations: int = 0

    def prepare_request(self, request: BatchRequest) -> BatchRequest:
        self.preparations += 1
        return super().prepare_request(request)


class Catalog(MetadataOnlySource):
    def __init__(self, source: ReviewContext) -> None:
        self.source = source

    def supporting_documents(self, bill: Bill, purpose: ReviewPurpose) -> tuple[ReviewSource, ...]:
        return tuple(
            ReviewSource(
                identity=doc.identity,
                document=TextDocument(url=doc.url, kind="rcl"),
                role=doc.role,
                snapshot=doc,
            )
            for doc in self.source.documents
        )


@dataclass
class ReviewCase:
    repo: SqliteBillRepository
    number: str
    purpose: ReviewPurpose
    catalog: Catalog
    backend: CountingBatch
    clock: FixedClock

    def services(self) -> tuple[AnalysisService, DocumentReviews]:
        analysis = AnalysisService(
            self.repo,
            self.catalog,
            TextLoader({}, extractor(), max_bytes=1024),
            FakeLlm(),
            self.clock,
            AnalysisOptions(
                require_batch=True,
                batch_provider="openai",
                batch_models={
                    kind: "fake" for kind in ("consultation_review", "commission_review")
                },
                batch_kinds=frozenset({"consultation_review", "commission_review"}),
                consultation_reviews_enabled=True,
                commission_reviews_enabled=True,
            ),
            text_budget=TextBudget(10000),
            batch=self.backend,
        )
        analysis.start_run()
        reviews = DocumentReviews(
            self.repo,
            self.catalog,
            analysis,
            poster(self.repo, FakePublisher()),
            self.clock,
            channel_id=CHANNEL,
            max_attempts=3,
        )
        return analysis, reviews

    def apply(self, term: int) -> tuple[AnalysisService, DocumentReviewRecord]:
        analysis, reviews = self.services()
        bill = self.repo.get(term, self.number)
        assert bill is not None, "the public repository retains the current government owner"
        reviews.request(bill, self.purpose, TrackingResult(), force_check=True)
        reviews.advance(TrackingResult(), publish=False)
        analysis.submit_queued_batches()
        self.backend.resolve()
        analysis.collect_batches()
        reviews.advance(TrackingResult(), publish=False)
        current = self.repo.get(term, self.number)
        assert current is not None, "review application preserves its existing bill"
        record = (
            current.consultation_review
            if self.purpose == "consultation_review"
            else current.commission_review
        )
        assert record is not None and record.computation_id is not None, (
            "public review request/collect/apply retains a paid result and current projection"
        )
        analysis.acknowledge_batch_costs()
        return analysis, record


def prepared(repo: SqliteBillRepository, number: str, purpose: ReviewPurpose) -> ReviewCase:
    repo.migrate()
    repo.upsert_summary(summary(number, "Controlled government review"), now=NOW)
    repo.set_status(10, number, BillStatus.ANALYZED)
    source = (
        commission_context() if purpose == "commission_review" else consultation_context()
    ).model_copy(update={"number": number})
    if purpose == "consultation_review":
        extra = source.documents[0].model_copy(
            update={"identity": "doc/2", "url": "https://example.test/second"}
        )
        source = source.model_copy(update={"documents": (*source.documents, extra)})
    return ReviewCase(repo, number, purpose, Catalog(source), CountingBatch(), FixedClock(NOW))


def replace_and_clean(case: ReviewCase) -> tuple[DocumentReviewRecord, str]:
    _, original = case.apply(10)
    assert original.computation_id is not None, (
        "public collection identifies the original immutable paid result"
    )
    result = case.repo.get_computation_result(original.computation_id)
    assert result is not None, "the result is retained before rollover"
    encoded = result.model_dump_json()
    assert result.input_binding is not None, "the real producer records the original input binding"
    assert original.source_key.startswith("review-input:")
    assert original.source_key != result.input_binding.reuse_key
    case.clock.advance(days=200)
    case.repo.prune_history(before=case.clock.now(), memo_before=case.clock.now())
    assert not case.repo.load_analysis_memo()
    rollover = TermRollover(
        case.repo, case.clock, poster(case.repo, FakePublisher()), channel_id=CHANNEL
    )
    outcome = TrackingResult()
    assert rollover.close_term(10, 11, outcome, publish=False)
    assert outcome.rehomed == 1
    receipts = case.repo.list_result_applications(11, case.number)
    assert any(
        item.result_id == original.computation_id
        and item.target is not None
        and item.target.ref == f"{case.purpose}/{original.source_key}"
        for item in receipts
    )
    documents = tuple(
        doc.model_copy(
            update={
                "text": doc.text + " ZMIANA",
                "sha256": hashlib.sha256((doc.text + " ZMIANA").encode()).hexdigest(),
                "normalized_sha256": text_digest(doc.text + " ZMIANA"),
            }
        )
        for doc in case.catalog.source.documents
    )
    case.catalog.source = case.catalog.source.model_copy(update={"documents": documents})
    _, replacement = case.apply(11)
    assert replacement.computation_id != original.computation_id
    case.clock.advance(days=200)
    case.repo.prune_history(before=case.clock.now(), memo_before=case.clock.now())
    assert not case.repo.load_analysis_memo()
    case.repo.restore(case.repo.dump())
    return original, encoded


@pytest.mark.parametrize("number", ["RCL/123", "WPL/UD123"])
@pytest.mark.parametrize("purpose", ["consultation_review", "commission_review"])
def test_actual_government_projection_receipt_reuses_old_review_after_replacement_and_cleanup(
    number: str, purpose: ReviewPurpose
) -> None:
    with closing(SqliteBillRepository(":memory:")) as repo:
        case = prepared(repo, number, purpose)
        original, encoded = replace_and_clean(case)
        service, _ = case.services()
        current = repo.get(11, number)
        assert current is not None, "rollover retained the current owner"
        before = case.backend.preparations
        assert not service.review_exhausted(11, original.context)
        applications = repo.list_result_applications(11, number)
        reused = service.request_document_review(current, original.context)
        assert isinstance(reused, DocumentReviewRecord), (
            "the explicit carried projection authorizes the exact original paid input"
        )
        assert reused.computation_id == original.computation_id
        assert reused.input_tokens == reused.output_tokens == 0
        assert case.backend.preparations == before and service.calls == []
        assert repo.list_result_applications(11, number) == applications
        assert original.computation_id is not None, (
            "the original current review retained its paid result ID"
        )
        retained = repo.get_computation_result(original.computation_id)
        assert retained is not None and retained.model_dump_json() == encoded
        assert (
            retained.term == 10
            and retained.input_binding is not None
            and retained.input_binding.term == 10
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "result_id",
        "phase",
        "generation",
        "owner",
        "duplicate",
        "wrong_purpose",
        "wrong_key",
    ],
)
def test_corrupt_current_owner_receipt_rejects_before_prepare(
    tmp_path: Path, mutation: str
) -> None:
    path = tmp_path / "state.db"
    with closing(SqliteBillRepository(str(path))) as repo:
        case = prepared(repo, "RCL/123", "consultation_review")
        original, encoded = replace_and_clean(case)
        with sqlite3.connect(path) as connection:
            connection.execute("PRAGMA ignore_check_constraints=ON")
            row = connection.execute(
                "SELECT id, application_json FROM result_applications WHERE term=11 AND result_id=?",
                (original.computation_id,),
            ).fetchone()
            assert row is not None, "rollover created the actual new-owner projection receipt"
            identity, raw = row
            if mutation in ("result_id", "phase", "generation"):
                values: dict[str, str | int] = {
                    "result_id": "missing",
                    "phase": "collection",
                    "generation": 9,
                }
                connection.execute(
                    f"UPDATE result_applications SET {mutation}=? WHERE id=?",
                    (values[mutation], identity),
                )
            else:
                payload = json.loads(raw)
                if mutation == "owner":
                    payload["term"] = 99
                elif mutation == "wrong_purpose":
                    payload["target"]["ref"] = "commission_review/" + original.source_key
                elif mutation == "wrong_key":
                    payload["target"]["ref"] = "consultation_review/document-review:" + "0" * 64
                raw = json.dumps(payload)
                if mutation == "duplicate":
                    raw = raw[:-1] + ',"term":11}'
                connection.execute(
                    "UPDATE result_applications SET application_json=? WHERE id=?", (raw, identity)
                )
        service, _ = case.services()
        current = repo.get(11, case.number)
        assert current is not None, "the indexed receipt corruption leaves the current owner intact"
        before = case.backend.preparations
        with pytest.raises(ValueError, match="invalid_government_review_application"):
            service.request_document_review(current, original.context)
        assert case.backend.preparations == before and service.calls == []
        assert original.computation_id is not None, "the original paid result remains identified"
        result = repo.get_computation_result(original.computation_id)
        assert result is not None and result.model_dump_json() == encoded


@pytest.mark.parametrize(
    "unknown",
    ["missing_receipt", "opaque_address", "changed_text", "reordered_inputs", "changed_scope"],
)
def test_unproven_or_changed_review_uses_ordinary_prepare(tmp_path: Path, unknown: str) -> None:
    path = tmp_path / "state.db"
    with closing(SqliteBillRepository(str(path))) as repo:
        case = prepared(repo, "RCL/123", "consultation_review")
        original, encoded = replace_and_clean(case)
        context = original.context
        if unknown in ("missing_receipt", "opaque_address"):
            with sqlite3.connect(path) as connection:
                if unknown == "missing_receipt":
                    connection.execute(
                        "DELETE FROM result_applications WHERE term=11 AND result_id=?",
                        (original.computation_id,),
                    )
                else:
                    row = connection.execute(
                        "SELECT id, application_json FROM result_applications WHERE term=11 AND result_id=?",
                        (original.computation_id,),
                    ).fetchone()
                    assert row is not None, (
                        "the controlled unknown-address fixture has a real receipt"
                    )
                    payload = json.loads(row[1])
                    payload["target"]["ref"] = "consultation_review/review-input:opaque"
                    connection.execute(
                        "UPDATE result_applications SET application_json=? WHERE id=?",
                        (json.dumps(payload), row[0]),
                    )
        elif unknown == "changed_text":
            document = context.documents[0]
            text = document.text + " zmiana zakresu"
            changed = document.model_copy(
                update={
                    "text": text,
                    "sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "normalized_sha256": text_digest(text),
                }
            )
            context = context.model_copy(update={"documents": (changed, *context.documents[1:])})
        elif unknown == "reordered_inputs":
            assert len(context.documents) > 1, (
                "the real consultation fixture includes multiple ordered inputs"
            )
            context = context.model_copy(update={"documents": tuple(reversed(context.documents))})
        else:
            context = context.model_copy(update={"comparison_sha256": "a" * 64})
        service, _ = case.services()
        current = repo.get(11, case.number)
        assert current is not None, "the current owner survives controlled authority changes"
        before = case.backend.preparations
        requested = service.request_document_review(current, context)
        assert isinstance(requested, Waiting)
        assert case.backend.preparations == before + 1
        assert original.computation_id is not None, (
            "the original computation is retained independently"
        )
        retained = repo.get_computation_result(original.computation_id)
        assert retained is not None and retained.model_dump_json() == encoded


def test_default_owner_matching_never_scans_government_receipts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(self: SqliteBillRepository, term: int, number: str) -> list[object]:
        raise AssertionError("default same-owner matching must not consult rollover authority")

    with closing(SqliteBillRepository(":memory:")) as repo:
        case = prepared(repo, "RCL/123", "consultation_review")
        original, _ = replace_and_clean(case)
        monkeypatch.setattr(SqliteBillRepository, "list_government_review_applications", forbidden)
        assert retained_review(repo, 11, original.context) is None
        current = repo.get(11, case.number)
        assert current is not None, "the controlled Sejm call starts from an existing frozen bill"
        sejm = current.model_copy(
            update={"summary": current.summary.model_copy(update={"number": "123"})}
        )
        service, _ = case.services()
        assert isinstance(
            service.request_document_review(
                sejm, original.context.model_copy(update={"number": "123"})
            ),
            Waiting,
        )


def test_known_same_number_rollover_receipt_cannot_name_another_government_result(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    with closing(SqliteBillRepository(str(path))) as repo:
        case = prepared(repo, "RCL/123", "consultation_review")
        original, encoded = replace_and_clean(case)
        other_case = prepared(repo, "RCL/999", "consultation_review")
        other_case.backend = case.backend
        _, other = other_case.apply(10)
        assert other.computation_id is not None, (
            "the second government review is actually collected"
        )
        with sqlite3.connect(path) as connection:
            row = connection.execute(
                "SELECT id, application_json FROM result_applications WHERE term=11 AND result_id=?",
                (original.computation_id,),
            ).fetchone()
            assert row is not None, "rollover created the exact current-owner projection receipt"
            payload = json.loads(row[1])
            payload["result_id"] = other.computation_id
            connection.execute(
                "UPDATE result_applications SET result_id=?, application_json=? WHERE id=?",
                (other.computation_id, json.dumps(payload), row[0]),
            )
        service, _ = case.services()
        current = repo.get(11, case.number)
        assert current is not None, (
            "tampering with a receipt preserves the current government owner"
        )
        before = case.backend.preparations
        with pytest.raises(ValueError, match="invalid_government_review_application"):
            service.request_document_review(current, original.context)
        assert case.backend.preparations == before and service.calls == []
        assert original.computation_id is not None, "the original paid result remains addressable"
        retained = repo.get_computation_result(original.computation_id)
        assert retained is not None and retained.model_dump_json() == encoded
