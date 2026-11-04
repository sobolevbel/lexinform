"""Bounded outer-URL candidate decisions without extracted bodies or inferred source relations."""

import hashlib
import json
from collections.abc import Iterable
from typing import Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lexinform.models.documents import SelectionState
from lexinform.models.physical_inventory import HeaderDisposition, Position, Sha256
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


MAX_ARCHIVE_MEMBER_BYTES = 1024
MAX_ARCHIVE_MEMBER_ROWS = 8
ArchiveMemberPolicyReason = Literal[
    "selected_main",
    "selected_annex",
    "selected_support",
    "rank_preference",
    "version_preference",
    "version_conflict",
    "family_exclusion",
    "non_bill_body",
    "unknown_role",
    "regulation_support",
    "empty_text",
    "long_unknown_text",
]


class ArchiveMemberDisposition(BaseModel):
    """A physical slot alone never establishes its root ordinal occurrence association."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    stage: Literal["walk", "policy"]
    phase: Literal["version_comparison", "fallback"]
    index: Position
    root_sha256: Sha256 | None = Field(default=None, exclude_if=lambda value: value is None)
    ordinals: tuple[Position, ...] | None = Field(
        default=None, min_length=1, max_length=4, exclude_if=lambda value: value is None
    )
    physical_part_index: Position | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    outcome: Literal["observed", "selected", "policy_excluded", "non_bill", "unclassified"]
    reason: ArchiveMemberPolicyReason | HeaderDisposition
    leaf_sha256: Sha256 | None = Field(default=None, exclude_if=lambda value: value is None)
    text_sha256: Sha256 | None = Field(default=None, exclude_if=lambda value: value is None)

    @property
    def association(self) -> Literal["known", "unknown"]:
        return "known" if self.root_sha256 is not None and self.ordinals is not None else "unknown"

    @model_validator(mode="after")
    def local_facts(self) -> Self:
        if self.ordinals is None and self.physical_part_index is None:
            raise ValueError("an archive row requires an actual ordinal path or physical slot")
        if self.ordinals is not None and self.root_sha256 is None:
            raise ValueError("a known ordinal path requires its actual root hash")
        if self.stage == "walk":
            if self.outcome != "observed" or self.reason not in get_args(HeaderDisposition):
                raise ValueError("walk rows describe physical statuses, never policy selections")
        elif self.outcome == "observed" or self.reason not in get_args(ArchiveMemberPolicyReason):
            raise ValueError("policy rows require a policy outcome and branch reason")
        elif (self.outcome == "selected") != self.reason.startswith("selected_"):
            raise ValueError("selected policy outcomes require their actual selection role")
        return self


def archive_member_rows_sha256(rows: Iterable[ArchiveMemberDisposition]) -> str:
    digest = hashlib.sha256(b"[")
    separator = b""
    for row in rows:
        digest.update(separator)
        digest.update(
            json.dumps(
                row.model_dump(mode="json", exclude_defaults=True),
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        )
        separator = b","
    digest.update(b"]")
    return digest.hexdigest()


class ArchiveMemberDispositions(BaseModel):
    """Retention covers observed rows only, independently of walk and descendant completeness."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    schema_version: Literal[1] = 1
    walk: Literal["finished", "interrupted", "unknown"]
    retention: Literal["complete", "prefix", "omitted", "unknown"]
    observed_rows: Position | None
    rows_sha256: Sha256 | None
    omitted_rows: Position | None
    unvisited: Literal["none_known", "known", "unknown"]
    rows: tuple[ArchiveMemberDisposition, ...] = Field(
        default=(), max_length=MAX_ARCHIVE_MEMBER_ROWS
    )

    @model_validator(mode="after")
    def bounded_observations(self) -> Self:
        observed, omitted, digest = self.observed_rows, self.omitted_rows, self.rows_sha256
        if self.retention == "unknown":
            if (
                observed is not None
                or omitted is not None
                or digest is not None
                or self.rows
                or self.walk != "unknown"
                or self.unvisited != "unknown"
            ):
                raise ValueError("unknown archive coverage cannot claim observed counts or rows")
        else:
            if observed is None or omitted is None or digest is None:
                raise ValueError("observed archive rows require their actual counts and full hash")
            if observed != len(self.rows) + omitted:
                raise ValueError("archive retained and omitted counts differ from observed rows")
            if self.retention == "complete":
                if omitted or digest != archive_member_rows_sha256(self.rows):
                    raise ValueError(
                        "complete archive retention requires the canonical whole row hash"
                    )
            elif self.retention == "prefix":
                if not self.rows or not omitted:
                    raise ValueError(
                        "an archive prefix requires retained and omitted observed rows"
                    )
            elif self.rows or not omitted:
                raise ValueError("omitted archive rows require a nonempty bounded summary only")
        paths: set[tuple[str, str, int, str, tuple[int, ...]]] = set()
        slots: set[tuple[str, str, int, int]] = set()
        for row in self.rows:
            if row.ordinals is not None and row.root_sha256 is not None:
                key = (row.stage, row.phase, row.index, row.root_sha256, row.ordinals)
                if key in paths:
                    raise ValueError("duplicate archive occurrence decision within one stage")
                paths.add(key)
            if row.physical_part_index is not None:
                slot = (row.stage, row.phase, row.index, row.physical_part_index)
                if slot in slots:
                    raise ValueError("duplicate physical slot decision within one archive stage")
                slots.add(slot)
        for index, row in enumerate(self.rows):
            for previous in self.rows[:index]:
                if (row.phase, row.index) != (previous.phase, previous.index):
                    continue
                if (
                    row.root_sha256 is not None
                    and previous.root_sha256 is not None
                    and row.root_sha256 != previous.root_sha256
                ):
                    raise ValueError(
                        "one outer attempt cannot claim conflicting known archive roots"
                    )
                same_slot = (
                    row.physical_part_index is not None
                    and row.physical_part_index == previous.physical_part_index
                )
                same_path = (
                    row.root_sha256 is not None
                    and row.ordinals is not None
                    and (row.root_sha256, row.ordinals) == (previous.root_sha256, previous.ordinals)
                )
                if same_slot or same_path:
                    for left, right in zip(
                        (
                            row.root_sha256,
                            row.ordinals,
                            row.physical_part_index,
                            row.leaf_sha256,
                            row.text_sha256,
                        ),
                        (
                            previous.root_sha256,
                            previous.ordinals,
                            previous.physical_part_index,
                            previous.leaf_sha256,
                            previous.text_sha256,
                        ),
                        strict=True,
                    ):
                        if left is not None and right is not None and left != right:
                            raise ValueError(
                                "known archive occurrence facts disagree across stages"
                            )
        if len(self.model_dump_json().encode()) > MAX_ARCHIVE_MEMBER_BYTES:
            raise ValueError("archive member section exceeds its metadata limit")
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
    archive_members: ArchiveMemberDispositions | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

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
        members = self.archive_members
        if members is not None:
            if self.coverage != "complete" and members.rows:
                raise ValueError("an omitted outer request retains only an archive summary")
            parents = {(item.phase, item.index): item for item in self.dispositions}
            for member in members.rows:
                if member.index >= self.candidate_count:
                    raise ValueError("archive member outer index exceeds the known candidate count")
                if self.coverage == "complete":
                    parent = parents.get((member.phase, member.index))
                    if parent is None or parent.outcome in ("official_conflict", "unattempted"):
                        raise ValueError(
                            "archive member must reference an actually attempted outer phase"
                        )
                    if parent.outcome == "read_failure" and member.stage != "walk":
                        raise ValueError(
                            "a failed outer read cannot certify a completed member policy"
                        )
                    if (
                        parent.observed_sha256 is not None
                        and member.root_sha256 is not None
                        and parent.observed_sha256 != member.root_sha256
                    ):
                        raise ValueError("archive root differs from its known outer complete body")
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
