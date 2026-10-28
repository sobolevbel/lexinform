"""Bounded outer-URL candidate decisions without extracted bodies or inferred source relations."""

import hashlib
import json
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lexinform.models.documents import SelectionState
from lexinform.models.sejm import TextDocument

MAX_CANDIDATE_TRACE_BYTES = 4_096
CandidateTraceResult = Literal["selected", "ambiguous", "fallback"]
CandidateTraceReason = Literal[
    "version_choice",
    "version_conflict",
    "first_acceptable_candidate",
    "all_rejected",
]


class CandidateDisposition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    phase: Literal["version_comparison", "fallback"]
    index: int = Field(ge=0)
    outcome: Literal[
        "official_conflict",
        "read_failure",
        "non_bill",
        "unclassified",
        "policy_excluded",
        "selected",
        "unattempted",
    ]
    reason: Literal[
        "official_link_conflict",
        "invalid_response",
        "read_error",
        "non_bill_body",
        "scan_without_text",
        "ambiguous_extraction",
        "version_preference",
        "version_conflict",
        "appendix_kind",
        "download_size_limit",
        "no_readable_body",
        "first_acceptable_candidate",
        "version_choice",
        "selection_stopped",
    ]
    observed_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def observed_body_requires_a_read(self) -> Self:
        if self.observed_sha256 is not None and self.outcome in (
            "official_conflict",
            "read_failure",
            "unattempted",
        ):
            raise ValueError("an unattempted or failed candidate has no observed complete body")
        return self


class CandidateDispositionTrace(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    coverage: Literal["complete", "omitted_over_limit"]
    requested_document: TextDocument | None
    universe_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_count: int = Field(ge=1)
    omitted_dispositions: int = Field(ge=0)
    dispositions: tuple[CandidateDisposition, ...]
    result: CandidateTraceResult
    reason: CandidateTraceReason
    selection_state: SelectionState

    @classmethod
    def from_decisions(
        cls,
        requested: TextDocument,
        dispositions: tuple[CandidateDisposition, ...],
        *,
        result: CandidateTraceResult,
        reason: CandidateTraceReason,
        selection_state: SelectionState,
    ) -> Self:
        universe = json.dumps(
            requested.model_dump(mode="json"),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        universe_sha256 = hashlib.sha256(universe.encode()).hexdigest()
        candidate_count = 1 + len(requested.alternatives)
        complete = cls.model_construct(
            schema_version=1,
            coverage="complete",
            requested_document=requested,
            universe_sha256=universe_sha256,
            candidate_count=candidate_count,
            omitted_dispositions=0,
            dispositions=dispositions,
            result=result,
            reason=reason,
            selection_state=selection_state,
        )
        encoded = complete.model_dump_json(exclude_defaults=True)
        if len(encoded.encode()) <= MAX_CANDIDATE_TRACE_BYTES:
            return cls.model_validate_json(encoded)
        return cls(
            coverage="omitted_over_limit",
            requested_document=None,
            universe_sha256=universe_sha256,
            candidate_count=candidate_count,
            omitted_dispositions=len(dispositions),
            dispositions=(),
            result=result,
            reason=reason,
            selection_state=selection_state,
        )

    @model_validator(mode="after")
    def bounded_complete_trace(self) -> Self:
        if self.coverage == "complete":
            if self.requested_document is None or self.omitted_dispositions:
                raise ValueError("a complete trace requires its full original request")
            count = 1 + len(self.requested_document.alternatives)
            if self.candidate_count != count:
                raise ValueError("candidate count differs from the original request")
            universe = json.dumps(
                self.requested_document.model_dump(mode="json"),
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if self.universe_sha256 != hashlib.sha256(universe.encode()).hexdigest():
                raise ValueError("candidate universe digest differs from the original request")
            version = tuple(
                item.index for item in self.dispositions if item.phase == "version_comparison"
            )
            fallback = tuple(item.index for item in self.dispositions if item.phase == "fallback")
            expected = tuple(range(count))
            if version and version != expected or fallback and fallback != expected:
                raise ValueError("candidate phases must cover the ordered requested candidates")
            if (
                bool(version) != self.requested_document.compare_versions
                or not fallback
                and not version
            ):
                raise ValueError("candidate phases differ from the requested selection path")
            ordered = tuple(
                item for item in self.dispositions if item.phase == "version_comparison"
            ) + tuple(item for item in self.dispositions if item.phase == "fallback")
            if self.dispositions != ordered:
                raise ValueError("candidate decisions must retain phase order")
            selected = tuple(item for item in self.dispositions if item.outcome == "selected")
            if (self.result == "selected") != (len(selected) == 1):
                raise ValueError("candidate result differs from its selected disposition")
            if self.result == "ambiguous" and (fallback or self.reason != "version_conflict"):
                raise ValueError("version ambiguity cannot claim a fallback decision")
            if self.result == "ambiguous" and self.selection_state != "ambiguous":
                raise ValueError("version ambiguity requires an ambiguous source selection")
            if self.result == "fallback" and (not fallback or self.reason != "all_rejected"):
                raise ValueError("fallback result requires a completed fallback phase")
            if self.result == "selected" and self.reason not in (
                "version_choice",
                "first_acceptable_candidate",
            ):
                raise ValueError("selected result requires a selection reason")
            if any(
                item.outcome == "unattempted" and item.phase != "fallback"
                for item in self.dispositions
            ):
                raise ValueError("only fallback may stop before later candidates")
        elif (
            self.requested_document is not None
            or self.dispositions
            or not self.omitted_dispositions
        ):
            raise ValueError("an omitted trace cannot claim partial candidate dispositions")
        if len(self.model_dump_json(exclude_defaults=True).encode()) > MAX_CANDIDATE_TRACE_BYTES:
            raise ValueError("candidate trace exceeds its metadata limit")
        return self

    @property
    def trace_id(self) -> str:
        encoded = json.dumps(
            self.model_dump(mode="json", exclude_defaults=True),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return "candidate-trace-v1:" + hashlib.sha256(encoded.encode()).hexdigest()

    @property
    def is_routine_singleton(self) -> bool:
        return (
            self.coverage == "complete"
            and self.candidate_count == 1
            and self.result == "selected"
            and self.selection_state in ("selected", "unverified")
            and all(item.outcome == "selected" for item in self.dispositions)
        )
