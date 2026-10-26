import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import pytest
from typer.testing import CliRunner

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.cli import app
from lexinform.errors import LlmUnavailableError
from lexinform.models import (
    AmendmentsRecord,
    AnalysisRecord,
    BatchRequest,
    BillStatus,
    DocumentReviewRecord,
    JointRecord,
    NameRecord,
    ReviewContext,
    SupplementRecord,
    TextDocument,
)
from lexinform.services.analysis import Waiting
from tests.harness import World

Kind = Literal[
    "amendments", "supplement", "joint", "name", "consultation_review", "commission_review"
]
Answer = AmendmentsRecord | SupplementRecord | JointRecord | NameRecord | DocumentReviewRecord


def retained_item(world: World, batch_id: str) -> tuple[dict[str, object], dict[str, object]]:
    with sqlite3.connect(":memory:") as database:
        database.executescript(world.repo.dump())
        row = database.execute(
            "SELECT meta_json, result_json FROM llm_batch_items WHERE batch_id = ?", (batch_id,)
        ).fetchone()
    assert row is not None and row[1] is not None, "the collected item remains in the durable dump"
    meta: dict[str, object] = json.loads(row[0])
    result: dict[str, object] = json.loads(row[1])
    return meta, result


def ask(world: World, kind: Kind) -> Answer | Waiting | None:
    bill = world.bill("3039")
    if kind == "amendments":
        return world.analysis.summarize_amendments(
            bill, TextDocument(url="https://api.test/filed.pdf", kind="committee_amendments")
        )
    if kind == "supplement":
        return world.analysis.digest_supplement(
            bill,
            TextDocument(url="https://api.test/filed.pdf", kind="government_position"),
            number="3041",
            title="Stanowisko Rządu",
        )
    if kind == "joint":
        return world.analysis.compare_joint(bill, [world.bill("3040")])
    if kind == "name":
        return world.analysis.name_bill(bill)
    return world.analysis.request_document_review(
        bill,
        ReviewContext(number=bill.number, title=bill.summary.title, purpose=kind, documents=()),
    )


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("legacy", [False, True])
def test_primary_request_identity_survives_collect_and_payload_and_memo_cleanup(
    workers: int, legacy: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(profile="production", workers=workers)
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    prepare = world.batch.prepare_request

    def old_request(request: BatchRequest) -> BatchRequest:
        return prepare(request).model_copy(update={"payload_sha256": None})

    with monkeypatch.context() as patch:
        if legacy:
            patch.setattr(world.batch, "prepare_request", old_request)
        world.run(track=False)
    request = world.batch.submitted[0]
    expected = request.payload_sha256
    assert (expected is None) == legacy
    batch_id = world.repo.list_open_llm_batches()[0].batch_id
    item = world.repo.list_llm_batch_items(batch_id)[0]
    assert item.meta.request_sha256 == expected
    world.repo.restore(world.repo.dump())
    world.batch.resolve()

    world.run(track=False)
    record = world.bill("3039").analysis

    assert record is not None, "the collected primary answer is applied to the bill"
    assert record.request_sha256 == expected
    world.run(track=False)
    world.clock.advance(days=200)
    report = world.run(track=False)
    saved = world.bill("3039").analysis
    meta, result = retained_item(world, batch_id)
    assert saved is not None, "cleanup retains the bill's paid answer"
    assert saved.request_sha256 == meta.get("request_sha256") == expected
    assert "answer" not in result and "analysis" not in result
    assert request.custom_id not in world.repo.load_analysis_memo()
    assert report.analyzed == report.reanalyzed == 0 and len(world.batch.submitted) == 1
    if legacy:
        assert "request_sha256" not in saved.model_dump()
        assert "request_sha256" not in meta


@pytest.mark.parametrize(
    "kind",
    ["amendments", "supplement", "joint", "name", "consultation_review", "commission_review"],
)
@pytest.mark.parametrize("legacy", [False, True])
def test_secondary_paid_request_identity_survives_restore_and_memo_reuse(
    kind: Kind, legacy: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(
        profile="production",
        consultation_reviews_enabled=True,
        commission_reviews_enabled=True,
        batch_kinds=frozenset(
            {"analysis", "reanalysis", "consultation_review", "commission_review"}
        ),
    )
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.add_bill("3040", "Projekt ustawy o obywatelstwie")
    world.run(track=False)
    world.batch.resolve()
    world.run(track=False)
    world.gateway.files["https://api.test/filed.pdf"] = b"%PDF-filed-document"
    world.analysis.start_run()
    prepare = world.batch.prepare_request

    def old_request(request: BatchRequest) -> BatchRequest:
        return prepare(request).model_copy(update={"payload_sha256": None})

    with monkeypatch.context() as patch:
        if legacy:
            patch.setattr(world.batch, "prepare_request", old_request)
        assert isinstance(ask(world, kind), Waiting)
    intent = world.repo.list_queued_batch_intents()[0]
    expected = intent.request.payload_sha256
    assert (expected is None) == legacy
    world.analysis.submit_queued_batches()
    batch_id = world.repo.list_open_llm_batches()[0].batch_id
    item = world.repo.list_llm_batch_items(batch_id)[0]
    assert item.meta.request_sha256 == expected
    world.repo.restore(world.repo.dump())
    world.batch.resolve()
    world.analysis.collect_batches()
    memos = world.repo.load_analysis_memo()
    world.analysis.collect_batches()
    assert world.repo.load_analysis_memo() == memos
    world.repo.restore(world.repo.dump())
    world.analysis.start_run()

    record = ask(world, kind)

    assert isinstance(
        record, (AmendmentsRecord, SupplementRecord, JointRecord, NameRecord, DocumentReviewRecord)
    ), "the secondary result is read from its validated paid memo"
    assert record.request_sha256 == expected and record.input_tokens == 0
    assert not world.repo.list_queued_batch_intents() and len(world.batch.submitted) == 3
    if legacy:
        assert "request_sha256" not in record.model_dump()


@pytest.mark.parametrize("legacy", [False, True])
def test_operator_attach_keeps_the_original_request_identity(
    legacy: bool, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    world = World(profile="production")
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    prepare = world.batch.prepare_request

    def old_request(request: BatchRequest) -> BatchRequest:
        return prepare(request).model_copy(update={"payload_sha256": None})

    def uncertain(requests: Sequence[BatchRequest]) -> str:
        raise LlmUnavailableError("injected loss of submission acknowledgement")

    with monkeypatch.context() as patch:
        if legacy:
            patch.setattr(world.batch, "prepare_request", old_request)
        patch.setattr(world.batch, "submit", uncertain)
        world.run(track=False)
    intent = world.repo.list_submitting_batch_intents()[0]
    path = tmp_path / "restored.db"
    repository = SqliteBillRepository(path)
    repository.migrate()
    repository.restore(world.repo.dump())
    repository.close()

    result = CliRunner().invoke(
        app,
        [
            "attach-batch-intents",
            "provider-accepted",
            intent.request.custom_id,
            "--confirmed-provider-batch",
        ],
        env={
            "LEXINFORM_DB_PATH": str(path),
            "LEXINFORM_TELEGRAM_BOT_TOKEN": "",
            "LEXINFORM_TELEGRAM_CHANNEL_ID": "",
            "LEXINFORM_TELEGRAM_LOG_CHANNEL_ID": "",
            "LEXINFORM_TERM": "10",
            "LEXINFORM_SEJM_API_BASE_URL": "http://127.0.0.1:9",
            "LEXINFORM_RCL_ENABLED": "false",
            "LEXINFORM_WYKAZ_ENABLED": "false",
        },
    )

    assert result.exit_code == 0, result.output
    repository = SqliteBillRepository(path)
    items = repository.list_llm_batch_items("provider-accepted")
    assert len(items) == 1 and items[0].meta.request_sha256 == intent.request.payload_sha256
    assert not repository.list_submitting_batch_intents()
    repository.close()


@pytest.mark.parametrize("legacy", [False, True])
def test_explanation_keeps_its_own_request_without_rewriting_the_bill(
    legacy: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(profile="production")
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run(track=False)
    world.batch.resolve()
    world.run(track=False)
    original = world.bill("3039").analysis
    prepare = world.batch.prepare_request

    def old_request(request: BatchRequest) -> BatchRequest:
        return prepare(request).model_copy(update={"payload_sha256": None})

    with monkeypatch.context() as patch:
        if legacy:
            patch.setattr(world.batch, "prepare_request", old_request)
        result = world.analysis.explain_bill(world.bill("3039"))
    assert isinstance(result, Waiting)
    expected = world.batch.submitted[-1].payload_sha256
    assert (expected is None) == legacy
    world.repo.restore(world.repo.dump())
    world.batch.resolve()
    world.analysis.collect_batches()

    record = world.analysis.explain_bill(world.bill("3039"))

    assert isinstance(record, AnalysisRecord), "the explanation reads its collected paid memo"
    assert record.request_sha256 == expected and record.input_tokens == 0
    assert world.bill("3039").analysis == original
    assert len(world.batch.submitted) == 2


def test_superseded_paid_result_keeps_its_request_in_audit_metadata() -> None:
    world = World(profile="production")
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run(track=False)
    request = world.batch.submitted[0]
    batch_id = world.repo.list_open_llm_batches()[0].batch_id
    world.command("/skip 3039")
    world.run(track=False)
    world.batch.resolve()

    world.run(track=False)

    bill = world.bill("3039")
    meta, result = retained_item(world, batch_id)
    assert bill.status is BillStatus.SKIPPED_PREFILTER and bill.analysis is None
    assert meta["request_sha256"] == request.payload_sha256
    assert result.get("model") and len(world.batch.submitted) == 1


def test_synchronous_compatibility_record_has_no_invented_frozen_request() -> None:
    world = World()
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    world.run(track=False)

    record = world.bill("3039").analysis
    assert record is not None, "the compatibility profile completes its synchronous analysis"
    assert record.request_sha256 is None and "request_sha256" not in record.model_dump()
