"""The Anthropic adapter: request shape, record fields and error classification, on a stub client."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2 as httpx
import pytest

from lexinform.adapters.llm_anthropic import AnthropicAnalyzer, LlmError, LlmFatalError
from lexinform.adapters.llm_prompts import PROMPT_VERSION, build_user_prompt, system_prompt
from lexinform.models import (
    Amendments,
    AmendmentsContext,
    Analysis,
    ApplicantType,
    BillContext,
    Triage,
    TriageContext,
)
from tests.fakes import make_amendments, make_analysis


@dataclass
class _StubMessages:
    """Stands in for `client.messages`: returns one response or raises one exception."""

    response: Any = None
    error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def _client(response: Any = None, *, error: Exception | None = None) -> Any:
    return SimpleNamespace(messages=_StubMessages(response=response, error=error))


def _response(parsed: Any, *, stop_reason: str = "end_turn", **usage: int) -> Any:
    return SimpleNamespace(
        parsed_output=parsed, stop_reason=stop_reason, usage=SimpleNamespace(**usage)
    )


def _ctx(**overrides: Any) -> BillContext:
    fields: dict[str, Any] = dict(
        number="3039",
        title="Projekt",
        description="opis",
        document_date=None,
        applicant_type=ApplicantType.DEPUTIES,
        text="Art. 1.",
        truncated=False,
        text_source="pdf",
    )
    fields.update(overrides)
    return BillContext(**fields)


def _api_error(cls: type[anthropic.APIStatusError], message: str) -> Exception:
    response = httpx.Response(400, request=httpx.Request("POST", "https://api.test/v1/messages"))
    return cls(message, response=response, body=None)


def test_analysis_request_thinks_caches_the_system_prompt_and_records_usage() -> None:
    client = _client(
        _response(
            make_analysis(), input_tokens=1200, output_tokens=300, cache_read_input_tokens=1000
        )
    )
    analyzer = AnthropicAnalyzer(
        client,
        model="claude-opus-5",
        output_language="ru",
        effort="medium",
        clock=lambda: datetime(2026, 9, 7, tzinfo=UTC),
    )

    record = analyzer.analyze(_ctx())

    call = client.messages.calls[0]
    assert call["model"] == "claude-opus-5" and call["output_format"] is Analysis
    assert call["thinking"] == {"type": "adaptive"} and call["output_config"] == {
        "effort": "medium"
    }
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "Russian" in call["system"][0]["text"]
    assert "Druk nr 3039" in call["messages"][0]["content"]
    assert record.prompt_version == PROMPT_VERSION
    assert (record.input_tokens, record.output_tokens, record.cache_read_input_tokens) == (
        1200,
        300,
        1000,
    )


def test_amendments_request_uses_the_analysis_model_with_thinking() -> None:
    client = _client(_response(make_amendments(), input_tokens=800, output_tokens=120))
    analyzer = AnthropicAnalyzer(
        client,
        model="claude-opus-5",
        triage_model="claude-sonnet-5",
        output_language="ru",
        clock=lambda: datetime(2026, 9, 7, tzinfo=UTC),
    )
    ctx = AmendmentsContext(
        number="3039",
        title="Projekt",
        source_kind="senate_amendments",
        text="Poprawka 1. W art. 5 ...",
        truncated=False,
        previous_summary="Проект меняет правила.",
        previous_key_changes=["Изменение 1"],
    )

    record = analyzer.summarize_amendments(ctx)

    call = client.messages.calls[0]
    assert call["model"] == "claude-opus-5" and call["output_format"] is Amendments
    assert call["thinking"] == {"type": "adaptive"}
    assert "amendments" in call["system"][0]["text"] and "Russian" in call["system"][0]["text"]
    prompt = call["messages"][0]["content"]
    assert "uchwała Senatu z poprawkami" in prompt and "Проект меняет правила." in prompt
    assert "- Изменение 1" in prompt and "Poprawka 1." in prompt
    assert (record.source_kind, record.input_tokens, record.output_tokens) == (
        "senate_amendments",
        800,
        120,
    )
    assert record.amendments.changes and record.prompt_version == PROMPT_VERSION


@pytest.mark.parametrize("reason", ["refusal", "max_tokens"])
def test_refusal_and_truncation_are_per_bill_errors(reason: str) -> None:
    analyzer = AnthropicAnalyzer(_client(_response(None, stop_reason=reason)))

    with pytest.raises(LlmError):
        analyzer.analyze(_ctx())


def test_unparsable_output_is_a_per_bill_error() -> None:
    analyzer = AnthropicAnalyzer(_client(_response({"not": "an analysis"})))

    with pytest.raises(LlmError, match="no parsable"):
        analyzer.analyze(_ctx())


TRIAGE_CTX = TriageContext(
    number="2695",
    title="Projekt ustawy o jakości handlowej",
    description=None,
    applicant_type=ApplicantType.GOVERNMENT,
    excerpts="... Straż Graniczna ...",
    text_chars=200_000,
)


def test_triage_runs_on_its_own_model_without_thinking() -> None:
    verdict = Triage(affects_foreigners=False, confidence=0.95, rationale="о бананах")
    client = _client(_response(verdict, input_tokens=5000, output_tokens=60))
    analyzer = AnthropicAnalyzer(client, model="claude-opus-5", triage_model="claude-sonnet-5")

    record = analyzer.triage(TRIAGE_CTX)

    call = client.messages.calls[0]
    assert call["model"] == "claude-sonnet-5" and call["output_format"] is Triage
    assert "thinking" not in call and "output_config" not in call
    assert "gate" in call["system"][0]["text"]
    assert "Pełny tekst: 200000 znaków" in call["messages"][0]["content"]
    assert (record.model, record.input_tokens) == ("claude-sonnet-5", 5000)
    assert record.rejects(min_confidence=0.8) and not record.rejects(min_confidence=0.99)


def test_triage_falls_back_to_the_analysis_model() -> None:
    verdict = Triage(affects_foreigners=True, confidence=0.5, rationale="x")
    client = _client(_response(verdict))

    AnthropicAnalyzer(client, model="claude-opus-5").triage(TRIAGE_CTX)

    assert client.messages.calls[0]["model"] == "claude-opus-5"


def test_prompts_mention_truncation_and_metadata_only() -> None:
    assert "[TEKST OBCIĘTY" in build_user_prompt(_ctx(truncated=True))
    assert "NIEDOSTĘPNY" in build_user_prompt(_ctx(text="", text_source="metadata_only"))
    assert system_prompt("ru") == system_prompt("RU")
    assert "legalization" in system_prompt("en")


@pytest.mark.parametrize(
    "error",
    [
        TypeError("Could not resolve authentication method"),
        _api_error(anthropic.NotFoundError, "model: x"),  # unknown model id
        _api_error(anthropic.BadRequestError, "thinking: unsupported"),  # bad parameters
    ],
)
def test_configuration_problems_are_fatal(error: Exception) -> None:
    analyzer = AnthropicAnalyzer(_client(error=error))

    with pytest.raises(LlmFatalError):
        analyzer.analyze(_ctx())


def test_oversized_input_is_a_per_bill_error() -> None:
    analyzer = AnthropicAnalyzer(
        _client(error=_api_error(anthropic.BadRequestError, "prompt is too long"))
    )

    with pytest.raises(LlmError):
        analyzer.analyze(_ctx())
