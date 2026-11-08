"""Bounded outer-URL candidate decisions without extracted bodies or inferred source relations."""

import datetime as dt
import hashlib
import json
import re
from collections.abc import Iterable
from typing import Annotated, Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lexinform.metadata_urls import metadata_url_decision
from lexinform.models.documents import DocumentPart, SelectionState
from lexinform.models.evidence import DecisionState, ReadingOutcome, reading_evidence
from lexinform.models.physical_inventory import HeaderDisposition, Position, Sha256
from lexinform.models.sejm import Stage, TextDocument, sejm_adopted_text_url
from lexinform.sections import APPENDIX_KINDS, Kind

MAX_CANDIDATE_TRACE_BYTES = 4_096
CandidateTraceResult = Literal["selected", "ambiguous", "fallback", "waiting"]
ReadExclusionReason = Literal[
    "missing_response", "refused_response", "pre_send_rejected", "read_error"
]
ReturnedPartGapReason = Literal[
    "returned_no_text", "download_size_limit", "pdf_page_extraction_failed"
]
NormativeAnnexGapReason = Literal[
    "no_returned_file", "non_annex_appendix", ReturnedPartGapReason, ReadExclusionReason
]
CandidateTraceReason = Literal[
    "version_choice",
    "version_conflict",
    "version_input_incomplete",
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
        "pdf_page_extraction_failed",
        "version_comparison_deferred",
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
        if self.reason in ("pdf_page_extraction_failed", "version_comparison_deferred") and (
            self.phase != "version_comparison"
            or self.outcome != "unclassified"
            or self.observed_sha256 is None
        ):
            raise ValueError("a deferred comparison disposition requires the observed body")
        return self


MAX_ARCHIVE_MEMBER_BYTES = 1024
MAX_ARCHIVE_MEMBER_ROWS = 8
MAX_PACKED_ARCHIVE_MEMBER_BYTES = 1_152
MAX_PACKED_ARCHIVE_MEMBER_ROWS = 32
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
    "archive_pdf_page_failure",
    "archive_comparison_deferred",
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
        if self.reason in ("archive_pdf_page_failure", "archive_comparison_deferred") and (
            self.outcome != "unclassified" or self.leaf_sha256 is None
        ):
            raise ValueError("a deferred archive row requires an observed body without a verdict")
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


_PACKED_STAGES = ("walk", "policy")
_PACKED_PHASES = ("version_comparison", "fallback")
_PACKED_OUTCOMES = ("observed", "selected", "policy_excluded", "non_bill", "unclassified")
_PACKED_REASONS = (
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
    "uninspected_format_part",
    "readability_rejected",
    "member_complete",
    "member_failed",
    "member_returned_length_differs",
    "unpacking_limit_source_pinned_inference",
    "not_visited_depth_limit",
    "format_complete",
    "format_failed",
    "format_returned_length_differs",
    "archive_pdf_page_failure",
    "archive_comparison_deferred",
)
type _PackedArchiveRow = tuple[
    Position,
    Position,
    Position,
    Position | None,
    Annotated[tuple[Position, ...], Field(min_length=1, max_length=4)] | None,
    Position | None,
    Position,
    Position,
    Position | None,
    Position | None,
]


class PackedArchiveMemberDispositions(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    schema_version: Literal[2]
    walk: Literal["finished", "interrupted", "unknown"]
    retention: Literal["complete"]
    observed_rows: Position
    rows_sha256: Sha256
    omitted_rows: Literal[0]
    unvisited: Literal["none_known", "known", "unknown"]
    packed_rows: tuple[_PackedArchiveRow, ...] = Field(
        min_length=1, max_length=MAX_PACKED_ARCHIVE_MEMBER_ROWS
    )
    hashes: tuple[Sha256, ...] = Field(max_length=MAX_PACKED_ARCHIVE_MEMBER_ROWS * 3)

    @field_validator("schema_version", "omitted_rows", mode="before")
    @classmethod
    def strict_integer(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("packed archive versions and counts require integers")
        return value

    @property
    def rows(self) -> tuple[ArchiveMemberDisposition, ...]:
        try:
            return tuple(self._decode(row) for row in self.packed_rows)
        except IndexError:
            raise ValueError("packed archive row contains an out-of-range code") from None

    def _decode(self, row: _PackedArchiveRow) -> ArchiveMemberDisposition:
        stage, phase, index, root, path, slot, outcome, reason, leaf, text = row

        def lookup(position: int | None) -> str | None:
            return self.hashes[position] if position is not None else None

        return ArchiveMemberDisposition.model_validate(
            {
                "stage": _PACKED_STAGES[stage],
                "phase": _PACKED_PHASES[phase],
                "index": index,
                "root_sha256": lookup(root),
                "ordinals": path,
                "physical_part_index": slot,
                "outcome": _PACKED_OUTCOMES[outcome],
                "reason": _PACKED_REASONS[reason],
                "leaf_sha256": lookup(leaf),
                "text_sha256": lookup(text),
            }
        )

    @model_validator(mode="after")
    def bounded_observations(self) -> Self:
        if len(self.model_dump_json().encode()) > MAX_PACKED_ARCHIVE_MEMBER_BYTES:
            raise ValueError("packed archive member section exceeds its metadata limit")
        positions = tuple(
            dict.fromkeys(
                index
                for row in self.packed_rows
                for index in (row[3], row[8], row[9])
                if index is not None
            )
        )
        if positions != tuple(range(len(self.hashes))) or len(set(self.hashes)) != len(self.hashes):
            raise ValueError(
                "packed archive hashes must be unique and used in first-occurrence order"
            )
        rows = self.rows
        if self.observed_rows != len(rows) or self.rows_sha256 != archive_member_rows_sha256(rows):
            raise ValueError("complete archive retention requires the canonical whole row hash")
        checker = ArchiveMemberRowChecker()
        for row in rows:
            checker.add(row)
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


MAX_SUPPORTING_EXCLUSIONS = 8
SupportingExclusionReason = Literal[
    "draft_regulation",
    "returned_no_text",
    "download_size_limit",
    ReadExclusionReason,
]


class SupportingPartExclusion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    phase: Literal["version_comparison", "fallback"]
    index: Position
    extra_index: Position
    reason: SupportingExclusionReason
    file_sha256: Sha256 | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def observed_body(self) -> Self:
        if (
            self.reason not in ("draft_regulation", "returned_no_text")
            and self.file_sha256 is not None
        ):
            raise ValueError("A failed supporting read cannot claim a complete body")
        return self


class SupportingPartExclusions(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    rows: tuple[SupportingPartExclusion, ...] = Field(
        min_length=1, max_length=MAX_SUPPORTING_EXCLUSIONS
    )
    omitted: int = Field(default=0, ge=0, strict=True)

    @model_validator(mode="after")
    def ordered_prefix(self) -> Self:
        first = self.rows[0]
        positions = [row.extra_index for row in self.rows]
        if (
            any((row.phase, row.index) != (first.phase, first.index) for row in self.rows)
            or positions != sorted(set(positions))
            or (self.omitted and len(self.rows) != MAX_SUPPORTING_EXCLUSIONS)
        ):
            raise ValueError(
                "Supporting exclusions require an ordered bounded prefix of one candidate"
            )
        return self


MAX_ADMITTED_SUPPORT = 8


class AdmittedSupportPart(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    extra_index: Position
    role: Literal["justification", "osr"]
    file_sha256: Sha256
    text_sha256: Sha256


class AdmittedSupport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    phase: Literal["version_comparison", "fallback"]
    index: Position
    rows: tuple[AdmittedSupportPart, ...] = Field(min_length=1, max_length=MAX_ADMITTED_SUPPORT)
    omitted: int = Field(default=0, ge=0, strict=True)

    @model_validator(mode="after")
    def ordered_prefix(self) -> Self:
        positions = [row.extra_index for row in self.rows]
        if positions != sorted(set(positions)) or (
            self.omitted and len(self.rows) != MAX_ADMITTED_SUPPORT
        ):
            raise ValueError("Admitted support requires an ordered bounded prefix")
        return self


class ComposedSupportPart(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    extra_index: Position
    first_part: Position
    part_count: Position = Field(gt=0)
    parts_sha256: Sha256


class ComposedSupport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    phase: Literal["version_comparison", "fallback"]
    index: Position
    rows: tuple[ComposedSupportPart, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def ordered_disjoint_parts(self) -> Self:
        previous_extra = -1
        next_part = 0
        for row in self.rows:
            if row.extra_index <= previous_extra or row.first_part < next_part:
                raise ValueError("Composed support requires ordered, disjoint selected parts")
            previous_extra = row.extra_index
            next_part = row.first_part + row.part_count
        return self


def composed_support_sha256(parts: tuple[DocumentPart, ...]) -> str:
    payload = [part.model_dump(mode="json", exclude={"text"}) for part in parts]
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


MAX_ADMITTED_NORMATIVE_ANNEXES = 8


class AdmittedNormativeAnnex(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    normative_index: Position
    observed_kind: Kind
    file_sha256: Sha256
    text_sha256: Sha256

    @model_validator(mode="after")
    def admitted_kind(self) -> Self:
        if self.observed_kind in APPENDIX_KINDS - {"annex"}:
            raise ValueError("Observed kind was excluded by the normative annex policy")
        return self


class AdmittedNormativeAnnexes(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    phase: Literal["version_comparison", "fallback"]
    index: Position
    rows: tuple[AdmittedNormativeAnnex, ...] = Field(
        min_length=1, max_length=MAX_ADMITTED_NORMATIVE_ANNEXES
    )
    omitted: int = Field(default=0, ge=0, strict=True)

    @model_validator(mode="after")
    def ordered_prefix(self) -> Self:
        positions = [row.normative_index for row in self.rows]
        if positions != sorted(set(positions)) or (
            self.omitted and len(self.rows) != MAX_ADMITTED_NORMATIVE_ANNEXES
        ):
            raise ValueError("Admitted normative annexes require an ordered bounded prefix")
        return self


def _validate_returned_gap(reason: NormativeAnnexGapReason, file_sha256: str | None) -> None:
    if reason == "download_size_limit" and file_sha256 is not None:
        raise ValueError("A size refusal cannot claim a complete body")
    if reason == "pdf_page_extraction_failed" and file_sha256 is None:
        raise ValueError("A PDF page extraction failure requires the returned file hash")


class RequiredPartGap(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    phase: Literal["version_comparison", "fallback"]
    index: Position
    required_index: Position
    reason: ReturnedPartGapReason
    file_sha256: Sha256 | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def observed_return(self) -> Self:
        _validate_returned_gap(self.reason, self.file_sha256)
        return self


class AmendmentPartGap(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    phase: Literal["version_comparison", "fallback"]
    index: Position
    amendment_index: Position
    reason: ReturnedPartGapReason
    file_sha256: Sha256 | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def observed_return(self) -> Self:
        _validate_returned_gap(self.reason, self.file_sha256)
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
        _validate_returned_gap(self.reason, self.file_sha256)
        if (
            self.reason
            not in ("returned_no_text", "non_annex_appendix", "pdf_page_extraction_failed")
            and self.file_sha256 is not None
        ):
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


class SejmPrintRepresentation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    profile: Literal["sejm-print-core-representation-v1", "sejm-print-core-live-v1"] = (
        "sejm-print-core-representation-v1"
    )
    relation: Literal["representation_of"] = "representation_of"
    term: int = Field(gt=0, le=99, strict=True)
    number: str = Field(pattern=r"^[1-9][0-9]{0,8}$")
    listing_sha256: Sha256 | None = Field(default=None, exclude_if=lambda value: value is None)
    print_metadata_sha256: Sha256 | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    pdf_sha256: Sha256
    docx_sha256: Sha256
    pdf_text_sha256: Sha256
    docx_text_sha256: Sha256
    core_sha256: Sha256
    core_chars: int = Field(ge=200, strict=True)
    selection_id: str = Field(pattern=r"^selection-v1:[0-9a-f]{64}$")
    edition_id: str = Field(pattern=r"^edition-v1:[0-9a-f]{64}$")
    docx_extraction_id: str = Field(pattern=r"^extraction-v1:[0-9a-f]{64}$")

    @property
    def pdf_url(self) -> str:
        return (
            f"https://api.sejm.gov.pl/sejm/term{self.term}/prints/{self.number}/{self.number}.pdf"
        )

    @property
    def docx_url(self) -> str:
        return self.pdf_url.removesuffix(".pdf") + "-ustawa.docx"

    @model_validator(mode="after")
    def distinct_bodies(self) -> Self:
        offline = self.profile == "sejm-print-core-representation-v1"
        if (self.listing_sha256 is not None, self.print_metadata_sha256 is not None) != (
            offline,
            not offline,
        ):
            raise ValueError("Representation requires its exact profile metadata hash")
        if self.pdf_sha256 == self.docx_sha256:
            raise ValueError("Sejm representation requires distinct PDF and DOCX bodies")
        return self


class SejmSupersededDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    profile: Literal["sejm-committee-draft-pages-v1"] = "sejm-committee-draft-pages-v1"
    report: str = Field(pattern=r"^[1-9][0-9]{0,8}$")
    report_date: dt.date
    observation_id: str = Field(pattern=r"^source-observation-v1:[0-9a-f]{64}$")
    edition_id: str = Field(pattern=r"^edition-v1:[0-9a-f]{64}$")
    pdf_sha256: Sha256
    text_sha256: Sha256
    first_page: int = Field(ge=2, le=600, strict=True)
    last_page: int = Field(ge=2, le=600, strict=True)

    @model_validator(mode="after")
    def ordered_pages(self) -> Self:
        if self.first_page >= self.last_page:
            raise ValueError("Superseded committee draft needs a bounded physical page range")
        return self


class SejmAdoptedText(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    profile: Literal["sejm-third-reading-adopted-text-v1"] = "sejm-third-reading-adopted-text-v1"
    term: int = Field(gt=0, le=99, strict=True)
    number: str = Field(pattern=r"^[1-9][0-9]{0,8}$")
    stage_name: str = Field(min_length=1, max_length=128)
    decision: str = Field(min_length=1, max_length=128)
    stage_date: dt.date
    pdf_sha256: Sha256
    pdf_text_sha256: Sha256
    selection_id: str = Field(pattern=r"^selection-v1:[0-9a-f]{64}$")
    edition_id: str = Field(pattern=r"^edition-v1:[0-9a-f]{64}$")
    supersedes: SejmSupersededDraft | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @property
    def pdf_url(self) -> str:
        return sejm_adopted_text_url(self.term, self.number)

    @model_validator(mode="after")
    def known_third_reading(self) -> Self:
        evidence = reading_evidence(
            Stage(stage_name=self.stage_name, stage_type="SejmReading", decision=self.decision)
        )
        if (
            evidence.state is not DecisionState.KNOWN
            or evidence.reading is not ReadingOutcome.PASSED
        ):
            raise ValueError("Adopted text requires a known third-reading adoption")
        prior = self.supersedes
        if prior is not None and (
            prior.report == self.number
            or prior.report_date >= self.stage_date
            or prior.pdf_sha256 == self.pdf_sha256
            or prior.edition_id == self.edition_id
            or prior.text_sha256 == self.pdf_text_sha256
        ):
            raise ValueError("Adopted text must supersede a distinct earlier edition")
        return self


class SejmCommitteeRecommendation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    profile: Literal["sejm-committee-recommendation-v1"] = "sejm-committee-recommendation-v1"
    term: int = Field(gt=0, le=99, strict=True)
    number: str = Field(pattern=r"^[1-9][0-9]{0,8}$")
    report: str = Field(pattern=r"^[1-9][0-9]{0,8}$")
    resolution: str = Field(pattern=r"^[1-9][0-9]{0,8}$")
    report_date: dt.date
    resolution_date: dt.date
    heading_sha256: Sha256
    report_sha256: Sha256
    resolution_sha256: Sha256
    report_read_id: str = Field(pattern=r"^artifact-read-v1:[0-9a-f]{64}$")
    resolution_read_id: str = Field(pattern=r"^artifact-read-v1:[0-9a-f]{64}$")
    resolution_observation_id: str = Field(pattern=r"^source-observation-v1:[0-9a-f]{64}$")

    @property
    def report_url(self) -> str:
        return (
            f"https://api.sejm.gov.pl/sejm/term{self.term}/prints/{self.report}/{self.report}.pdf"
        )

    @property
    def resolution_url_prefix(self) -> str:
        return f"https://api.sejm.gov.pl/sejm/term{self.term}/prints/{self.resolution}/"

    @model_validator(mode="after")
    def distinct_earlier_resolution(self) -> Self:
        if (
            len({self.number, self.report, self.resolution}) != 3
            or self.resolution_date >= self.report_date
            or self.report_sha256 == self.resolution_sha256
        ):
            raise ValueError("Recommendation requires a distinct earlier Senate resolution")
        return self


MAX_VERSION_DECISION_ROWS = 8
VersionDate = Annotated[str, Field(strict=True, pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")]


class VersionDecisionEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    compared_indices: tuple[Position, ...] = Field(
        min_length=2, max_length=MAX_VERSION_DECISION_ROWS
    )
    retained_indices: tuple[Position, ...] = Field(
        min_length=1, max_length=MAX_VERSION_DECISION_ROWS
    )
    outcome: Literal["single_survivor", "matching_policy_identity", "distinct_policy_identity"]
    date_filter: tuple[VersionDate, ...] | None = Field(
        default=None, max_length=MAX_VERSION_DECISION_ROWS, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def comparison_steps(self) -> Self:
        compared = self.compared_indices
        if tuple(sorted(set(compared))) != compared:
            raise ValueError("version comparison requires unique ordered candidate positions")
        expected = compared
        if self.date_filter is not None:
            if len(self.date_filter) != len(compared) or len(set(self.date_filter)) < 2:
                raise ValueError(
                    "the saved date filter must eliminate at least one compared candidate"
                )
            dates = tuple(dt.date.fromisoformat(value) for value in self.date_filter)
            latest = max(dates)
            expected = tuple(
                index for index, date in zip(compared, dates, strict=True) if date == latest
            )
        if self.retained_indices != expected:
            raise ValueError("retained comparison positions differ from the applied date filter")
        if (len(expected) == 1) != (self.outcome == "single_survivor"):
            raise ValueError("version outcome differs from the number of retained candidates")
        return self


class ArchiveVersionDecisionEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    capture_id: Sha256
    selected_slot: Position
    peer: ArchiveMemberDisposition
    order: Literal["selected_first", "peer_first"]
    date_filter: tuple[VersionDate, VersionDate] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def successful_peer(self) -> Self:
        if (
            self.peer.stage != "policy"
            or self.peer.outcome != "policy_excluded"
            or self.peer.reason != "version_preference"
            or self.peer.association != "known"
            or self.peer.physical_part_index is None
            or self.peer.physical_part_index == self.selected_slot
            or self.peer.leaf_sha256 is None
            or self.peer.text_sha256 is None
        ):
            raise ValueError("archive comparison requires an exact excluded policy peer")
        if self.date_filter is None:
            if self.order != "selected_first":
                raise ValueError("an equal archive comparison chooses the first ranked peer")
        else:
            first, second = map(dt.date.fromisoformat, self.date_filter)
            if first == second or (first > second) != (self.order == "selected_first"):
                raise ValueError("archive comparison must retain the actually newer textual date")
        return self


class ArchiveVersionConflictEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    capture_id: Sha256
    index: Position
    compared_slots: tuple[Position, Position]

    @model_validator(mode="after")
    def exact_pair(self) -> Self:
        first, second = self.compared_slots
        if first >= second:
            raise ValueError("archive conflict requires two distinct slots in physical order")
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
    archive_members: ArchiveMemberDispositions | PackedArchiveMemberDispositions | None = Field(
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
    sejm_print_representation: SejmPrintRepresentation | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    sejm_adopted_text: SejmAdoptedText | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    sejm_committee_recommendation: SejmCommitteeRecommendation | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    required_part_gap: RequiredPartGap | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    amendment_part_gap: AmendmentPartGap | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    supporting_exclusions: SupportingPartExclusions | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    admitted_support: AdmittedSupport | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    composed_support: ComposedSupport | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    admitted_normative_annexes: AdmittedNormativeAnnexes | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    version_decision: VersionDecisionEvidence | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    archive_version_decision: ArchiveVersionDecisionEvidence | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    archive_version_conflict: ArchiveVersionConflictEvidence | None = Field(
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
        waiting = self.result == "waiting"
        if waiting != (self.reason == "version_input_incomplete") or (
            waiting and self.selection_state != "waiting"
        ):
            raise ValueError(
                "an incomplete version comparison requires a waiting result and selection"
            )
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
            if waiting and (
                fallback
                or selected
                or not any(
                    item.reason == "pdf_page_extraction_failed" for item in self.dispositions
                )
                or any(item.outcome == "policy_excluded" for item in self.dispositions)
            ):
                raise ValueError("a waiting comparison requires its failed read without a winner")
            if not waiting and any(
                item.reason in ("pdf_page_extraction_failed", "version_comparison_deferred")
                for item in self.dispositions
            ):
                raise ValueError("deferred comparison rows require a waiting comparison")
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
        if self.required_part_gap is not None:
            validate_required_part_gap_parent(self, self.required_part_gap)
        if self.amendment_part_gap is not None:
            validate_amendment_part_gap_parent(self, self.amendment_part_gap)
        if self.sejm_original_proposal is not None:
            validate_sejm_original_proposal(self, self.sejm_original_proposal)
        if self.sejm_print_representation is not None:
            validate_sejm_print_representation(self, self.sejm_print_representation)
        if self.sejm_adopted_text is not None:
            validate_sejm_adopted_text(self, self.sejm_adopted_text)
        if self.sejm_committee_recommendation is not None:
            validate_sejm_committee_recommendation(self, self.sejm_committee_recommendation)
        if self.supporting_exclusions is not None:
            validate_supporting_exclusions(self, self.supporting_exclusions)
        if self.admitted_support is not None:
            validate_admitted_support(self, self.admitted_support)
        if self.composed_support is not None:
            validate_composed_support(self, self.composed_support)
        if self.admitted_normative_annexes is not None:
            validate_admitted_normative_annexes(self, self.admitted_normative_annexes)
        if self.version_decision is not None:
            validate_version_decision(self, self.version_decision)
        if self.archive_version_decision is not None:
            archive_version_main(self, self.archive_version_decision)
        if self.archive_version_conflict is not None:
            archive_conflict_attempt(self, self.archive_version_conflict)
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
            and self.sejm_print_representation is None
            and self.sejm_adopted_text is None
            and self.sejm_committee_recommendation is None
            and self.supporting_exclusions is None
            and self.admitted_support is None
            and self.composed_support is None
            and self.admitted_normative_annexes is None
            and self.version_decision is None
            and self.archive_version_decision is None
            and self.archive_version_conflict is None
        )


def validate_version_decision(
    trace: CandidateDispositionTrace, evidence: VersionDecisionEvidence
) -> None:
    conflict = evidence.outcome == "distinct_policy_identity"
    if (
        trace.coverage != "complete"
        or (trace.result, trace.reason)
        != (("ambiguous", "version_conflict") if conflict else ("selected", "version_choice"))
        or any(row.phase != "version_comparison" for row in trace.dispositions)
    ):
        raise ValueError("version evidence requires the completed comparison phase")
    compared = tuple(
        row
        for row in trace.dispositions
        if row.outcome in ("policy_excluded", "selected")
        or row.reason in ("version_choice", "version_preference", "version_conflict")
    )
    if tuple(row.index for row in compared) != evidence.compared_indices:
        raise ValueError("version evidence differs from compared candidate dispositions")
    chosen = None if conflict else evidence.retained_indices[0]
    for row in compared:
        if row.index == chosen:
            expected = ("selected", "version_choice")
        else:
            expected = ("policy_excluded", "version_conflict" if conflict else "version_preference")
        if (row.outcome, row.reason) != expected:
            raise ValueError("version evidence differs from the selected or rejected disposition")


def _parent_disposition(
    trace: CandidateDispositionTrace, phase: str, index: int
) -> CandidateDisposition | None:
    return next(
        (item for item in trace.dispositions if (item.phase, item.index) == (phase, index)),
        None,
    )


def validate_supporting_exclusions(
    trace: CandidateDispositionTrace, exclusions: SupportingPartExclusions
) -> None:
    request = trace.requested_document
    first = exclusions.rows[0]
    parent = _parent_disposition(trace, first.phase, first.index)
    reason = (
        "version_choice" if first.phase == "version_comparison" else "first_acceptable_candidate"
    )
    if (
        request is None
        or first.index >= trace.candidate_count
        or parent is None
        or parent.outcome != "selected"
        or parent.reason != reason
        or trace.result != "selected"
        or trace.reason != reason
        or any((trace.required_part_gap, trace.normative_annex_gap, trace.amendment_part_gap))
    ):
        raise ValueError("Supporting exclusions require the selected outer candidate")
    extras = (request, *request.alternatives)[first.index].extra_urls
    if exclusions.omitted > len(extras) - exclusions.rows[-1].extra_index - 1 or any(
        row.extra_index >= len(extras)
        or not metadata_url_decision(extras[row.extra_index]).admitted
        for row in exclusions.rows
    ):
        raise ValueError("Supporting exclusion exceeds its admitted extra URL namespace")


def _selected_admission_candidate(
    trace: CandidateDispositionTrace, phase: str, index: int, label: str
) -> TextDocument:
    request = trace.requested_document
    parent = _parent_disposition(trace, phase, index)
    reason = "version_choice" if phase == "version_comparison" else "first_acceptable_candidate"
    if (
        request is None
        or index >= trace.candidate_count
        or parent is None
        or parent.outcome != "selected"
        or parent.reason != reason
        or trace.result != "selected"
        or trace.reason != reason
        or any((trace.required_part_gap, trace.normative_annex_gap, trace.amendment_part_gap))
    ):
        raise ValueError(f"{label} requires the selected outer candidate")
    return (request, *request.alternatives)[index]


def validate_admitted_support(trace: CandidateDispositionTrace, evidence: AdmittedSupport) -> None:
    extras = _selected_admission_candidate(
        trace, evidence.phase, evidence.index, "Admitted support"
    ).extra_urls
    exclusions = trace.supporting_exclusions
    excluded = {row.extra_index for row in exclusions.rows} if exclusions is not None else set()
    if evidence.omitted > len(extras) - evidence.rows[-1].extra_index - 1 or any(
        row.extra_index >= len(extras)
        or row.extra_index in excluded
        or not metadata_url_decision(extras[row.extra_index]).admitted
        for row in evidence.rows
    ):
        raise ValueError("Admitted support exceeds its available extra URL namespace")


def validate_composed_support(trace: CandidateDispositionTrace, evidence: ComposedSupport) -> None:
    candidate = _selected_admission_candidate(
        trace, evidence.phase, evidence.index, "Composed support"
    )
    extras = candidate.extra_urls
    excluded = trace.supporting_exclusions.rows if trace.supporting_exclusions is not None else ()
    admitted = trace.admitted_support.rows if trace.admitted_support is not None else ()
    known = {row.extra_index for row in excluded} | {row.extra_index for row in admitted}
    other_urls = {
        candidate.url,
        *candidate.required_urls,
        *candidate.amendment_urls,
        *candidate.normative_urls,
    }
    for row in evidence.rows:
        if row.extra_index >= len(extras) or row.extra_index in known:
            raise ValueError("Composed support exceeds its available extra URL namespace")
        url = extras[row.extra_index]
        if extras.count(url) != 1 or url in other_urls or not metadata_url_decision(url).admitted:
            raise ValueError("Composed support requires an unambiguous extra URL")


def validate_admitted_normative_annexes(
    trace: CandidateDispositionTrace, evidence: AdmittedNormativeAnnexes
) -> None:
    candidate = _selected_admission_candidate(
        trace, evidence.phase, evidence.index, "Admitted normative annex evidence"
    )
    if candidate.kind != "rcl" or trace.selection_state not in ("selected", "unverified"):
        raise ValueError("Admitted normative annexes require a composed RCL candidate")
    urls = candidate.normative_urls
    if evidence.omitted > len(urls) - evidence.rows[-1].normative_index - 1 or any(
        row.normative_index >= len(urls)
        or urls.count(urls[row.normative_index]) != 1
        or not metadata_url_decision(urls[row.normative_index]).admitted
        for row in evidence.rows
    ):
        raise ValueError("Admitted normative annex exceeds its exact URL namespace")


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
        or trace.required_part_gap is not None
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


def validate_sejm_print_representation(
    trace: CandidateDispositionTrace, proof: SejmPrintRepresentation
) -> None:
    request = trace.requested_document
    if (
        request is None
        or request.kind != "print"
        or request.url != proof.pdf_url
        or request.version_basis != (f"sejm/{proof.term}/print/{proof.number}",)
        or not request.compare_versions
        or request.senate is not None
        or request.official_link_conflict
        or request.alternatives
        or request.required_urls
        or request.extra_urls
        or request.normative_urls
        or request.amendment_urls
        or trace.candidate_count != 1
        or trace.result != "selected"
        or trace.reason != "version_choice"
        or trace.selection_state not in ("selected", "unverified")
        or trace.archive_members is not None
        or trace.required_exclusions is not None
        or trace.normative_annex_gap is not None
        or trace.required_part_gap is not None
        or trace.sejm_original_proposal is not None
    ):
        raise ValueError("Sejm representation requires the selected original singleton print")
    disposition = _parent_disposition(trace, "version_comparison", 0)
    if (
        disposition is None
        or disposition.outcome != "selected"
        or disposition.reason != "version_choice"
        or disposition.observed_sha256 != proof.pdf_sha256
    ):
        raise ValueError("Sejm representation requires the observed PDF identity")


def validate_sejm_adopted_text(trace: CandidateDispositionTrace, proof: SejmAdoptedText) -> None:
    if (
        trace.requested_document != TextDocument(url=proof.pdf_url, kind="text_after3")
        or trace.candidate_count != 1
        or trace.result != "selected"
        or trace.reason != "first_acceptable_candidate"
        or trace.selection_state not in ("selected", "unverified")
        or trace.archive_members is not None
        or trace.required_exclusions is not None
        or trace.normative_annex_gap is not None
        or trace.required_part_gap is not None
        or trace.sejm_original_proposal is not None
        or trace.sejm_print_representation is not None
    ):
        raise ValueError("Adopted text requires its selected same-owner attachment")
    disposition = _parent_disposition(trace, "fallback", 0)
    if (
        disposition is None
        or disposition.outcome != "selected"
        or disposition.reason != "first_acceptable_candidate"
        or disposition.observed_sha256 != proof.pdf_sha256
    ):
        raise ValueError("Adopted text requires the observed PDF identity")


def validate_sejm_committee_recommendation(
    trace: CandidateDispositionTrace, proof: SejmCommitteeRecommendation
) -> None:
    disposition = _parent_disposition(trace, "fallback", 0)
    if (
        not trace.model_copy(update={"sejm_committee_recommendation": None}).is_routine_singleton
        or trace.requested_document
        != TextDocument(url=proof.report_url, kind="committee_amendments")
        or trace.reason != "first_acceptable_candidate"
        or trace.archive_members is not None
        or trace.normative_annex_gap is not None
        or trace.required_part_gap is not None
        or disposition is None
        or disposition.reason != "first_acceptable_candidate"
        or disposition.observed_sha256 != proof.report_sha256
    ):
        raise ValueError("Recommendation requires its exact selected committee report")


def _terminal_wait_candidate(
    trace: CandidateDispositionTrace, gap: RequiredPartGap | AmendmentPartGap
) -> TextDocument:
    request = trace.requested_document
    if request is None or gap.index >= trace.candidate_count:
        raise ValueError("a terminal part gap requires its retained outer request")
    parent = _parent_disposition(trace, gap.phase, gap.index)
    expected_reason = (
        "version_choice" if gap.phase == "version_comparison" else "first_acceptable_candidate"
    )
    if (
        parent is None
        or parent.outcome != "selected"
        or trace.result != "selected"
        or parent.reason != expected_reason
        or trace.reason != expected_reason
    ):
        raise ValueError("a terminal part gap requires the selected outer disposition")
    if (
        trace.selection_state != "waiting"
        or trace.normative_annex_gap is not None
        or (trace.required_part_gap is not None and trace.amendment_part_gap is not None)
    ):
        raise ValueError("a terminal part gap requires its own terminal waiting state")
    return (request, *request.alternatives)[gap.index]


def validate_amendment_part_gap_parent(
    trace: CandidateDispositionTrace, gap: AmendmentPartGap
) -> None:
    candidate = _terminal_wait_candidate(trace, gap)
    if gap.amendment_index >= len(candidate.amendment_urls):
        raise ValueError("amendment gap exceeds the actual amendment URL namespace")
    if not metadata_url_decision(candidate.amendment_urls[gap.amendment_index]).admitted:
        raise ValueError("amendment gap parent URL is outside metadata admission")


def validate_required_part_gap_parent(
    trace: CandidateDispositionTrace, gap: RequiredPartGap
) -> None:
    candidate = _terminal_wait_candidate(trace, gap)
    if gap.required_index >= len(candidate.required_urls):
        raise ValueError("required part gap exceeds the actual required URL namespace")
    if not metadata_url_decision(candidate.required_urls[gap.required_index]).admitted:
        raise ValueError("required part gap parent URL is outside metadata admission")
    exclusions = trace.required_exclusions
    if exclusions is not None and any(
        (row.phase, row.index) == (gap.phase, gap.index)
        and row.required_index >= gap.required_index
        for row in exclusions.rows
    ):
        raise ValueError("required exclusions cannot occur after a terminal required part gap")


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


def archive_version_main(
    trace: CandidateDispositionTrace, evidence: ArchiveVersionDecisionEvidence
) -> ArchiveMemberDisposition:
    peer = evidence.peer
    members = trace.archive_members
    parent = _parent_disposition(trace, peer.phase, peer.index)
    if (
        trace.coverage != "complete"
        or trace.result != "selected"
        or trace.version_decision is not None
        or members is None
        or parent is None
        or parent.outcome != "selected"
        or parent.observed_sha256 != peer.root_sha256
    ):
        raise ValueError("archive comparison requires an exact selected root disposition")
    validate_archive_member_parent(trace, peer)
    root_rows = tuple(
        row
        for row in members.rows
        if (row.phase, row.index, row.root_sha256) == (peer.phase, peer.index, peer.root_sha256)
        and row.stage == "policy"
    )
    mains = tuple(row for row in root_rows if row.reason == "selected_main")
    if len(mains) != 1:
        raise ValueError("archive comparison requires one retained selected main")
    (main,) = mains
    if (
        main.physical_part_index != evidence.selected_slot
        or main.association != "known"
        or main.ordinals == peer.ordinals
        or main.leaf_sha256 is None
        or main.text_sha256 is None
        or (members.retention == "complete" and peer not in root_rows)
        or any(
            row != peer
            for row in root_rows
            if row.reason == "version_preference"
            or row.physical_part_index == peer.physical_part_index
            or row.ordinals == peer.ordinals
        )
    ):
        raise ValueError("archive comparison differs from its retained member policy")
    return main


def archive_conflict_attempt(
    trace: CandidateDispositionTrace, evidence: ArchiveVersionConflictEvidence
) -> CandidateDisposition:
    parent = _parent_disposition(trace, "version_comparison", evidence.index)
    if (
        trace.coverage != "complete"
        or trace.archive_version_decision is not None
        or parent is None
        or (parent.outcome, parent.reason) != ("unclassified", "ambiguous_extraction")
        or parent.observed_sha256 is None
        or trace.archive_members is None
    ):
        raise ValueError("archive conflict requires the exact ambiguous extraction attempt")
    rows = tuple(
        row
        for row in trace.archive_members.rows
        if (row.phase, row.index, row.stage) == ("version_comparison", evidence.index, "policy")
    )
    if any(
        row.reason == "selected_main"
        or (
            row.physical_part_index in evidence.compared_slots
            and (row.reason != "version_conflict" or row.association != "known")
        )
        for row in rows
    ) or (
        trace.archive_members.retention == "complete"
        and not set(evidence.compared_slots).issubset(row.physical_part_index for row in rows)
    ):
        raise ValueError("archive conflict differs from the retained member policy")
    return parent
