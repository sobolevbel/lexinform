"""The OpenAI adapter: request shape, record fields and error classification, on a stub client."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx2 as httpx
import openai
import pytest

from lexinform.adapters.llm_openai import LlmError, LlmFatalError, OpenAiAnalyzer
from lexinform.adapters.llm_prompts import PROMPT_VERSION, gpt51_system_prompt
from lexinform.models import (
    AmendmentsContext,
    ApplicantType,
    BillContext,
    JointBillDescription,
    JointContext,
    ScannedDocument,
    SupplementContext,
)
from tests.fakes import make_amendments, make_analysis, make_comparison, make_digest


@dataclass
class _StubResponses:
    response: Any = None
    error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def _client(response: Any = None, *, error: Exception | None = None) -> Any:
    return SimpleNamespace(responses=_StubResponses(response=response, error=error))


def _message(text: str) -> Any:
    return SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text", text=text)])


def _response(
    text: str, *, status: str = "completed", incomplete_reason: str | None = None, **usage: int
) -> Any:
    return SimpleNamespace(
        status=status,
        output=[_message(text)],
        incomplete_details=SimpleNamespace(reason=incomplete_reason) if incomplete_reason else None,
        usage=SimpleNamespace(
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            input_tokens_details=SimpleNamespace(cached_tokens=usage.get("cached_tokens")),
        ),
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


def _analyzer(client: Any, **kwargs: Any) -> OpenAiAnalyzer:
    return OpenAiAnalyzer(lambda: client, **kwargs)


def _prompt_of(call: dict[str, Any]) -> str:
    blocks = call["input"][1]["content"]
    return str(next(block["text"] for block in blocks if block["type"] == "input_text"))


def _files_of(call: dict[str, Any]) -> list[dict[str, Any]]:
    return [b for b in call["input"][1]["content"] if b["type"] == "input_file"]


def _api_error(cls: type[openai.APIStatusError], message: str) -> Exception:
    request = httpx.Request("POST", "https://api.test/v1/responses")
    response = httpx.Response(400, request=request)
    return cls(message, response=response, body=None)


def test_analysis_request_uses_the_gpt51_prompt_and_records_usage() -> None:
    client = _client(
        _response(make_analysis().model_dump_json(), input_tokens=1200, output_tokens=300)
    )
    analyzer = _analyzer(
        client,
        model="gpt-5.1",
        output_language="ru",
        effort="medium",
        clock=lambda: datetime(2026, 9, 22, tzinfo=UTC),
    )

    record = analyzer.analyze(_ctx())

    call = client.responses.calls[0]
    assert call["model"] == "gpt-5.1"
    assert call["reasoning"] == {"effort": "medium"}
    assert call["text"]["format"]["strict"] is True
    assert call["input"][0] == {"role": "system", "content": gpt51_system_prompt("ru")}
    assert "Druk nr 3039" in _prompt_of(call) and _files_of(call) == []
    assert record.model == "gpt-5.1" and record.prompt_version == PROMPT_VERSION
    assert (record.input_tokens, record.output_tokens, record.cache_creation_input_tokens) == (
        1200,
        300,
        0,
    )


def test_effort_none_omits_the_reasoning_param() -> None:
    client = _client(_response(make_analysis().model_dump_json()))
    _analyzer(client, effort="none").analyze(_ctx())

    assert client.responses.calls[0]["reasoning"] is None


def test_a_scanned_print_is_sent_as_an_input_file_before_the_prompt() -> None:
    client = _client(_response(make_analysis().model_dump_json(), input_tokens=16_000))
    scan = ScannedDocument(data="JVBERi0=", pages=9, of_pages=10, sha256="abc")

    _analyzer(client).analyze(_ctx(text="", text_source="scan", scan=scan))

    files = _files_of(client.responses.calls[0])
    assert files == [
        {
            "type": "input_file",
            "filename": "document.pdf",
            "file_data": "data:application/pdf;base64,JVBERi0=",
        }
    ]


def test_a_refusal_output_item_is_a_per_bill_error() -> None:
    response = SimpleNamespace(
        status="completed",
        output=[SimpleNamespace(type="refusal", content=[])],
        incomplete_details=None,
    )
    with pytest.raises(LlmError, match="refused"):
        _analyzer(_client(response)).analyze(_ctx())


def test_an_incomplete_response_is_a_per_bill_error() -> None:
    response = _response(
        make_analysis().model_dump_json(),
        status="incomplete",
        incomplete_reason="max_output_tokens",
    )
    with pytest.raises(LlmError, match="max_output_tokens"):
        _analyzer(_client(response)).analyze(_ctx())


def test_unparsable_output_is_a_per_bill_error() -> None:
    response = SimpleNamespace(status="completed", output=[], incomplete_details=None)
    with pytest.raises(LlmError, match="no parsable"):
        _analyzer(_client(response)).analyze(_ctx())


@pytest.mark.parametrize(
    "error",
    [
        _api_error(openai.NotFoundError, "model: x"),  # unknown model id
        _api_error(openai.AuthenticationError, "invalid api key"),
    ],
)
def test_configuration_problems_are_fatal(error: Exception) -> None:
    with pytest.raises(LlmFatalError):
        _analyzer(_client(error=error)).analyze(_ctx())


def test_oversized_input_is_a_per_bill_error() -> None:
    error = _api_error(openai.BadRequestError, "input is too long for this model")
    with pytest.raises(LlmError):
        _analyzer(_client(error=error)).analyze(_ctx())


def test_the_client_is_built_lazily_and_only_once() -> None:
    """The openai SDK validates credentials at construction; building it for every command that
    merely wires the container — never mind one that never calls the model — would demand a key
    nothing here needs."""
    built = []

    def factory() -> Any:
        built.append(1)
        return _client(_response(make_analysis().model_dump_json()))

    analyzer = OpenAiAnalyzer(factory)
    assert built == []

    analyzer.analyze(_ctx())
    analyzer.analyze(_ctx())
    assert built == [1]


def test_count_input_tokens_is_a_local_estimate_not_a_call() -> None:
    client = _client()
    analyzer = _analyzer(client)

    tokens = analyzer.count_input_tokens(_ctx())

    assert tokens is not None and tokens > 0
    assert client.responses.calls == []


def test_count_input_tokens_is_none_for_a_scan() -> None:
    analyzer = _analyzer(_client())
    scan = ScannedDocument(data="JVBERi0=", pages=1, of_pages=1, sha256="abc")

    assert analyzer.count_input_tokens(_ctx(text="", text_source="scan", scan=scan)) is None


def test_amendments_request_uses_the_amendments_prompt() -> None:
    client = _client(_response(make_amendments().model_dump_json(), input_tokens=800))
    ctx = AmendmentsContext(
        number="3039",
        title="Projekt",
        source_kind="senate_amendments",
        text="Poprawka 1. W art. 5 ...",
        truncated=False,
        previous_summary="Проект меняет правила.",
        previous_key_changes=["Изменение 1"],
    )

    record = _analyzer(client, output_language="ru").summarize_amendments(ctx)

    call = client.responses.calls[0]
    assert call["text"]["format"]["name"] == "amendments"
    assert "amendments" in call["input"][0]["content"]
    prompt = _prompt_of(call)
    assert "uchwała Senatu z poprawkami" in prompt and "Проект меняет правила." in prompt
    assert record.source_kind == "senate_amendments" and record.input_tokens == 800


def test_supplement_request_carries_a_scan_when_the_filing_is_one() -> None:
    client = _client(_response(make_digest().model_dump_json()))
    scan = ScannedDocument(data="JVBERi0=", pages=2, of_pages=2, sha256="abc")
    ctx = SupplementContext(
        number="3039",
        title="Projekt",
        document_title="Stanowisko Rządu",
        source_kind="government_position",
        text="",
        truncated=False,
        scan=scan,
        previous_summary="Опис.",
    )

    record = _analyzer(client).digest_supplement(ctx)

    assert _files_of(client.responses.calls[0]) != []
    assert record.title == "Stanowisko Rządu"


def test_joint_comparison_request_compares_the_descriptions_not_the_texts() -> None:
    client = _client(_response(make_comparison().model_dump_json()))
    ctx = JointContext(
        subject=JointBillDescription(
            number="1933", title="A", applicant_type=ApplicantType.DEPUTIES, summary="Опис A."
        ),
        others=[
            JointBillDescription(
                number="1929",
                title="B",
                applicant_type=ApplicantType.GOVERNMENT,
                summary="Опис B.",
            )
        ],
    )

    record = _analyzer(client).compare_joint(ctx)

    prompt = _prompt_of(client.responses.calls[0])
    assert "druk nr 1933" in prompt.lower() and "druk nr 1929" in prompt.lower()
    assert record.compared_with == ["1929"]
