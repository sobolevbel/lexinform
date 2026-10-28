import json
import logging

import pytest

from lexinform.concurrency import fan_out
from lexinform.log_context import current_log_fields, log_context, log_extra
from lexinform.logging_setup import CorrelationFilter, JsonFormatter, TextFormatter


def test_nested_context_restores_its_parent_after_an_exception() -> None:
    assert current_log_fields() == {}
    with log_context(run_id=7):
        with pytest.raises(ValueError, match="failed"), log_context(command_id=9, run_id=8):
            assert current_log_fields() == {"run_id": 8, "command_id": 9}
            raise ValueError("failed")
        assert current_log_fields() == {"run_id": 7}
    assert current_log_fields() == {}


@pytest.mark.parametrize("workers", [1, 4])
def test_fan_out_copies_context_for_each_step_without_leaking_item_fields(workers: int) -> None:
    def step(number: int) -> dict[str, int | str]:
        assert current_log_fields() == {"run_id": 12}
        with log_context(bill_term=10, bill_number=str(number)):
            return current_log_fields()

    with log_context(run_id=12):
        result = [outcome.result() for outcome in fan_out(range(6), step, workers=workers)]
        assert current_log_fields() == {"run_id": 12}

    assert result == [
        {"run_id": 12, "bill_term": 10, "bill_number": str(number)} for number in range(6)
    ]
    assert current_log_fields() == {}


def test_record_keeps_its_emission_context_and_explicit_identifiers() -> None:
    with log_context(run_id=3, bill_number="original"):
        record = logging.makeLogRecord(
            {"msg": "local write", **log_extra(bill_number="target", publication_id=4)}
        )
        assert CorrelationFilter().filter(record)

    with log_context(run_id=100):
        payload: dict[str, object] = json.loads(JsonFormatter().format(record))
        text = TextFormatter("%(message)s").format(record)

    assert payload["run_id"] == 3
    assert payload["bill_number"] == "target"
    assert payload["publication_id"] == 4
    assert "100" not in text and '"run_id": 3' in text


def test_formatter_omits_unknown_fields_bodies_urls_and_oversized_opaque_ids() -> None:
    record = logging.makeLogRecord(
        {
            "msg": "local write",
            "correlation": {
                "run_id": 5,
                "selection_id": "s" * 257,
                "request_sha256": None,
                "bill_term": True,
                "url": "https://source.test/private",
                "body": "secret body",
            },
        }
    )

    payload: dict[str, object] = json.loads(JsonFormatter().format(record))
    text = TextFormatter("%(message)s").format(record)

    assert payload["run_id"] == 5
    assert not set(payload) & {"selection_id", "request_sha256", "bill_term", "url", "body"}
    assert text == 'local write | {"run_id": 5}'


def test_plain_record_without_context_keeps_its_existing_text() -> None:
    record = logging.makeLogRecord({"msg": "unchanged"})

    assert CorrelationFilter().filter(record)

    assert TextFormatter("%(message)s").format(record) == "unchanged"
    payload: dict[str, object] = json.loads(JsonFormatter().format(record))
    assert set(payload) == {"ts", "level", "logger", "msg"}
