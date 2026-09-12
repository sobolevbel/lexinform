"""LlmAnalyzer implementation on top of the official Anthropic SDK (structured outputs)."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

import anthropic

from lexinform.adapters.llm_prompts import (
    PAGE_MAP_SYSTEM_PROMPT,
    PROMPT_VERSION,
    amendments_system_prompt,
    build_amendments_prompt,
    build_page_map_prompt,
    build_supplement_prompt,
    build_triage_prompt,
    build_user_prompt,
    supplement_system_prompt,
    system_prompt,
    triage_system_prompt,
)
from lexinform.errors import LlmUnavailableError
from lexinform.models import (
    Amendments,
    AmendmentsContext,
    AmendmentsRecord,
    Analysis,
    AnalysisRecord,
    BillContext,
    DocumentDigest,
    PageMap,
    PageMapContext,
    PageMapRecord,
    ScannedDocument,
    SupplementContext,
    SupplementRecord,
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
        map_model: str | None = None,
        output_language: str = "ru",
        effort: Effort = "medium",
        max_tokens: int = 4000,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._client = client
        self._model = model
        self._triage_model = triage_model or model
        self._map_model = map_model or self._triage_model
        self._effort = effort
        self._max_tokens = max_tokens
        self._clock = clock
        self._system = system_prompt(output_language)
        self._triage_system = triage_system_prompt(output_language)
        self._amendments_system = amendments_system_prompt(output_language)
        self._supplement_system = supplement_system_prompt(output_language)

    def analyze(self, ctx: BillContext) -> AnalysisRecord:
        response = self._parse(
            self._model,
            self._system,
            build_user_prompt(ctx),
            Analysis,
            thinking=True,
            scan=ctx.scan,
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
            cache_read_input_tokens=_usage_int(usage, "cache_read_input_tokens"),
            cache_creation_input_tokens=_usage_int(usage, "cache_creation_input_tokens"),
        )

    def map_pages(self, ctx: PageMapContext) -> PageMapRecord:
        """What each page of a scan holds, on the cheap model, without thinking: this is sorting
        by headings, not reading."""
        images: list[Any] = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": page},
            }
            for page in ctx.pages
        ]
        images.append({"type": "text", "text": build_page_map_prompt(ctx)})
        response = self._request(
            self._map_model, PAGE_MAP_SYSTEM_PROMPT, images, PageMap, thinking=False
        )
        mapped = response.parsed_output
        if not isinstance(mapped, PageMap):
            raise LlmError("model returned no parsable structured output")
        usage = getattr(response, "usage", None)
        log.info(
            "LLM mapped %d page(s) of %s (%s) in=%s out=%s",
            len(mapped.roles),
            ctx.document_title,
            ctx.number,
            _usage_int(usage, "input_tokens"),
            _usage_int(usage, "output_tokens"),
        )
        return PageMapRecord(
            map=mapped,
            model=self._map_model,
            prompt_version=PROMPT_VERSION,
            input_tokens=_usage_int(usage, "input_tokens"),
            output_tokens=_usage_int(usage, "output_tokens"),
            cache_read_input_tokens=_usage_int(usage, "cache_read_input_tokens"),
            cache_creation_input_tokens=_usage_int(usage, "cache_creation_input_tokens"),
        )

    def count_input_tokens(self, ctx: BillContext) -> int | None:
        """The tokenizer's own count of what `analyze` would send, without sending it.

        The structured-output schema of the request is not counted: it is about a thousand
        tokens, it is cached, and this number exists to decide whether a document is too
        expensive to read at all. A failure is not one — the caller falls back to estimating
        from the text."""
        try:
            counted = self._client.messages.count_tokens(
                model=self._model,
                system=[{"type": "text", "text": self._system}],
                messages=[{"role": "user", "content": _content(build_user_prompt(ctx), ctx.scan)}],
            )
        except Exception as exc:
            log.warning("tokens of druk %s not counted (%s); estimating", ctx.number, _short(exc))
            return None
        return int(counted.input_tokens)

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
            cache_read_input_tokens=_usage_int(usage, "cache_read_input_tokens"),
            cache_creation_input_tokens=_usage_int(usage, "cache_creation_input_tokens"),
        )

    def summarize_amendments(self, ctx: AmendmentsContext) -> AmendmentsRecord:
        """What a set of amendments changes; runs on the analysis model, with thinking."""
        response = self._parse(
            self._model,
            self._amendments_system,
            build_amendments_prompt(ctx),
            Amendments,
            thinking=True,
        )
        amendments = response.parsed_output
        if not isinstance(amendments, Amendments):
            raise LlmError("model returned no parsable structured output")
        usage = getattr(response, "usage", None)
        log.info(
            "LLM summarised amendments of druk %s (%s): %d change(s), affects=%s in=%s out=%s",
            ctx.number,
            ctx.source_kind,
            len(amendments.changes),
            amendments.affects_foreigners,
            _usage_int(usage, "input_tokens"),
            _usage_int(usage, "output_tokens"),
        )
        return AmendmentsRecord(
            amendments=amendments,
            model=self._model,
            prompt_version=PROMPT_VERSION,
            source_url="",  # the caller knows the document
            source_kind=ctx.source_kind,
            created_at=self._clock(),
            input_tokens=_usage_int(usage, "input_tokens"),
            output_tokens=_usage_int(usage, "output_tokens"),
            cache_read_input_tokens=_usage_int(usage, "cache_read_input_tokens"),
            cache_creation_input_tokens=_usage_int(usage, "cache_creation_input_tokens"),
        )

    def digest_supplement(self, ctx: SupplementContext) -> SupplementRecord:
        """What a document filed to the print says about the bill; the analysis model, thinking."""
        response = self._parse(
            self._model,
            self._supplement_system,
            build_supplement_prompt(ctx),
            DocumentDigest,
            thinking=True,
            scan=ctx.scan,
        )
        digest = response.parsed_output
        if not isinstance(digest, DocumentDigest):
            raise LlmError("model returned no parsable structured output")
        usage = getattr(response, "usage", None)
        log.info(
            "LLM digested %s of druk %s: %d point(s), supports=%s affects=%s in=%s out=%s",
            ctx.source_kind,
            ctx.number,
            len(digest.points),
            digest.supports,
            digest.affects_foreigners,
            _usage_int(usage, "input_tokens"),
            _usage_int(usage, "output_tokens"),
        )
        return SupplementRecord(
            number="",  # the caller knows which document it handed over
            title=ctx.document_title,
            source_kind=ctx.source_kind,
            source_url="",
            digest=digest,
            model=self._model,
            prompt_version=PROMPT_VERSION,
            created_at=self._clock(),
            input_tokens=_usage_int(usage, "input_tokens"),
            output_tokens=_usage_int(usage, "output_tokens"),
            cache_read_input_tokens=_usage_int(usage, "cache_read_input_tokens"),
            cache_creation_input_tokens=_usage_int(usage, "cache_creation_input_tokens"),
        )

    def _parse(
        self,
        model: str,
        system: str,
        user_prompt: str,
        output_format: type[Any],
        *,
        thinking: bool,
        scan: ScannedDocument | None = None,
    ) -> _ParsedMessageLike:
        """One structured-output request from a prompt, with the document in front of it when
        the document is a scan: that is the only way to read the signed paper the Sejm files as
        images."""
        return self._request(
            model, system, _content(user_prompt, scan), output_format, thinking=thinking
        )

    def _request(
        self,
        model: str,
        system: str,
        content: list[Any],
        output_format: type[Any],
        *,
        thinking: bool,
    ) -> _ParsedMessageLike:
        """One structured-output request with the error classification shared by every pass.

        The analysis thinks (adaptive thinking, configured effort); the triage and the page map
        are short classifications and run without it, which also keeps smaller models (Haiku 4.5)
        eligible.
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
                messages=[{"role": "user", "content": content}],
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


def _content(user_prompt: str, scan: ScannedDocument | None) -> list[Any]:
    """The user turn: a scanned document goes in front of the prompt, so the model reads its
    pages; a readable one is already inside the prompt as text."""
    blocks: list[Any] = []
    if scan is not None:
        blocks.append(
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": scan.media_type,
                    "data": scan.data,
                },
            }
        )
    blocks.append({"type": "text", "text": user_prompt})
    return blocks


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
