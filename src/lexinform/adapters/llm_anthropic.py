"""LlmAnalyzer implementation on top of the official Anthropic SDK (structured outputs)."""

import logging
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

import anthropic
from anthropic import transform_schema
from anthropic.lib._parse._response import parse_response
from anthropic.types.json_output_format_param import JSONOutputFormatParam
from anthropic.types.messages.batch_create_params import Request as AnthropicBatchRequest
from pydantic import TypeAdapter

from lexinform.adapters.llm_prompts import (
    PROMPT_VERSION,
    amendments_system_prompt,
    build_amendments_prompt,
    build_joint_prompt,
    build_supplement_prompt,
    build_triage_prompt,
    build_user_prompt,
    joint_system_prompt,
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
    BatchRequest,
    BatchResult,
    BatchStatus,
    BillContext,
    DocumentDigest,
    JointComparison,
    JointContext,
    JointRecord,
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
        output_language: str = "ru",
        effort: Effort = "medium",
        max_tokens: int = 4000,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._client = client
        self._model = model
        self._triage_model = triage_model or model
        self._effort = effort
        self._max_tokens = max_tokens
        self._clock = clock
        self._system = system_prompt(output_language)
        self._triage_system = triage_system_prompt(output_language)
        self._amendments_system = amendments_system_prompt(output_language)
        self._supplement_system = supplement_system_prompt(output_language)
        self._joint_system = joint_system_prompt(output_language)
        self._analysis_format = _json_output_format(Analysis)

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
            scan=ctx.scan,
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

    def compare_joint(self, ctx: JointContext) -> JointRecord:
        """How one print of a jointly considered group differs from the others.

        The analysis model with thinking, on a prompt of a few descriptions: the input is a
        thousandth of a bill's text, and the question — telling two bills on one subject apart —
        is the kind a cheap model answers by inventing a difference.
        """
        response = self._parse(
            self._model,
            self._joint_system,
            build_joint_prompt(ctx),
            JointComparison,
            thinking=True,
        )
        comparison = response.parsed_output
        if not isinstance(comparison, JointComparison):
            raise LlmError("model returned no parsable structured output")
        usage = getattr(response, "usage", None)
        log.info(
            "LLM compared druk %s with %s: same_substance=%s, %d difference(s) in=%s out=%s",
            ctx.subject.number,
            ", ".join(other.number for other in ctx.others),
            comparison.same_substance,
            len(comparison.differences),
            _usage_int(usage, "input_tokens"),
            _usage_int(usage, "output_tokens"),
        )
        return JointRecord(
            comparison=comparison,
            compared_with=[other.number for other in ctx.others],
            model=self._model,
            prompt_version=PROMPT_VERSION,
            created_at=self._clock(),
            input_tokens=_usage_int(usage, "input_tokens"),
            output_tokens=_usage_int(usage, "output_tokens"),
            cache_read_input_tokens=_usage_int(usage, "cache_read_input_tokens"),
            cache_creation_input_tokens=_usage_int(usage, "cache_creation_input_tokens"),
        )

    def submit(self, requests: Sequence[BatchRequest]) -> str:
        """File one Messages Batches API submission — half of `analyze`'s price, answered within
        a run or two rather than at once. `output_config` is built the way `messages.parse`
        builds it internally: the Batches API takes raw request params, with no `.parse()`
        convenience of its own, but the structured-output feature underneath is the same one."""
        batch_requests: list[AnthropicBatchRequest] = [
            {
                "custom_id": req.custom_id,
                "params": {
                    "model": self._model,
                    "max_tokens": self._max_tokens,
                    "system": [
                        {
                            "type": "text",
                            "text": self._system,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    "messages": [
                        {
                            "role": "user",
                            "content": _content(build_user_prompt(req.ctx), req.ctx.scan),
                        }
                    ],
                    "thinking": {"type": "adaptive"},
                    "output_config": {"effort": self._effort, "format": self._analysis_format},
                },
            }
            for req in requests
        ]
        try:
            batch = self._client.messages.batches.create(requests=batch_requests)
        except (
            anthropic.AuthenticationError,
            anthropic.PermissionDeniedError,
            anthropic.NotFoundError,
            anthropic.RateLimitError,
            anthropic.InternalServerError,
            anthropic.APIConnectionError,
        ) as exc:
            raise LlmFatalError(f"{type(exc).__name__}: {_short(exc)}") from exc
        except anthropic.APIError as exc:
            raise LlmFatalError(f"{type(exc).__name__}: {_short(exc)}") from exc
        log.info("submitted batch %s of %d request(s)", batch.id, len(batch_requests))
        return batch.id

    def poll(self, batch_id: str) -> BatchStatus:
        try:
            batch = self._client.messages.batches.retrieve(batch_id)
        except (
            anthropic.AuthenticationError,
            anthropic.PermissionDeniedError,
            anthropic.RateLimitError,
            anthropic.InternalServerError,
            anthropic.APIConnectionError,
        ) as exc:
            raise LlmFatalError(f"{type(exc).__name__}: {_short(exc)}") from exc
        except anthropic.NotFoundError:
            return "failed"
        return "ended" if batch.processing_status == "ended" else "submitted"

    def fetch_results(self, batch_id: str) -> Iterator[BatchResult]:
        try:
            for item in self._client.messages.batches.results(batch_id):
                yield self._batch_result(item)
        except (
            anthropic.AuthenticationError,
            anthropic.PermissionDeniedError,
            anthropic.RateLimitError,
            anthropic.InternalServerError,
            anthropic.APIConnectionError,
        ) as exc:
            raise LlmFatalError(f"{type(exc).__name__}: {_short(exc)}") from exc

    def _batch_result(self, item: Any) -> BatchResult:
        result = item.result
        if result.type != "succeeded":
            return BatchResult(custom_id=item.custom_id, error=f"batch item {result.type}")
        message = result.message
        # Any: `parse_response` binds its generic to the class object, not an Analysis instance.
        parsed: Any = parse_response(output_format=Analysis, response=message)
        analysis = parsed.parsed_output
        if not isinstance(analysis, Analysis):
            return BatchResult(
                custom_id=item.custom_id, error="model returned no parsable structured output"
            )
        usage = message.usage
        return BatchResult(
            custom_id=item.custom_id,
            analysis=analysis,
            model=self._model,
            prompt_version=PROMPT_VERSION,
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
        """One structured-output request with the error classification shared by both passes.

        The analysis thinks (adaptive thinking, configured effort); the triage is a short
        classification and runs without it, which also keeps smaller models (Haiku 4.5) eligible.
        A `scan` is the document itself, attached before the prompt so the model reads its pages:
        that is the only way to read the signed paper the Sejm files as images.
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
                messages=[{"role": "user", "content": _content(user_prompt, scan)}],
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


def _json_output_format(output_format: type[Any]) -> JSONOutputFormatParam:
    """The raw `output_config.format` `messages.parse` builds from `output_format=`, for the
    Batches API's plain request params, which take no `output_format=` shortcut of their own."""
    schema = TypeAdapter(output_format).json_schema()
    return JSONOutputFormatParam(type="json_schema", schema=transform_schema(schema))


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
