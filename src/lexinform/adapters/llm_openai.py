"""LlmAnalyzer's per-bill work on the OpenAI SDK (Responses API, structured outputs).

Everything but `triage`: the full analysis, amendments, filed-document digests and joint
comparisons — `count_input_tokens` too, since it exists only to guard `analyze`'s cost. Which of
these actually run here, as against Claude, is `llm_hybrid.py`'s decision, made per call kind
(`llm_*_model` in settings): this class only has to answer honestly for whichever it is asked.
The full analysis was measured against Claude Opus 5 on 26 real prints and one scan before the
switch (docs/llm-cost.md): 96% agreement on relevance, zero missed bills, ~10x cheaper.
"""

import json
import logging
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime
from typing import Any, TypeVar

import openai
import tiktoken
from openai.types.responses.response import Response
from openai.types.responses.response_input_file_param import ResponseInputFileParam
from openai.types.responses.response_input_message_content_list_param import (
    ResponseInputMessageContentListParam,
)
from openai.types.responses.response_input_param import ResponseInputParam
from openai.types.responses.response_input_text_param import ResponseInputTextParam
from pydantic import BaseModel, ValidationError

from lexinform.adapters.llm_prompts import (
    PROMPT_VERSION,
    build_amendments_prompt,
    build_joint_prompt,
    build_supplement_prompt,
    build_user_prompt,
    gpt51_amendments_system_prompt,
    gpt51_joint_system_prompt,
    gpt51_supplement_system_prompt,
    gpt51_system_prompt,
)
from lexinform.errors import BatchNotSubmittedError, LlmUnavailableError
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
)
from lexinform.pricing import TOKENS_PER_SCANNED_PAGE, batch_reservation
from lexinform.settings import OpenAiEffort

log = logging.getLogger(__name__)

_T = TypeVar("_T", bound=BaseModel)


class LlmError(RuntimeError):
    """Per-bill LLM failure (refusal, truncated output, bad request for this input)."""


class LlmFatalError(LlmUnavailableError):
    """Systemic failure that will not go away by moving to the next bill: auth, permissions,
    rate limits, connection problems, server errors, missing credentials."""


# Every schema below mirrors its `models.py` counterpart field for field; strict Structured
# Outputs takes no `$ref` to a pydantic-generated schema without the same by-hand adjustments
# (every property required, `additionalProperties: false` at every level), so these are kept by
# hand like the prompts' own "## Output fields" sections already are, rather than through a
# converter that would still need the same care to get right and would hide it in a second place.
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

_AMENDMENTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "changes": {"type": "array", "items": {"type": "string"}},
        "affects_foreigners": {"type": "boolean"},
        "confidence": {"type": "number"},
    },
    "required": ["summary", "changes", "affects_foreigners", "confidence"],
    "additionalProperties": False,
}

_DOCUMENT_DIGEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "points": {"type": "array", "items": {"type": "string"}},
        "supports": {"type": ["boolean", "null"]},
        "affects_foreigners": {"type": "boolean"},
        "confidence": {"type": "number"},
    },
    "required": ["summary", "points", "supports", "affects_foreigners", "confidence"],
    "additionalProperties": False,
}

