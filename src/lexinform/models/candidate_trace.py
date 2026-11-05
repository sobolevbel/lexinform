"""Bounded outer-URL candidate decisions without extracted bodies or inferred source relations."""

import hashlib
import json
import re
from collections.abc import Iterable
from typing import Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lexinform.metadata_urls import metadata_url_decision
from lexinform.models.documents import DocumentPart, SelectionState
from lexinform.models.physical_inventory import HeaderDisposition, Position, Sha256
from lexinform.models.sejm import TextDocument
from lexinform.sections import APPENDIX_KINDS, Kind

MAX_CANDIDATE_TRACE_BYTES = 4_096
CandidateTraceResult = Literal["selected", "ambiguous", "fallback"]
NormativeAnnexGapReason = Literal["no_returned_file", "returned_no_text", "non_annex_appendix"]
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
    "no_bill_candidate",
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
    return _disposition_rows_sha256(rows)


def _disposition_rows_sha256(rows: Iterable[BaseModel]) -> str:
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


class ArchiveMemberRowChecker:
    """Check known row relations without changing or enriching supplied facts."""

    def __init__(self) -> None:
        self._rows: list[ArchiveMemberDisposition] = []
        self._paths: set[tuple[str, str, int, str, tuple[int, ...]]] = set()
        self._slots: set[tuple[str, str, int, int]] = set()

    def add(self, row: ArchiveMemberDisposition) -> ArchiveMemberDisposition:
        row = ArchiveMemberDisposition.model_validate(row)
        if row.ordinals is not None and row.root_sha256 is not None:
            key = (row.stage, row.phase, row.index, row.root_sha256, row.ordinals)
            if key in self._paths:
                raise ValueError("duplicate archive occurrence decision within one stage")
            self._paths.add(key)
        if row.physical_part_index is not None:
            slot = (row.stage, row.phase, row.index, row.physical_part_index)
            if slot in self._slots:
                raise ValueError("duplicate physical slot decision within one archive stage")
            self._slots.add(slot)
        for previous in self._rows:
            if (row.phase, row.index) != (previous.phase, previous.index):
                continue
            if (
                row.root_sha256 is not None
                and previous.root_sha256 is not None
                and row.root_sha256 != previous.root_sha256
            ):
                raise ValueError("one outer attempt cannot claim conflicting known archive roots")
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
                        raise ValueError("known archive occurrence facts disagree across stages")
        self._rows.append(row)
        return row


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
        checker = ArchiveMemberRowChecker()
        for row in self.rows:
            checker.add(row)
        if len(self.model_dump_json().encode()) > MAX_ARCHIVE_MEMBER_BYTES:
            raise ValueError("archive member section exceeds its metadata limit")
        return self


class RequiredPartExclusion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    phase: Literal["version_comparison", "fallback"]
    index: Position
    required_index: Position
    reason: Literal["required_non_annex_appendix"]
    kind: Kind
    file_sha256: Sha256 | None = Field(default=None, exclude_if=lambda value: value is None)
    text_sha256: Sha256 | None = Field(default=None, exclude_if=lambda value: value is None)
    parts: tuple[DocumentPart, ...]

    @model_validator(mode="before")
    @classmethod
    def declared_part_fields(cls, value: object) -> object:
        if isinstance(value, dict) and isinstance(value.get("parts"), (list, tuple)):
            for part in value["parts"]:
                if isinstance(part, dict) and set(part) - DocumentPart.model_fields.keys():
                    raise ValueError("required descriptors cannot hide undeclared body fields")
        return value

    @model_validator(mode="after")
    def body_free_descriptors(self) -> Self:
        if self.kind not in APPENDIX_KINDS - {"annex"}:
            raise ValueError("required exclusion must describe a non-annex appendix")
        for part in self.parts:
            if part.text or part.url and not metadata_url_decision(part.url).admitted:
                raise ValueError("required exclusion requires body-free admitted metadata")
            if any(
                value and re.fullmatch(r"[0-9a-f]{64}", value) is None
                for value in (part.sha256, part.container_sha256, part.text_sha256)
            ):
                raise ValueError("required descriptor hashes must be known SHA256 or absent")
            if self.file_sha256 and part.source_sha256 and self.file_sha256 != part.source_sha256:
                raise ValueError("required descriptor differs from its known file root")
        return self


def required_part_rows_sha256(rows: Iterable[RequiredPartExclusion]) -> str:
    return _disposition_rows_sha256(rows)


