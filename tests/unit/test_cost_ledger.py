import datetime as dt

import pytest

from lexinform.models import AnalysisRecord, LlmCall
from lexinform.pricing import cost_usd
from lexinform.services.cost import CostLedger
from tests.fakes import make_analysis


def test_each_call_accounts_for_the_same_cache_cost_as_the_run_total() -> None:
    record = AnalysisRecord(
        analysis=make_analysis(),
        model="claude-opus-5",
        prompt_version="test",
        input_chars=2000,
        truncated=False,
        text_source="pdf",
        created_at=dt.datetime(2026, 9, 16, tzinfo=dt.UTC),
        input_tokens=1000,
        output_tokens=100,
        cache_read_input_tokens=2000,
        cache_creation_input_tokens=3000,
    )
    ledger = CostLedger()

    ledger.charge(record, number="2111", kind="analysis")

    assert cost_usd(ledger.calls[0].usage) == pytest.approx(ledger.spent_usd)
    assert ledger.spent_usd == pytest.approx(0.02725)


def test_old_call_records_without_cache_fields_still_load() -> None:
    call = LlmCall.model_validate(
        {"number": "2111", "kind": "analysis", "model": "claude-opus-5", "input_tokens": 1000}
    )

    assert cost_usd(call.usage) == pytest.approx(0.005)
