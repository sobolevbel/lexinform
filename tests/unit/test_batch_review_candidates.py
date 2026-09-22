"""Provider-contract probes for open batch review candidates; all responses are offline."""

import json
from types import SimpleNamespace
from typing import Any

import pytest
from anthropic.types import Message

from lexinform.adapters.llm_anthropic import AnthropicAnalyzer
from lexinform.adapters.llm_openai import OpenAiAnalyzer
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


def test_batch_result_keeps_the_model_that_answered() -> None:
    client = _openai_client(_line("ok", make_analysis().model_dump_json()))
    backend = OpenAiAnalyzer(lambda: client, model="gpt-5.1-new")

    result = next(backend.fetch_results("batch-1"))

    assert result.model == "gpt-5.1-original"
