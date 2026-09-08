"""LlmAnalyzer implementation on top of the official Anthropic SDK (structured outputs)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

import anthropic

from lexinform.adapters.llm_prompts import (
    PROMPT_VERSION,
    build_triage_prompt,
    build_user_prompt,
    system_prompt,
    triage_system_prompt,
)
from lexinform.errors import LlmUnavailableError
from lexinform.models import (
    Analysis,
    AnalysisRecord,
    BillContext,
    Triage,
    TriageContext,
    TriageRecord,
)
from lexinform.settings import Effort

log = logging.getLogger(__name__)


class LlmError(RuntimeError):
    """Per-bill LLM failure (refusal, truncated output, bad request for this input)."""


class LlmFatalError(LlmUnavailableError):
    """Systemic failure that will not go away by moving to the next bill: auth, permissions,
    rate limits, connection problems, server errors, missing credentials."""


class _ParsedMessageLike(Protocol):
    """The subset of anthropic.ParsedMessage we use; lets tests stub the client."""

    @property
    def parsed_output(self) -> Any: ...

    @property
    def stop_reason(self) -> Any: ...

    @property
    def usage(self) -> Any: ...


class AnthropicAnalyzer:
    def __init__(
        self,
        client: anthropic.Anthropic,
        *,
        model: str = "claude-opus-5",
        triage_model: str | None = None,
        output_language: str = "ru",
        effort: Effort = "medium",
        max_tokens: int = 4000,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._client = client
        self._model = model
        self._triage_model = triage_model or model
        self._language = output_language
        self._effort = effort
        self._max_tokens = max_tokens
        self._clock = clock
        self._system = system_prompt(output_language)
        self._triage_system = triage_system_prompt(output_language)

    def analyze(self, ctx: BillContext) -> AnalysisRecord:
        response = self._parse(
            self._model, self._system, build_user_prompt(ctx), Analysis, thinking=True
        )
        analysis = response.parsed_output
        if not isinstance(analysis, Analysis):
            raise LlmError("model returned no parsable structured output")
        usage = getattr(response, "usage", None)
        log.info(
            "LLM analysed druk %s: relevant=%s score=%d in=%s (cached %d) out=%s",
            ctx.number,
            analysis.relevant,
            analysis.score,
            _usage_int(usage, "input_tokens"),
            _usage_int(usage, "cache_read_input_tokens") or 0,
            _usage_int(usage, "output_tokens"),
        )
        return AnalysisRecord(
            analysis=analysis,
            model=self._model,
            prompt_version=PROMPT_VERSION,
            input_chars=len(ctx.text),
            truncated=ctx.truncated,
            text_source=ctx.text_source,
            created_at=self._clock(),
            input_tokens=_usage_int(usage, "input_tokens"),
            output_tokens=_usage_int(usage, "output_tokens"),
        )

    def triage(self, ctx: TriageContext) -> TriageRecord:
        """The cheap first pass on excerpts; may run on a smaller model than the analysis."""
        response = self._parse(
            self._triage_model,
            self._triage_system,
            build_triage_prompt(ctx),
            Triage,
            thinking=False,
        )
        triage = response.parsed_output
        if not isinstance(triage, Triage):
            raise LlmError("model returned no parsable structured output")
        usage = getattr(response, "usage", None)
        log.info(
            "LLM triaged druk %s: affects_foreigners=%s confidence=%.2f in=%s out=%s",
            ctx.number,
            triage.affects_foreigners,
            triage.confidence,
            _usage_int(usage, "input_tokens"),
            _usage_int(usage, "output_tokens"),
        )
        return TriageRecord(
            triage=triage,
            model=self._triage_model,
            prompt_version=PROMPT_VERSION,
            input_tokens=_usage_int(usage, "input_tokens"),
            output_tokens=_usage_int(usage, "output_tokens"),
        )

    def _parse(
        self,
        model: str,
        system: str,
        user_prompt: str,
        output_format: type[Any],
        *,
        thinking: bool,
    ) -> _ParsedMessageLike:
        """One structured-output request with the error classification shared by both passes.

        The analysis thinks (adaptive thinking, configured effort); the triage is a short
        classification and runs without it, which also keeps smaller models (Haiku 4.5) eligible.
        """
        reasoning: dict[str, Any] = (
            {"thinking": {"type": "adaptive"}, "output_config": {"effort": self._effort}}
            if thinking
            else {}
        )
        try:
            response: _ParsedMessageLike = self._client.messages.parse(
                model=model,
                max_tokens=self._max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user_prompt}],
                output_format=output_format,
                **reasoning,
            )
        except (
            anthropic.AuthenticationError,
            anthropic.PermissionDeniedError,
            anthropic.NotFoundError,  # unknown model id: would fail for every bill
            anthropic.RateLimitError,
            anthropic.InternalServerError,
            anthropic.APIConnectionError,
        ) as exc:
            raise LlmFatalError(f"{type(exc).__name__}: {_short(exc)}") from exc
        except anthropic.BadRequestError as exc:
            if _is_input_problem(exc):
                raise LlmError(f"BadRequestError: {_short(exc)}") from exc
            # Unsupported parameter, malformed schema, ...: a configuration problem, not this bill.
            raise LlmFatalError(f"BadRequestError: {_short(exc)}") from exc
        except TypeError as exc:
            # The SDK raises TypeError when no credentials can be resolved at request time.
            raise LlmFatalError(f"client misconfigured: {_short(exc)}") from exc
        except anthropic.APIError as exc:
            raise LlmError(f"{type(exc).__name__}: {_short(exc)}") from exc

        if response.stop_reason == "refusal":
            raise LlmError("model refused the request")
        if response.stop_reason == "max_tokens":
            raise LlmError("response truncated by max_tokens")
        return response


def _usage_int(usage: Any, field: str) -> int | None:
    value = getattr(usage, field, None)
    return int(value) if isinstance(value, int) else None


_INPUT_PROBLEM_MARKERS = ("prompt is too long", "too many tokens", "exceeds the context")


def _is_input_problem(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _INPUT_PROBLEM_MARKERS)


def _short(exc: BaseException, limit: int = 300) -> str:
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return text[:limit]
