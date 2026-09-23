"""Provider-adapter regressions of the 23 Sept batch review (docs/BUGS.md); all offline."""

import json
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2 as httpx
import openai
import pytest
from anthropic.types import Message

from lexinform.adapters.llm_anthropic import AnthropicAnalyzer
from lexinform.adapters.llm_openai import OpenAiAnalyzer
from lexinform.errors import LlmUnavailableError
from lexinform.models import ApplicantType, BatchRequest, BillContext
from lexinform.pricing import batch_reservation
from tests.fakes import make_analysis


class _Files:
    def __init__(self, contents: dict[str, str]) -> None:
        self.contents = contents
        self.reads: list[str] = []

    def content(self, file_id: str) -> Any:
        self.reads.append(file_id)
        return SimpleNamespace(text=self.contents[file_id])


def _openai_client(output: str, *, errors: str = "", status: str = "completed") -> Any:
    batch = SimpleNamespace(status=status, output_file_id="out", error_file_id="err")
    return SimpleNamespace(
        batches=SimpleNamespace(retrieve=lambda batch_id: batch),
        files=_Files({"out": output, "err": errors}),
    )


def _line(custom_id: str, text: str) -> str:
    return json.dumps(
        {
            "custom_id": custom_id,
            "response": {
                "status_code": 200,
                "body": {
                    "id": f"resp_{custom_id}",
                    "object": "response",
                    "created_at": 1,
                    "status": "completed",
                    "model": "gpt-5.1-original",
                    "parallel_tool_calls": False,
                    "tool_choice": "auto",
                    "tools": [],
                    "output": [
                        {
                            "id": "msg_1",
                            "type": "message",
                            "role": "assistant",
                            "status": "completed",
                            "content": [{"type": "output_text", "text": text, "annotations": []}],
                        }
                    ],
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 20,
                        "total_tokens": 120,
                        "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                        "output_tokens_details": {"reasoning_tokens": 0},
                    },
                },
            },
        }
    )


def test_openai_returns_failed_items_from_the_error_file() -> None:
    client = _openai_client(
        _line("ok", make_analysis().model_dump_json()),
        errors=json.dumps({"custom_id": "bad", "error": {"code": "invalid_request"}}),
    )
    backend = OpenAiAnalyzer(lambda: client)

    results = list(backend.fetch_results("batch-1"))

    assert {item.custom_id for item in results} == {"ok", "bad"}, client.files.reads


@pytest.mark.parametrize("status", ["expired", "cancelled"])
def test_openai_terminal_batch_with_results_is_collectible(status: str) -> None:
    client = _openai_client(_line("ok", make_analysis().model_dump_json()), status=status)
    backend = OpenAiAnalyzer(lambda: client)

    assert backend.poll("batch-1") == "ended"


def test_openai_malformed_analysis_does_not_hide_the_next_item() -> None:
    client = _openai_client(
        _line("bad", '{"relevant":') + "\n" + _line("ok", make_analysis().model_dump_json())
    )
    backend = OpenAiAnalyzer(lambda: client)

    results = list(backend.fetch_results("batch-1"))

    assert {item.custom_id for item in results} == {"ok", "bad"}
    assert results[0].error is not None and results[1].analysis is not None
    assert results[0].input_tokens == 100


def _anthropic_message(text: str, stop_reason: str) -> Message:
    return Message.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5-original",
            "content": [{"type": "text", "text": text}],
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 100, "output_tokens": 20},
        }
    )


def test_anthropic_truncated_analysis_does_not_hide_the_next_item() -> None:
    items = [
        SimpleNamespace(
            custom_id=custom_id,
            result=SimpleNamespace(type="succeeded", message=message),
        )
        for custom_id, message in (
            ("bad", _anthropic_message('{"relevant":', "max_tokens")),
            ("ok", _anthropic_message(make_analysis().model_dump_json(), "end_turn")),
        )
    ]
    client: Any = SimpleNamespace(
        messages=SimpleNamespace(batches=SimpleNamespace(results=lambda batch_id: iter(items)))
    )
    backend = AnthropicAnalyzer(client)

    results = list(backend.fetch_results("batch-1"))

    assert {item.custom_id for item in results} == {"ok", "bad"}
    assert results[0].error is not None and results[1].analysis is not None
    assert results[0].input_tokens == 100


def test_invalid_jsonl_and_envelopes_do_not_hide_later_answers() -> None:
    client = _openai_client(
        '{broken\n[]\n{"response": null}\n'
        + json.dumps({"custom_id": "bad", "response": "invalid"})
        + "\n"
        + _line("ok", make_analysis().model_dump_json())
    )
    results = list(OpenAiAnalyzer(lambda: client).fetch_results("batch-1"))
    assert [result.custom_id for result in results] == ["bad", "ok"]
    assert results[0].error is not None
    assert results[1].analysis is not None


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_missing_remote_batch_stays_recoverable(provider: str) -> None:
    response = httpx.Response(404, request=httpx.Request("GET", "https://example.test/batch"))
    error_type = anthropic.NotFoundError if provider == "anthropic" else openai.NotFoundError

    def missing(batch_id: str) -> Any:
        raise error_type("not in this workspace", response=response, body=None)

    batches = SimpleNamespace(retrieve=missing)
    client: Any = SimpleNamespace(batches=batches, messages=SimpleNamespace(batches=batches))
    backend = (
        AnthropicAnalyzer(client) if provider == "anthropic" else OpenAiAnalyzer(lambda: client)
    )
    with pytest.raises(LlmUnavailableError):
        backend.poll("batch-1")


def test_batch_result_keeps_the_model_that_answered() -> None:
    client = _openai_client(_line("ok", make_analysis().model_dump_json()))
    backend = OpenAiAnalyzer(lambda: client, model="gpt-5.1-new")

    result = next(backend.fetch_results("batch-1"))

    assert result.model == "gpt-5.1-original"


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_saved_request_keeps_model_prompt_and_output_limit_after_configuration_change(
    provider: str,
) -> None:
    client: Any = SimpleNamespace()
    original = (
        AnthropicAnalyzer(client, model="claude-opus-5", max_tokens=100)
        if provider == "anthropic"
        else OpenAiAnalyzer(lambda: client, model="gpt-5.1", max_output_tokens=100)
    )
    changed = (
        AnthropicAnalyzer(client, model="claude-sonnet-5", max_tokens=900)
        if provider == "anthropic"
        else OpenAiAnalyzer(lambda: client, model="gpt-5.1-new", max_output_tokens=900)
    )
    request = BatchRequest(
        custom_id="stable",
        call_kind="analysis",
        term=10,
        number="3039",
        ctx=BillContext(
            number="3039",
            title="Projekt ustawy",
            text="tekst",
            description=None,
            document_date=None,
            applicant_type=ApplicantType.UNKNOWN,
            truncated=False,
            text_source="pdf",
        ),
    )
    prepared = original.prepare_request(request)
    restored = BatchRequest.model_validate_json(prepared.model_dump_json())

    assert changed.prepare_request(restored) == prepared
    assert prepared.payload_json is not None
    payload = json.loads(prepared.payload_json)
    body = payload["params" if provider == "anthropic" else "body"]
    assert body["model"] == prepared.model
    assert body["max_tokens" if provider == "anthropic" else "max_output_tokens"] == 100
    assert prepared.estimated_cost_usd > batch_reservation(
        prepared.model, input_tokens=0, max_output_tokens=100
    )
