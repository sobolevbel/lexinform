"""The aggregate the services work on: a bill with its status, stages, analysis, submission,
act and authors, plus the two bookkeeping rows (publications and detected status changes)."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field

from lexinform.models.analysis import AnalysisRecord
from lexinform.models.enums import BillStatus, PublicationKind, PublicationStatus
from lexinform.models.sejm import (
    ActInfo,
    BillAuthors,
    BillSubmission,
    ProcessSummary,
    Stage,
    flatten_stages,
)


class Bill(BaseModel):
    summary: ProcessSummary
    status: BillStatus
    prefilter_hits: list[str] = Field(default_factory=list)
    stages: tuple[Stage, ...] = ()
    stages_fingerprint: str | None = None
    analysis: AnalysisRecord | None = None
    analysis_attempts: int = 0
    last_error: str | None = None
    submission: BillSubmission | None = None  # the /bills entry (consultation dates, RPW number)
    linked_number: str | None = None  # RPW <-> print number once the print is assigned
    act: ActInfo | None = None  # the published act, once it appears in Dziennik Ustaw
    authors: BillAuthors | None = None  # signatories of a deputies' bill, by club
    first_seen_at: dt.datetime
    last_checked_at: dt.datetime

    @property
    def term(self) -> int:
        return self.summary.term

    @property
    def number(self) -> str:
        return self.summary.number

    @property
    def is_pre_print(self) -> bool:
        return self.summary.is_pre_print

    @property
    def last_stage(self) -> Stage | None:
        flat = flatten_stages(self.stages)
        return flat[-1] if flat else None


class Publication(BaseModel):
    id: int | None = None
    term: int
    number: str
    attempts: int = 0
    kind: PublicationKind
    status: PublicationStatus
    channel_id: str
    message_id: int | None = None
    document_message_ids: list[int] = Field(default_factory=list)
    status_change_id: int | None = None
    created_at: dt.datetime
    sent_at: dt.datetime | None = None
    error: str | None = None


class StatusChange(BaseModel):
    id: int | None = None
    term: int
    number: str
    old_fingerprint: str | None
    new_fingerprint: str
    new_stages: list[Stage]
    closure_detected: bool = False
    passed: bool | None = None
    content_changed: bool = False
    withdrawn: bool = False  # pre-print bill withdrawn before getting a print number
    detected_at: dt.datetime
