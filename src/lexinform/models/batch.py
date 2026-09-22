"""Submitting a full analysis or a re-analysis to a provider's batch API and collecting it later.

Only `analysis`/`reanalysis` are batched (Phase 1); `CallKind` is shared with `models.report` so
that `amendments`/`supplement`/`joint` extend the same shape without a new type once they join.
"""

import datetime as dt
from typing import Literal

from pydantic import BaseModel

from lexinform.models.analysis import Analysis, BillContext
from lexinform.models.enums import SourceKind, TextSource
from lexinform.models.report import CallKind

BatchProvider = Literal["anthropic", "openai"]
BatchStatus = Literal["submitted", "ended", "failed"]


class BatchRequest(BaseModel):
    """One item of a submission; `custom_id` is how its answer finds its way back to a bill."""

    custom_id: str
    call_kind: CallKind
    term: int
    number: str
    ctx: BillContext


class BatchResult(BaseModel):
    """One line of a collected batch: what the model said and what it cost, or why there is
    nothing — never more than that. `input_chars`/`source_url`/`revision`/… are not the
    provider's to know; `AnalysisService.collect_batches` attaches them from `BatchItemMeta`."""

    custom_id: str
    analysis: Analysis | None = None
    model: str | None = None
    prompt_version: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    error: str | None = None


class LlmBatch(BaseModel):
    """A submission's own bookkeeping row (`llm_batches`)."""

    batch_id: str
    provider: BatchProvider
    call_kind: CallKind
    submitted_at: dt.datetime
    status: BatchStatus
    polled_at: dt.datetime | None = None
    completed_at: dt.datetime | None = None
    request_count: int
    estimated_cost_usd: float


class BatchItemMeta(BaseModel):
    """What was already known about a request when it was queued — the provenance `_prepare`
    would otherwise attach to the `AnalysisRecord` right after calling the model, and that the
    model's answer alone can never supply. All of it is derivable from the `BillContext` that was
    sent except `source_url` and `revision`, which is why only those two are not."""

    input_chars: int
    truncated: bool
    text_source: TextSource
    source_kind: SourceKind
    source_url: str | None
    revision: int
    text_sha256: str | None
    prompt_version: str = ""


class LlmBatchItem(BaseModel):
    """Which bill one `custom_id` of a batch answers for, and whether it has been written back."""

    batch_id: str
    custom_id: str
    call_kind: CallKind
    term: int
    number: str
    meta: BatchItemMeta
    consumed_at: dt.datetime | None = None


class BatchIntent(BaseModel):
    request: BatchRequest
    meta: BatchItemMeta
    provider: BatchProvider
    state: Literal["queued", "submitting"] = "queued"
    created_at: dt.datetime
