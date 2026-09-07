from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from lexinform.adapters.llm_anthropic import AnthropicAnalyzer, LlmError, LlmFatalError
from lexinform.adapters.llm_prompts import PROMPT_VERSION, build_user_prompt, system_prompt
from lexinform.models import Analysis, ApplicantType, BillContext
from tests.fakes import make_analysis


@dataclass
class _StubMessages:
    response: Any
    calls: list[dict[str, Any]]

    def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.response


def _client(response: Any) -> Any:
    return SimpleNamespace(messages=_StubMessages(response=response, calls=[]))


def _ctx(**kw: Any) -> BillContext:
    base = dict(
        number="3039",
        title="Projekt",
        description="opis",
        document_date=None,
        applicant_type=ApplicantType.DEPUTIES,
        text="Art. 1.",
        truncated=False,
        text_source="pdf",
    )
    base.update(kw)
    return BillContext(**base)  # type: ignore[arg-type]


def test_analyze_builds_request_and_record() -> None:
    response = SimpleNamespace(
        parsed_output=make_analysis(),
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=1200, output_tokens=300, cache_read_input_tokens=1000),
    )
    client = _client(response)
    analyzer = AnthropicAnalyzer(
        client,
        model="claude-opus-5",
        output_language="ru",
        effort="medium",
        clock=lambda: datetime(2026, 9, 7, tzinfo=UTC),
    )
    record = analyzer.analyze(_ctx())
    call = client.messages.calls[0]
    assert call["model"] == "claude-opus-5"
    assert call["output_format"] is Analysis
    assert call["thinking"] == {"type": "adaptive"}
    assert call["output_config"] == {"effort": "medium"}
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "Russian" in call["system"][0]["text"]
    assert "Druk nr 3039" in call["messages"][0]["content"]
    assert (
        record.prompt_version == PROMPT_VERSION
        and record.input_tokens == 1200
        and record.output_tokens == 300
    )


def test_refusal_and_truncation_raise() -> None:
    for reason in ("refusal", "max_tokens"):
        client = _client(SimpleNamespace(parsed_output=None, stop_reason=reason, usage=None))
        with pytest.raises(LlmError):
            AnthropicAnalyzer(client).analyze(_ctx())


def test_prompts_mention_truncation_and_metadata_only() -> None:
    assert "[TEKST OBCIĘTY" in build_user_prompt(_ctx(truncated=True))
    assert "NIEDOSTĘPNY" in build_user_prompt(_ctx(text="", text_source="metadata_only"))
    assert system_prompt("ru") == system_prompt("RU")
    assert "legalization" in system_prompt("en")


def test_missing_credentials_is_fatal() -> None:
    class _Broken:
        def parse(self, **kwargs: Any) -> Any:
            raise TypeError("Could not resolve authentication method")

    client = SimpleNamespace(messages=_Broken())
    with pytest.raises(LlmFatalError):
        AnthropicAnalyzer(client).analyze(_ctx())