class RequiredPartExclusions(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    schema_version: Literal[1] = 1
    retention: Literal["complete", "omitted"]
    observed_rows: Position
    rows_sha256: Sha256
    omitted_rows: Position
    rows: tuple[RequiredPartExclusion, ...]

    @model_validator(mode="before")
    @classmethod
    def explicit_schema_type(cls, value: object) -> object:
        if (
            isinstance(value, dict)
            and "schema_version" in value
            and type(value["schema_version"]) is not int
        ):
            raise ValueError("required exclusion schema version must be an integer")
        return value

    @model_validator(mode="after")
    def actual_retention(self) -> Self:
        if not self.observed_rows or self.observed_rows != len(self.rows) + self.omitted_rows:
            raise ValueError("required exclusion counts must describe actual observed rows")
        if self.retention == "complete":
            if self.omitted_rows or self.rows_sha256 != required_part_rows_sha256(self.rows):
                raise ValueError("complete required exclusions require their exact whole hash")
        elif self.rows or self.omitted_rows != self.observed_rows:
            raise ValueError("omitted required exclusions retain only their full count and hash")
        keys = [(row.phase == "fallback", row.index, row.required_index) for row in self.rows]
        if keys != sorted(set(keys)):
            raise ValueError("required exclusions must retain unique ordered relation positions")
        if len(self.model_dump_json(exclude_defaults=True).encode()) > MAX_CANDIDATE_TRACE_BYTES:
            raise ValueError("required exclusions exceed the existing whole trace budget")
        return self


class NormativeAnnexGap(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    phase: Literal["version_comparison", "fallback"]
    index: Position
    normative_index: Position
    reason: NormativeAnnexGapReason
    file_sha256: Sha256 | None = Field(default=None, exclude_if=lambda value: value is None)
    kind: Kind | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def observed_return(self) -> Self:
        if self.reason == "no_returned_file" and self.file_sha256 is not None:
            raise ValueError("a known file hash requires a returned file")
        if self.reason == "non_annex_appendix":
            if self.kind not in APPENDIX_KINDS - {"annex"}:
                raise ValueError("a non-annex gap requires the actual appendix kind")
        elif self.kind is not None:
            raise ValueError("an appendix kind requires returned text")
        return self


class SejmOriginalProposal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    profile: Literal["sejm-original-proposal-v1"] = "sejm-original-proposal-v1"
    relation: Literal["proposes_change_to"] = "proposes_change_to"
    term: int = Field(gt=0, le=99, strict=True)
    parent_print: str = Field(pattern=r"^[1-9][0-9]{0,8}$")
    proposal_print: str = Field(pattern=r"^[1-9][0-9]{0,8}-A$")
    official_process_prints: tuple[str]
    base_sha256: Sha256
    proposal_sha256: Sha256
    selection_id: str = Field(pattern=r"^selection-v1:[0-9a-f]{64}$")
    edition_id: str = Field(pattern=r"^edition-v1:[0-9a-f]{64}$")

    @property
    def base_url(self) -> str:
        return self._print_url(self.parent_print)

    @property
    def proposal_url(self) -> str:
        return self._print_url(self.proposal_print)

    def _print_url(self, number: str) -> str:
        return f"https://api.sejm.gov.pl/sejm/term{self.term}/prints/{number}/{number}.pdf"

    @model_validator(mode="after")
    def known_pair(self) -> Self:
        if (
            self.proposal_print != f"{self.parent_print}-A"
            or self.official_process_prints != (self.parent_print,)
            or self.base_sha256 == self.proposal_sha256
        ):
            raise ValueError("Sejm proposal requires its one same-term parent and distinct bodies")
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
    required_exclusions: RequiredPartExclusions | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    normative_annex_gap: NormativeAnnexGap | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    sejm_original_proposal: SejmOriginalProposal | None = Field(
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
            for member in members.rows:
                validate_archive_member_parent(self, member)
        if self.required_exclusions is not None:
            if self.coverage != "complete" and self.required_exclusions.rows:
                raise ValueError("omitted outer requests retain only required exclusion summaries")
            for row in self.required_exclusions.rows:
                validate_required_part_parent(self, row)
        if self.normative_annex_gap is not None:
            validate_normative_annex_parent(self, self.normative_annex_gap)
        if self.sejm_original_proposal is not None:
            validate_sejm_original_proposal(self, self.sejm_original_proposal)
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
            and self.required_exclusions is None
            and self.sejm_original_proposal is None
        )


def _parent_disposition(
    trace: CandidateDispositionTrace, phase: str, index: int
) -> CandidateDisposition | None:
    return next(
        (item for item in trace.dispositions if (item.phase, item.index) == (phase, index)),
        None,
    )


def validate_sejm_original_proposal(
    trace: CandidateDispositionTrace, proposal: SejmOriginalProposal
) -> None:
    request = trace.requested_document
    if (
        request is None
        or request.kind != "print"
        or request.url != proposal.base_url
        or request.version_basis != (f"sejm/{proposal.term}/print/{proposal.parent_print}",)
        or request.amendment_urls != (proposal.proposal_url,)
        or request.alternatives
        or request.extra_urls
        or request.required_urls
        or request.normative_urls
        or request.official_link_conflict
        or not request.compare_versions
        or request.senate is not None
        or trace.candidate_count != 1
        or trace.result != "selected"
        or trace.reason != "version_choice"
        or trace.selection_state not in ("selected", "unverified")
        or trace.archive_members is not None
        or trace.required_exclusions is not None
        or trace.normative_annex_gap is not None
    ):
        raise ValueError("Sejm proposal requires the selected original singleton print")
    disposition = _parent_disposition(trace, "version_comparison", 0)
    if (
        disposition is None
        or disposition.outcome != "selected"
        or disposition.reason != "version_choice"
        or disposition.observed_sha256 != proposal.base_sha256
    ):
        raise ValueError("Sejm proposal requires the observed original PDF identity")


def validate_normative_annex_parent(
    trace: CandidateDispositionTrace, gap: NormativeAnnexGap
) -> None:
    request = trace.requested_document
    if request is None or gap.index >= trace.candidate_count:
        raise ValueError("a normative annex gap requires its retained outer request")
    parent = _parent_disposition(trace, gap.phase, gap.index)
    expected_reason = (
        "version_choice" if gap.phase == "version_comparison" else ("first_acceptable_candidate")
    )
    if (
        parent is None
        or parent.outcome != "selected"
        or trace.result != "selected"
        or parent.reason != expected_reason
        or trace.reason != expected_reason
    ):
        raise ValueError("a normative annex gap requires the selected outer disposition")
    expected_state = "ambiguous" if gap.reason == "non_annex_appendix" else "waiting"
    if trace.selection_state != expected_state:
        raise ValueError("normative annex gap differs from the blocked selection state")
    candidate = (request, *request.alternatives)[gap.index]
    if gap.normative_index >= len(candidate.normative_urls):
        raise ValueError("normative annex gap exceeds the actual normative URL namespace")
    if not metadata_url_decision(candidate.normative_urls[gap.normative_index]).admitted:
        raise ValueError("normative annex gap parent URL is outside metadata admission")


def validate_required_part_parent(
    trace: CandidateDispositionTrace, row: RequiredPartExclusion
) -> None:
    if row.index >= trace.candidate_count or trace.requested_document is None:
        raise ValueError("required exclusion has no retained outer request parent")
    parent = _parent_disposition(trace, row.phase, row.index)
    if parent is None or parent.outcome in ("official_conflict", "unattempted"):
        raise ValueError("required exclusion requires an actually attempted outer parent")
    candidate = (trace.requested_document, *trace.requested_document.alternatives)[row.index]
    if row.required_index >= len(candidate.required_urls):
        raise ValueError("required exclusion exceeds the actual required URL namespace")
    if not metadata_url_decision(candidate.required_urls[row.required_index]).admitted:
        raise ValueError("required exclusion parent URL is outside metadata admission")


def validate_archive_member_parent(
    trace: CandidateDispositionTrace, member: ArchiveMemberDisposition
) -> None:
    """Check one row against retained outer facts without inventing omitted parents."""
    if member.index >= trace.candidate_count:
        raise ValueError("archive member outer index exceeds the known candidate count")
    if trace.coverage == "complete":
        parent = _parent_disposition(trace, member.phase, member.index)
        if parent is None or parent.outcome in ("official_conflict", "unattempted"):
            raise ValueError("archive member must reference an actually attempted outer phase")
        if parent.outcome == "read_failure" and member.stage != "walk":
            raise ValueError("a failed outer read cannot certify a completed member policy")
        if (
            parent.observed_sha256 is not None
            and member.root_sha256 is not None
            and parent.observed_sha256 != member.root_sha256
        ):
            raise ValueError("archive root differs from its known outer complete body")
