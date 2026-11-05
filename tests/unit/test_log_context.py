import json
import logging
import sys

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
                "extraction_id": "e" * 257,
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
    assert not set(payload) & {
        "selection_id",
        "extraction_id",
        "request_sha256",
        "bill_term",
        "url",
        "body",
    }
    assert text == 'local write | {"run_id": 5}'


def test_plain_record_without_context_keeps_its_existing_text() -> None:
    record = logging.makeLogRecord({"msg": "unchanged"})

    assert CorrelationFilter().filter(record)

    assert TextFormatter("%(message)s").format(record) == "unchanged"
    payload: dict[str, object] = json.loads(JsonFormatter().format(record))
    assert set(payload) == {"ts", "level", "logger", "msg"}


def test_error_record_keeps_its_fields_on_the_message_line() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.makeLogRecord(
            {"msg": "failed", "exc_info": sys.exc_info(), "correlation": {"run_id": 5}}
        )

    lines = TextFormatter("%(message)s").format(record).splitlines()

    assert lines[0] == 'failed | {"run_id": 5}'
    assert lines[1] == "Traceback (most recent call last):" and lines[-1] == "ValueError: boom"


@pytest.mark.parametrize("provided", [None, "x" * 257, True, [], {}, 1.5, ""])
def test_explicit_unavailable_or_invalid_ids_mask_outer_context_across_repeated_filters(
    provided: object,
) -> None:
    record = logging.makeLogRecord(
        {"msg": "local write", "correlation": {"selection_id": provided, "unknown": "PRIVATE_BODY"}}
    )
    with log_context(run_id=7, selection_id="outer-source"):
        assert CorrelationFilter().filter(record)
    bound: object = getattr(record, "correlation", None)
    assert isinstance(bound, dict), "CorrelationFilter binds a bounded metadata dictionary"
    first = dict(bound)
    assert first["selection_id"] is None
    with log_context(run_id=99, selection_id="later-source", publication_id=999):
        assert CorrelationFilter().filter(record)
    assert getattr(record, "correlation", None) == first
    payload: dict[str, object] = json.loads(JsonFormatter().format(record))
    assert payload["run_id"] == 7
    assert not payload.keys() & {"selection_id", "publication_id", "unknown"}
    assert "PRIVATE_BODY" not in JsonFormatter().format(record)
    assert TextFormatter("%(message)s").format(record) == 'local write | {"run_id": 7}'


def test_valid_explicit_identifier_overrides_context_and_stays_bound_to_first_filter() -> None:
    record = logging.makeLogRecord(
        {
            "msg": "local write",
            "correlation": {"extraction_id": "actual-extraction", "bill_number": None},
        }
    )
    with log_context(run_id=7, extraction_id="outer-extraction", bill_number="outer-owner"):
        assert CorrelationFilter().filter(record)
    with log_context(
        run_id=99, command_id=99, extraction_id="later-extraction", bill_number="later-owner"
    ):
        assert CorrelationFilter().filter(record)
    payload: dict[str, object] = json.loads(JsonFormatter().format(record))
    assert payload["extraction_id"] == "actual-extraction"
    assert payload["run_id"] == 7
    assert not payload.keys() & {"bill_number", "command_id"}


def test_record_without_explicit_metadata_keeps_the_first_filter_context() -> None:
    record = logging.makeLogRecord({"msg": "local write"})
    with log_context(run_id=7):
        assert CorrelationFilter().filter(record)
    with log_context(run_id=99, command_id=88):
        assert CorrelationFilter().filter(record)
    payload: dict[str, object] = json.loads(JsonFormatter().format(record))
    assert payload["run_id"] == 7 and "command_id" not in payload
