"""LlmAnalyzer implementation on top of the official Anthropic SDK (structured outputs)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

import anthropic

from lexinform.adapters.llm_prompts import PROMPT_VERSION, build_user_prompt, system_prompt
from lexinform.models import Analysis, AnalysisRecord, BillContext
from lexinform.settings import Effort

log = logging.getLogger(__name__)


class LlmError(RuntimeError):
    """Per-bill LLM failure (refusal, validation, transient API error)."""


class LlmFatalError(RuntimeError):
    """Systemic failure that will not go away by moving to the next bill (auth, permissions)."""


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
        output_language: str = "ru",
        effort: Effort = "medium",
        max_tokens: int = 4000,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._client = client
        self._model = model
        self._language = output_language
        self._effort = effort
        self._max_tokens = max_tokens
        self._clock = clock
        self._system = system_prompt(output_language)

    def analyze(self, ctx: BillContext) -> AnalysisRecord:
        user_prompt = build_user_prompt(ctx)
        try:
            response: _ParsedMessageLike = self._client.messages.parse(
                model=self._model,
                max_tokens=self._max_tokens,
                system=[
                    {"type": "text", "text": self._system, "cache_control": {"type": "ephemeral"}}
                ],
                thinking={"type": "adaptive"},
                output_config={"effort": self._effort},
                messages=[{"role": "user", "content": user_prompt}],
                output_format=Analysis,
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise LlmFatalError(str(exc)) from exc
        except anthropic.APIError as exc:
            raise LlmError(f"{type(exc).__name__}: {exc}") from exc

        if response.stop_reason == "refusal":
            raise LlmError("model refused the request")
        if response.stop_reason == "max_tokens":
            raise LlmError("response truncated by max_tokens")
        analysis = response.parsed_output
        if not isinstance(analysis, Analysis):
            raise LlmError("model returned no parsable structured output")

        usage = getattr(response, "usage", None)
        input_tokens = _usage_int(usage, "input_tokens")
        cached = _usage_int(usage, "cache_read_input_tokens") or 0
        log.info(
            "LLM analysed druk %s: relevant=%s score=%d in=%s (cached %d) out=%s",
            ctx.number,
            analysis.relevant,
            analysis.score,
            input_tokens,
            cached,
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
            input_tokens=input_tokens,
            output_tokens=_usage_int(usage, "output_tokens"),
        )


def _usage_int(usage: Any, field: str) -> int | None:
    value = getattr(usage, field, None)
    return int(value) if isinstance(value, int) else None
