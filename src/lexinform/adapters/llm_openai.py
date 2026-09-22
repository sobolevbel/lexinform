"""LlmAnalyzer's full analysis on the OpenAI SDK (Responses API, structured outputs).

Only `analyze` and `count_input_tokens`: everything else (triage, amendments, filed-document
digests, joint comparisons) stays on Claude, `llm_hybrid.py` routes between the two, and this
class never sees those calls. Measured against Claude Opus 5 on 26 real prints and one scan
before the switch (docs/llm-cost.md): 96% agreement on relevance, zero missed bills, ~10x
cheaper.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import openai
import tiktoken
from openai.types.responses.response_input_file_param import ResponseInputFileParam
from openai.types.responses.response_input_message_content_list_param import (
    ResponseInputMessageContentListParam,
)
from openai.types.responses.response_input_param import ResponseInputParam
from openai.types.responses.response_input_text_param import ResponseInputTextParam

from lexinform.adapters.llm_prompts import PROMPT_VERSION, build_user_prompt, gpt51_system_prompt
from lexinform.errors import LlmUnavailableError
from lexinform.models import Analysis, AnalysisRecord, BillContext, ScannedDocument
from lexinform.settings import OpenAiEffort

log = logging.getLogger(__name__)


class LlmError(RuntimeError):
    """Per-bill LLM failure (refusal, truncated output, bad request for this input)."""


class LlmFatalError(LlmUnavailableError):
    """Systemic failure that will not go away by moving to the next bill: auth, permissions,
    rate limits, connection problems, server errors, missing credentials."""


# Mirrors `models.Analysis` field for field; strict Structured Outputs takes no `$ref` to a
# pydantic-generated schema without the same by-hand adjustments (every property required,
# `additionalProperties: false` at every level), so this is kept by hand like the prompt's own
# "## Output fields" section already is (see its docstring) rather than through a converter that
# would still need the same care to get right and would hide it in a second place.
_ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "relevant": {"type": "boolean"},
        "score": {"type": "integer"},
        "category": {
            "type": "string",
            "enum": ["legal_stay", "employment", "social", "indirect", "marginal", "none"],
        },
        "summary": {"type": "string"},
        "key_changes": {"type": "array", "items": {"type": "string"}},
        "affected_groups": {"type": "array", "items": {"type": "string"}},
        "practical_impact": {"type": "string"},
        "effective_date": {"type": ["string", "null"]},
        "confidence": {"type": "number"},
        "rationale": {"type": "string"},
        "changes_since_previous": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "relevant",
        "score",
        "category",
        "summary",
        "key_changes",
        "affected_groups",
        "practical_impact",
        "effective_date",
        "confidence",
        "rationale",
        "changes_since_previous",
    ],
    "additionalProperties": False,
}


class OpenAiAnalyzer:
    def __init__(
        self,
        client: Callable[[], openai.OpenAI],
        *,
        model: str = "gpt-5.1",
        output_language: str = "ru",
        effort: OpenAiEffort = "medium",
        max_output_tokens: int = 8000,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """`client` is a factory, not a built client: the openai SDK validates credentials at
        construction (unlike anthropic's, which only fails at the first real call), so building
        it eagerly would demand an API key from every command that merely wires the container —
        `/forget`, `/status`, a dry run over a quiet Sejm — never mind that this one never calls
        the model. Built once, on first actual use."""
        self._client_factory = client
        self._client: openai.OpenAI | None = None
        self._model = model
        self._effort = effort
        self._max_output_tokens = max_output_tokens
        self._clock = clock
        self._system = gpt51_system_prompt(output_language)

    @property
    def _resolved_client(self) -> openai.OpenAI:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    def analyze(self, ctx: BillContext) -> AnalysisRecord:
        response = self._call(ctx)
        analysis = _parsed_analysis(response)
        usage = response.usage
        input_tokens = getattr(usage, "input_tokens", None)
        output_tokens = getattr(usage, "output_tokens", None)
        cached = getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", None)
        log.info(
            "LLM analysed druk %s: relevant=%s score=%d in=%s (cached %s) out=%s",
            ctx.number,
            analysis.relevant,
            analysis.score,
            input_tokens,
            cached or 0,
            output_tokens,
        )
        return AnalysisRecord(
            analysis=analysis,
            model=self._model,
            prompt_version=PROMPT_VERSION,
            input_chars=len(ctx.text),
            truncated=ctx.truncated,
            text_source=ctx.text_source,
            created_at=self._clock(),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cached,
            cache_creation_input_tokens=0,
        )

    def count_input_tokens(self, ctx: BillContext) -> int | None:
        """A local encoding, not a call: OpenAI has no free-standing token-counting endpoint, and
        asking the model to spend a request just to learn a number defeats the guard it feeds.
        Approximate for a scan (page-image tokens are not text tokens at all — the per-bill guard
        prices those from the page count instead, see `pricing.estimate_scan_cost`)."""
        try:
            encoding = tiktoken.encoding_for_model(self._model)
        except Exception as exc:
            log.warning("no tiktoken encoding for %s (%s); estimating", self._model, _short(exc))
            return None
        if ctx.scan is not None:
            return None
        return len(encoding.encode(self._system)) + len(encoding.encode(build_user_prompt(ctx)))

    def _call(self, ctx: BillContext) -> Any:
        input_messages: ResponseInputParam = [
            {"role": "system", "content": self._system},
            {"role": "user", "content": _content(build_user_prompt(ctx), ctx.scan)},
        ]
        try:
            response = self._resolved_client.responses.create(
                model=self._model,
                reasoning=None if self._effort == "none" else {"effort": self._effort},
                max_output_tokens=self._max_output_tokens,
                input=input_messages,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "analysis",
                        "schema": _ANALYSIS_SCHEMA,
                        "strict": True,
                    }
                },
            )
        except (
            openai.AuthenticationError,
            openai.PermissionDeniedError,
            openai.NotFoundError,  # unknown model id: would fail for every bill
            openai.RateLimitError,
            openai.InternalServerError,
            openai.APIConnectionError,
        ) as exc:
            raise LlmFatalError(f"{type(exc).__name__}: {_short(exc)}") from exc
        except openai.BadRequestError as exc:
            if _is_input_problem(exc):
                raise LlmError(f"BadRequestError: {_short(exc)}") from exc
            raise LlmFatalError(f"BadRequestError: {_short(exc)}") from exc
        except openai.APIError as exc:
            raise LlmError(f"{type(exc).__name__}: {_short(exc)}") from exc

        if response.status == "incomplete":
            reason = getattr(response.incomplete_details, "reason", None)
            raise LlmError(f"response incomplete: {reason}")
        return response


def _content(
    user_prompt: str, scan: ScannedDocument | None
) -> ResponseInputMessageContentListParam:
    """The user turn: a scanned document goes in front of the prompt, so the model reads its
    pages; a readable one is already inside the prompt as text."""
    blocks: ResponseInputMessageContentListParam = []
    if scan is not None:
        blocks.append(
            ResponseInputFileParam(
                type="input_file",
                filename="document.pdf",
                file_data=f"data:{scan.media_type};base64,{scan.data}",
            )
        )
    blocks.append(ResponseInputTextParam(type="input_text", text=user_prompt))
    return blocks


def _parsed_analysis(response: Any) -> Analysis:
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", None) == "refusal":
            raise LlmError("model refused the request")
        if getattr(item, "type", None) == "message":
            for block in item.content:
                if getattr(block, "type", None) == "output_text":
                    return Analysis.model_validate_json(block.text)
                if getattr(block, "type", None) == "refusal":
                    raise LlmError("model refused the request")
    raise LlmError("model returned no parsable structured output")


_INPUT_PROBLEM_MARKERS = ("too long", "too many tokens", "exceeds the context", "context_length")


def _is_input_problem(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _INPUT_PROBLEM_MARKERS)


def _short(exc: BaseException, limit: int = 300) -> str:
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return text[:limit]