_JOINT_COMPARISON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "same_substance": {"type": "boolean"},
        "summary": {"type": "string"},
        "differences": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
    },
    "required": ["same_substance", "summary", "differences", "confidence"],
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
        self._language = output_language
        self._system = gpt51_system_prompt(output_language)

    @property
    def _resolved_client(self) -> openai.OpenAI:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    def analyze(self, ctx: BillContext) -> AnalysisRecord:
        analysis, usage = self._structured_call(
            system=self._system,
            user_prompt=build_user_prompt(ctx),
            scan=ctx.scan,
            schema_name="analysis",
            schema=_ANALYSIS_SCHEMA,
            output_model=Analysis,
        )
        log.info(
            "LLM analysed druk %s: relevant=%s score=%d in=%s (cached %s) out=%s",
            ctx.number,
            analysis.relevant,
            analysis.score,
            usage.input_tokens,
            usage.cached_tokens or 0,
            usage.output_tokens,
        )
        return AnalysisRecord(
            analysis=analysis,
            model=self._model,
            prompt_version=PROMPT_VERSION,
            input_chars=len(ctx.text),
            truncated=ctx.truncated,
            text_source=ctx.text_source,
            created_at=self._clock(),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_input_tokens=usage.cached_tokens,
            cache_creation_input_tokens=0,
        )

    def summarize_amendments(self, ctx: AmendmentsContext) -> AmendmentsRecord:
        amendments, usage = self._structured_call(
            system=gpt51_amendments_system_prompt(self._language),
            user_prompt=build_amendments_prompt(ctx),
            scan=None,
            schema_name="amendments",
            schema=_AMENDMENTS_SCHEMA,
            output_model=Amendments,
        )
        log.info(
            "LLM summarised amendments of druk %s (%s): %d change(s), affects=%s in=%s out=%s",
            ctx.number,
            ctx.source_kind,
            len(amendments.changes),
            amendments.affects_foreigners,
            usage.input_tokens,
            usage.output_tokens,
        )
        return AmendmentsRecord(
            amendments=amendments,
            model=self._model,
            prompt_version=PROMPT_VERSION,
            source_url="",  # the caller knows the document
            source_kind=ctx.source_kind,
            created_at=self._clock(),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_input_tokens=usage.cached_tokens,
            cache_creation_input_tokens=0,
        )

    def digest_supplement(self, ctx: SupplementContext) -> SupplementRecord:
        digest, usage = self._structured_call(
            system=gpt51_supplement_system_prompt(self._language),
            user_prompt=build_supplement_prompt(ctx),
            scan=ctx.scan,
            schema_name="document_digest",
            schema=_DOCUMENT_DIGEST_SCHEMA,
            output_model=DocumentDigest,
        )
        log.info(
            "LLM digested %s of druk %s: %d point(s), supports=%s affects=%s in=%s out=%s",
            ctx.source_kind,
            ctx.number,
            len(digest.points),
            digest.supports,
            digest.affects_foreigners,
            usage.input_tokens,
            usage.output_tokens,
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
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_input_tokens=usage.cached_tokens,
            cache_creation_input_tokens=0,
        )

    def compare_joint(self, ctx: JointContext) -> JointRecord:
        comparison, usage = self._structured_call(
            system=gpt51_joint_system_prompt(self._language),
            user_prompt=build_joint_prompt(ctx),
            scan=None,
            schema_name="joint_comparison",
            schema=_JOINT_COMPARISON_SCHEMA,
            output_model=JointComparison,
        )
        log.info(
            "LLM compared druk %s with %s: same_substance=%s, %d difference(s) in=%s out=%s",
            ctx.subject.number,
            ", ".join(other.number for other in ctx.others),
            comparison.same_substance,
            len(comparison.differences),
            usage.input_tokens,
            usage.output_tokens,
        )
        return JointRecord(
            comparison=comparison,
            compared_with=[other.number for other in ctx.others],
            model=self._model,
            prompt_version=PROMPT_VERSION,
            created_at=self._clock(),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_input_tokens=usage.cached_tokens,
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

    def prepare_request(self, request: BatchRequest) -> BatchRequest:
        if request.payload_json is not None:
            return request
        req = request
        payload = {
            "custom_id": req.custom_id,
            "method": "POST",
            "url": "/v1/responses",
            "body": {
                "model": self._model,
                "reasoning": None if self._effort == "none" else {"effort": self._effort},
                "max_output_tokens": self._max_output_tokens,
                "input": [
                    {"role": "system", "content": self._system},
                    {
                        "role": "user",
                        "content": _content(build_user_prompt(req.ctx), req.ctx.scan),
                    },
                ],
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "analysis",
                        "schema": _ANALYSIS_SCHEMA,
                        "strict": True,
                    }
                },
            },
        }
        tokens = self.count_input_tokens(req.ctx)
        estimate = max(tokens or 0, len(req.ctx.text) + len(req.ctx.title) + 2_000)
        if req.ctx.scan is not None:
            estimate += req.ctx.scan.pages * TOKENS_PER_SCANNED_PAGE
        return req.model_copy(
            update={
                "payload_json": json.dumps(payload, ensure_ascii=False),
                "model": self._model,
                "prompt_version": PROMPT_VERSION,
                "estimated_cost_usd": batch_reservation(
                    self._model, input_tokens=estimate, max_output_tokens=self._max_output_tokens
                ),
            }
        )

    def submit(self, requests: Sequence[BatchRequest]) -> str:
        """File one Batches API submission against `/v1/responses` — half of the synchronous
        price, answered within 24h at the most. One JSONL line per request, uploaded as a file
        first: the Batches API takes a file id, never the requests inline."""
        lines = [self.prepare_request(req).payload_json or "{}" for req in requests]
        payload = ("\n".join(lines) + "\n").encode("utf-8")
        try:
            uploaded = self._resolved_client.files.create(
                file=("batch.jsonl", payload, "application/jsonl"), purpose="batch"
            )
            batch = self._resolved_client.batches.create(
                input_file_id=uploaded.id, endpoint="/v1/responses", completion_window="24h"
            )
        except (
            openai.AuthenticationError,
            openai.PermissionDeniedError,
            openai.NotFoundError,
            openai.RateLimitError,
        ) as exc:
            raise BatchNotSubmittedError(f"{type(exc).__name__}: {_short(exc)}") from exc
        except (
            openai.InternalServerError,
            openai.APIConnectionError,
        ) as exc:
            raise LlmFatalError(f"{type(exc).__name__}: {_short(exc)}") from exc
        except openai.APIError as exc:
            raise LlmFatalError(f"{type(exc).__name__}: {_short(exc)}") from exc
        log.info("submitted batch %s of %d request(s)", batch.id, len(lines))
        return batch.id

    def poll(self, batch_id: str) -> BatchStatus:
        try:
            batch = self._resolved_client.batches.retrieve(batch_id)
        except (
            openai.NotFoundError,
            openai.AuthenticationError,
            openai.PermissionDeniedError,
            openai.RateLimitError,
            openai.InternalServerError,
            openai.APIConnectionError,
        ) as exc:
            raise LlmFatalError(f"{type(exc).__name__}: {_short(exc)}") from exc
        if batch.status in ("completed", "expired", "cancelled"):
            return "ended"
        if batch.status == "failed":
            return "failed"
        return "submitted"

    def fetch_results(self, batch_id: str) -> Iterator[BatchResult]:
        try:
            batch = self._resolved_client.batches.retrieve(batch_id)
            files = [
                self._resolved_client.files.content(file_id)
                for file_id in (batch.output_file_id, batch.error_file_id)
                if file_id is not None
            ]
        except (
            openai.NotFoundError,
            openai.AuthenticationError,
            openai.PermissionDeniedError,
            openai.RateLimitError,
            openai.InternalServerError,
            openai.APIConnectionError,
        ) as exc:
            raise LlmFatalError(f"{type(exc).__name__}: {_short(exc)}") from exc
        for content in files:
            for line in content.text.splitlines():
                if line.strip():
                    try:
                        decoded = json.loads(line)
                    except json.JSONDecodeError:
                        log.warning("batch %s contains an invalid JSONL line", batch_id)
                        continue
                    if not isinstance(decoded, dict) or not isinstance(
                        decoded.get("custom_id"), str
                    ):
                        log.warning("batch %s contains a result without custom_id", batch_id)
                        continue
                    yield self._batch_result_line(decoded)

    def _batch_result_line(self, line: dict[str, Any]) -> BatchResult:
        custom_id = str(line["custom_id"])
        error = line.get("error")
        if error is not None:
            return BatchResult(custom_id=custom_id, error=str(error))
        response_data = line.get("response")
        body = response_data.get("body") if isinstance(response_data, dict) else None
        if body is None:
            return BatchResult(custom_id=custom_id, error="batch item carries no response")
        try:
            response = Response.model_validate(body)
        except ValidationError as exc:
            return BatchResult(custom_id=custom_id, error=str(exc))
        usage = _usage_of(response)
        answer = BatchResult(
            custom_id=custom_id,
            model=response.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_input_tokens=usage.cached_tokens,
            cache_creation_input_tokens=0,
        )
        try:
            if response.status != "completed":
                raise LlmError(f"response is {response.status}")
            analysis = _parsed_output(response, Analysis)
        except (LlmError, ValidationError) as exc:
            return answer.model_copy(update={"error": str(exc)})
        return answer.model_copy(update={"analysis": analysis})

    def _structured_call(
        self,
        *,
        system: str,
        user_prompt: str,
        scan: ScannedDocument | None,
        schema_name: str,
        schema: dict[str, Any],
        output_model: type[_T],
    ) -> tuple[_T, _Usage]:
        input_messages: ResponseInputParam = [
            {"role": "system", "content": system},
            {"role": "user", "content": _content(user_prompt, scan)},
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
                        "name": schema_name,
                        "schema": schema,
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
        return _parsed_output(response, output_model), _usage_of(response)


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


class _Usage(BaseModel):
    input_tokens: int | None
    output_tokens: int | None
    cached_tokens: int | None


def _usage_of(response: Any) -> _Usage:
    usage = response.usage
    details = getattr(usage, "input_tokens_details", None)
    return _Usage(
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
        cached_tokens=getattr(details, "cached_tokens", None),
    )


def _parsed_output[T: BaseModel](response: Any, output_model: type[T]) -> T:
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", None) == "refusal":
            raise LlmError("model refused the request")
        if getattr(item, "type", None) == "message":
            for block in item.content:
                if getattr(block, "type", None) == "output_text":
                    return output_model.model_validate_json(block.text)
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
