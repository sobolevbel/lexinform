import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    BatchItemMeta,
    BatchResult,
    BillStatus,
    DeliveryPlan,
    LlmBatch,
    LlmBatchItem,
    Publication,
    PublicationKind,
    PublicationStatus,
    RunMode,
    RunReport,
)
from tests.fakes import make_analysis
from tests.harness import summary

NOW = datetime(2026, 9, 24, tzinfo=UTC)
OLD = NOW - timedelta(days=200)


@pytest.fixture
def repo() -> Iterator[SqliteBillRepository]:
    repository = SqliteBillRepository(":memory:")
    repository.migrate()
    repository.upsert_summary(summary("3039", "Projekt ustawy o cudzoziemcach"), now=OLD)
    repository.set_status(10, "3039", BillStatus.ANALYZED)
    yield repository
    repository.close()


def prune(repo: SqliteBillRepository) -> dict[str, int]:
    return repo.prune_history(
        before=NOW - timedelta(days=90), memo_before=NOW - timedelta(days=180)
    )


def publication(repo: SqliteBillRepository, status: PublicationStatus) -> int:
    return repo.create_publication(
        Publication(
            term=10,
            number="3039",
            kind=PublicationKind.NEW_BILL,
            status=status,
            channel_id="test",
            created_at=OLD,
            sent_at=OLD if status is PublicationStatus.SENT else None,
            message_id=17,
            delivery=DeliveryPlan(bill_json='{"saved":"payload"}'),
        )
    )


@pytest.mark.parametrize("status", list(PublicationStatus))
def test_only_confirmed_old_sent_payloads_are_removed(
    repo: SqliteBillRepository,
    status: PublicationStatus,
) -> None:
    publication(repo, status)

    counts = prune(repo)

    saved = repo.get_publication(10, "3039", PublicationKind.NEW_BILL, "test")
    assert saved is not None and saved.message_id == 17 and saved.status is status
    assert (saved.delivery is None) == (status is PublicationStatus.SENT)
    assert counts["delivery_payloads"] == int(status is PublicationStatus.SENT)
    assert prune(repo)["delivery_payloads"] == 0


def test_old_memo_expires_but_recent_reuse_and_unowned_legacy_entries_survive(
    repo: SqliteBillRepository,
) -> None:
    for key in ("expired", "reused"):
        repo.save_analysis_memo(key, '{"paid":true}', term=10, number="3039", used_at=OLD)
    repo.save_analysis_memo("reused", '{"paid":false}', term=10, number="3039", used_at=NOW)
    repo.save_analysis_memo("legacy", '{"paid":true}')

    counts = prune(repo)

    assert counts["analysis_memos"] == 1
    assert repo.load_analysis_memo() == {"reused": '{"paid":true}', "legacy": '{"paid":true}'}


@pytest.mark.parametrize(
    "status",
    [
        BillStatus.BATCH_PENDING,
        BillStatus.ANALYSIS_READY,
        BillStatus.REANALYSIS_READY,
        BillStatus.ANALYSIS_FAILED,
    ],
)
def test_unfinished_bill_memos_never_expire(repo: SqliteBillRepository, status: BillStatus) -> None:
    repo.save_analysis_memo("paid", "{}", term=10, number="3039", used_at=OLD)
    repo.set_status(10, "3039", status)

    assert prune(repo)["analysis_memos"] == 0
    assert "paid" in repo.load_analysis_memo()


@pytest.mark.parametrize("protection", ["waiting", "queued", "failed", "pending", "unknown"])
def test_waiting_tracking_or_delivery_protects_paid_memo(
    repo: SqliteBillRepository,
    protection: str,
) -> None:
    repo.save_analysis_memo("paid", "{}", term=10, number="3039", used_at=OLD)
    if protection == "waiting":
        repo.set_awaiting_batch(10, "3039", OLD)
    else:
        publication(repo, PublicationStatus(protection))

    assert prune(repo)["analysis_memos"] == 0


def batch(repo: SqliteBillRepository, *, consumed: bool, accounted: bool, forgotten: bool) -> None:
    repo.save_llm_batch(
        LlmBatch(
            batch_id="batch",
            provider="anthropic",
            call_kind="analysis",
            submitted_at=OLD,
            status="ended",
            request_count=1,
            estimated_cost_usd=0.1,
        ),
        [
            LlmBatchItem(
                batch_id="batch",
                custom_id="paid",
                call_kind="analysis",
                term=10,
                number="3039",
                meta=BatchItemMeta(
                    input_chars=1000,
                    truncated=False,
                    text_source="pdf",
                    source_kind="print",
                    source_url=None,
                    revision=1,
                    text_sha256=None,
                    text="heavy source text",
                    memo_key="paid",
                ),
            )
        ],
    )
    if consumed:
        repo.mark_llm_batch_item_consumed(
            "batch",
            "paid",
            consumed_at=OLD,
            result=BatchResult(
                custom_id="paid",
                answer=make_analysis(),
                input_tokens=123,
                output_tokens=45,
                model="claude-opus-5",
            ),
        )
        repo.mark_llm_batch_collected("batch", completed_at=OLD)
    if accounted:
        repo.mark_batch_item_accounted("batch", "paid", at=OLD)
    if forgotten:
        repo.mark_llm_batch_forgotten("batch", forgotten_at=OLD)


@pytest.mark.parametrize(
    ("consumed", "accounted", "forgotten"),
    [(False, False, False), (True, False, True), (True, True, False), (True, True, True)],
)
def test_batch_payload_cleanup_requires_consumption_accounting_and_provider_cleanup(
    repo: SqliteBillRepository,
    consumed: bool,
    accounted: bool,
    forgotten: bool,
) -> None:
    batch(repo, consumed=consumed, accounted=accounted, forgotten=forgotten)
    repo.save_analysis_memo("paid", "{}", term=10, number="3039", used_at=OLD)

    counts = prune(repo)

    eligible = consumed and accounted and forgotten
    assert counts["batch_payloads"] == int(eligible)
    if not consumed or not accounted:
        assert "paid" in repo.load_analysis_memo()
    conn = sqlite3.connect(":memory:")
    conn.executescript(repo.dump())
    meta, result = conn.execute("SELECT meta_json, result_json FROM llm_batch_items").fetchone()
    assert ("text" not in json.loads(meta)) == eligible
    if consumed:
        decoded = json.loads(result)
        assert decoded["input_tokens"] == 123 and decoded["output_tokens"] == 45
        assert ("answer" not in decoded) == eligible
    assert conn.execute("SELECT count(*) FROM llm_batches").fetchone()[0] == 1
    conn.close()
    assert prune(repo)["batch_payloads"] == 0


def test_last_discovery_watermark_survives_a_long_period_without_runs(
    repo: SqliteBillRepository,
) -> None:
    report = RunReport(started_at=OLD, since=OLD, mode=RunMode.RUN, discovery_ok=True)
    repo.finish_run(repo.start_run(report), report)

    repo.prune_runs(before=NOW - timedelta(days=90))

    assert repo.last_discovery_started_at() == OLD


def test_retention_changes_roll_back_with_a_dry_run(repo: SqliteBillRepository) -> None:
    publication(repo, PublicationStatus.SENT)
    repo.save_analysis_memo("paid", "{}", term=10, number="3039", used_at=OLD)
    before = repo.dump()
    repo.begin()

    assert prune(repo)["delivery_payloads"] == 1
    repo.rollback()

    assert repo.dump() == before
