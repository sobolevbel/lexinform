"""Editorial versions outlive operational delivery plans and reuse exact semantic snapshots."""

import hashlib
import json
import re
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, PositiveInt, model_validator

from lexinform.bill_context import continuation_snapshot
from lexinform.json_storage import read_json
from lexinform.models.bill import Bill
from lexinform.models.enums import PublicationStatus
from lexinform.models.grouped_update_witness import GroupedRenderWitness


class ContentSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["bill", "change", "plan"]
    payload_json: str

    @model_validator(mode="after")
    def json_object(self) -> Self:
        payload = json.loads(self.payload_json)
        if not isinstance(payload, dict):
            raise ValueError("a content snapshot requires a JSON object")
        if self.kind == "plan" and any(
            payload.get(field) for field in ("bill_json", "change_json", "rendered_text")
        ):
            raise ValueError("semantic plan snapshots reference their bodies separately")
        return self

    @property
    def snapshot_id(self) -> str:
        encoded = self.kind + "\n" + self.payload_json
        return "content-snapshot-v1:" + hashlib.sha256(encoded.encode()).hexdigest()


def editorial_bill_snapshot(raw: str) -> ContentSnapshot:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("a content snapshot requires a JSON object")
    if payload.get("review_work") or payload.get("ready_analysis"):
        payload.pop("review_work", None)
        payload.pop("ready_analysis", None)
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return ContentSnapshot(kind="bill", payload_json=raw)


def card_bill_snapshot(raw: str) -> ContentSnapshot:
    return editorial_bill_snapshot(continuation_snapshot(Bill.model_validate(read_json(raw))))


class CardPrimaryEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: Literal["card-primary-evidence-v1"] = "card-primary-evidence-v1"
    projection_application_id: int = Field(gt=0, lt=2**63, strict=True)
    source_observation_id: str = Field(pattern=r"^source-observation-v1:[0-9a-f]{64}$")


class SupplementContentEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_observation_id: str = Field(pattern=r"^source-observation-v1:[0-9a-f]{64}$")


MAX_DIRECT_STAGE_CONTENT_BYTES = 8_192


class DirectStageContentEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: Literal["direct-stage-content-v1"] = "direct-stage-content-v1"
    anchor_publication_id: int = Field(gt=0, lt=2**63, strict=True)
    original_bill_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


MAX_GROUPED_STAGE_CONTENT_BYTES = 16_384
type GroupSqlId = Annotated[int, Field(gt=0, lt=2**63, strict=True)]
type SnapshotId = Annotated[str, Field(pattern=r"^content-snapshot-v1:[0-9a-f]{64}$")]
type Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class GroupedStageMember(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    publication_id: GroupSqlId
    anchor_publication_id: GroupSqlId
    anchor_message_id: GroupSqlId
    status_change_id: GroupSqlId
    bill_snapshot_id: SnapshotId
    change_snapshot_id: SnapshotId
    original_bill_sha256: Sha256


class GroupedStageRevisionEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    member_publication_id: GroupSqlId
    target_message_id: GroupSqlId


class GroupedStageContentEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: Literal["grouped-stage-content-v1"] = "grouped-stage-content-v1"
    members: tuple[GroupedStageMember, GroupedStageMember]
    render_witness: GroupedRenderWitness | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    revision: GroupedStageRevisionEvidence | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


type ContentReason = Literal[
    "original", "amendments_revision", "card_revision", "grouped_stage_revision"
]


class ContentVersion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    root_publication_id: int = Field(gt=0)
    previous_version_id: str | None = Field(default=None, pattern=r"^content-v1:[0-9a-f]{64}$")
    reason: ContentReason
    language: str | None = Field(default=None, min_length=1)
    bill_snapshot_id: str | None = Field(
        default=None, pattern=r"^content-snapshot-v1:[0-9a-f]{64}$"
    )
    change_snapshot_id: str | None = Field(
        default=None,
        pattern=r"^content-snapshot-v1:[0-9a-f]{64}$",
        exclude_if=lambda value: value is None,
    )
    plan_snapshot_id: str = Field(pattern=r"^content-snapshot-v1:[0-9a-f]{64}$")
    rendered_text: str | None = Field(default=None, min_length=1)
    formatter_version: str | None = Field(default=None, min_length=1)
    source_observation_ids: tuple[str, ...] = ()
    card_context_profile: Literal["continuation-v1"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    card_primary_evidence: CardPrimaryEvidence | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    direct_stage_evidence: DirectStageContentEvidence | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    grouped_stage_evidence: GroupedStageContentEvidence | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    digest_profile: Literal["digest-content-v1", "digest-rendered-v1"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    source_scope: Literal["frozen_primary_context/analysis"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    computation_ids: tuple[str, ...] = ()
    application_ref_profile: (
        Literal["amendment-projections-v1", "supplement-projection-v1"] | None
    ) = Field(default=None, exclude_if=lambda value: value is None)
    supplement_evidence: SupplementContentEvidence | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    application_ids: tuple[PositiveInt, ...] = ()
    status_change_id: PositiveInt | None = None
    recorded_at: AwareDatetime

    @model_validator(mode="before")
    @classmethod
    def profiled_integer_ids(cls, value: object) -> object:
        if isinstance(value, dict) and value.get("application_ref_profile") is not None:
            ids = value.get("application_ids")
            if not isinstance(ids, (tuple, list)) or any(type(item) is not int for item in ids):
                raise ValueError("profiled references require actual integer IDs")
        return value

    @model_validator(mode="after")
    def predecessor(self) -> Self:
        if self.digest_profile is not None:
            if (
                self.reason != "original"
                or self.previous_version_id is not None
                or self.bill_snapshot_id is not None
                or self.change_snapshot_id is not None
                or self.rendered_text is not None
                or self.language not in ("ru", "en")
                or not self.formatter_version
                or len(self.formatter_version) > 128
                or self.status_change_id is not None
                or self.card_context_profile is not None
                or self.card_primary_evidence is not None
                or self.direct_stage_evidence is not None
                or self.grouped_stage_evidence is not None
                or self.source_scope is not None
                or self.source_observation_ids
                or self.computation_ids
                or self.application_ref_profile is not None
                or self.application_ids
                or self.supplement_evidence is not None
            ):
                raise ValueError("digest content requires its own bounded original profile")
            return self
        if self.bill_snapshot_id is None:
            raise ValueError("non-digest content requires a frozen Bill")
        if (self.reason == "original") != (self.previous_version_id is None):
            raise ValueError("an original has no predecessor; a revision requires one")
        if self.change_snapshot_id is None and (
            self.card_context_profile != "continuation-v1"
            or self.reason == "amendments_revision"
            or self.status_change_id is not None
            or self.source_scope is not None
            or not self.rendered_text
            or self.language is None
            or self.formatter_version is None
        ):
            raise ValueError("Card content requires a frozen render without a change event")
        if self.card_primary_evidence is not None and (
            self.card_context_profile != "continuation-v1"
            or self.change_snapshot_id is not None
            or self.reason != "original"
        ):
            raise ValueError("Primary card evidence requires an original compact card")
        if self.card_context_profile is not None and self.change_snapshot_id is not None:
            raise ValueError("Compact card context cannot describe an amendment")
        if self.reason == "card_revision" and self.change_snapshot_id is not None:
            raise ValueError("Card revisions cannot borrow a change event")
        if self.source_scope is not None and (
            len(self.source_observation_ids) > 1
            or any(
                re.fullmatch(r"source-observation-v1:[0-9a-f]{64}", value) is None
                for value in self.source_observation_ids
            )
        ):
            raise ValueError("a scoped primary context has at most one canonical source ID")
        if self.application_ref_profile is not None and (
            self.change_snapshot_id is None
            or self.reason == "card_revision"
            or not 1 <= len(self.application_ids) <= 32
            or tuple(sorted(set(self.application_ids))) != self.application_ids
            or any(value >= 2**63 for value in self.application_ids)
        ):
            raise ValueError("profiled amendment references require bounded canonical SQL IDs")
        supplement_profile = self.application_ref_profile == "supplement-projection-v1"
        if supplement_profile != (self.supplement_evidence is not None) or (
            supplement_profile
            and (
                self.reason != "original"
                or len(self.application_ids) != 1
                or not self.rendered_text
                or self.language is None
                or self.formatter_version is None
                or self.source_scope is not None
            )
        ):
            raise ValueError("Supplement evidence requires one original editorial projection")
        if self.direct_stage_evidence is not None and (
            self.reason != "original"
            or self.change_snapshot_id is None
            or self.status_change_id is None
            or self.language not in ("ru", "en")
            or not self.formatter_version
            or not self.rendered_text
            or len(self.rendered_text.encode()) > MAX_DIRECT_STAGE_CONTENT_BYTES
            or self.source_scope is not None
            or self.source_observation_ids
            or self.computation_ids
            or self.application_ref_profile is not None
            or self.application_ids
        ):
            raise ValueError(
                "Direct stage evidence requires a bounded original without result claims"
            )
        group = self.grouped_stage_evidence
        if (self.reason == "grouped_stage_revision") != (
            group is not None and group.revision is not None
        ):
            raise ValueError("Grouped stage revisions require explicit physical revision evidence")
        if group is not None:
            first, second = group.members
            if (
                self.reason not in ("original", "grouped_stage_revision")
                or self.language not in ("ru", "en")
                or not self.formatter_version
                or len(self.formatter_version) > 128
                or not self.rendered_text
                or len(self.rendered_text.encode()) > MAX_GROUPED_STAGE_CONTENT_BYTES
                or self.card_context_profile is not None
                or self.direct_stage_evidence is not None
                or self.source_scope is not None
                or self.source_observation_ids
                or self.computation_ids
                or self.application_ref_profile is not None
                or self.application_ids
                or (
                    first.publication_id,
                    first.status_change_id,
                    first.bill_snapshot_id,
                    first.change_snapshot_id,
                )
                != (
                    self.root_publication_id,
                    self.status_change_id,
                    self.bill_snapshot_id,
                    self.change_snapshot_id,
                )
                or first.publication_id == second.publication_id
                or first.anchor_publication_id == second.anchor_publication_id
                or first.status_change_id == second.status_change_id
            ):
                raise ValueError("Grouped stage history requires two distinct ordered originals")
            if group.revision is not None and (
                group.render_witness is None
                or group.revision.member_publication_id
                not in (first.publication_id, second.publication_id)
            ):
                raise ValueError("Grouped stage revisions require a witnessed member of the group")
            witness = group.render_witness
            if witness is not None:
                if tuple(
                    (owner.card_message_id, owner.status_change_id) for owner in witness.members
                ) != tuple(
                    (member.anchor_message_id, member.status_change_id) for member in group.members
                ):
                    raise ValueError(
                        "Grouped render witness does not match ordered card and event identities"
                    )
                witness.bind(self.rendered_text, self.language)

        return self

    @property
    def bill_context_id(self) -> str:
        if self.bill_snapshot_id is None:
            raise ValueError("digest content has no Bill snapshot")
        return self.bill_snapshot_id

    @property
    def content_id(self) -> str:
        payload = self.model_dump(mode="json", exclude={"recorded_at"})
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return "content-v1:" + hashlib.sha256(encoded.encode()).hexdigest()


class ContentVersionSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    content_id: str
    root_publication_id: int
    previous_version_id: str | None
    reason: ContentReason
    language: str | None
    bill_snapshot_id: str | None
    change_snapshot_id: str | None
    plan_snapshot_id: str
    formatter_version: str | None
    source_observation_ids: tuple[str, ...]
    card_context_profile: Literal["continuation-v1"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    direct_stage_evidence: DirectStageContentEvidence | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    grouped_stage_evidence: GroupedStageContentEvidence | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    digest_profile: Literal["digest-content-v1", "digest-rendered-v1"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    digest_exact_parts_available: Literal[True] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    digest_upstream_semantics: Literal["not_retained"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    source_scope: Literal["frozen_primary_context/analysis"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    computation_ids: tuple[str, ...]
    application_ref_profile: (
        Literal["amendment-projections-v1", "supplement-projection-v1"] | None
    ) = Field(default=None, exclude_if=lambda value: value is None)
    application_ids: tuple[int, ...]
    status_change_id: int | None
    recorded_at: AwareDatetime
    rendered_text_known: bool
    rendered_utf8_bytes: int | None
    rendered_sha256: str | None
    source_observation_ids_opaque_count: int = 0
    computation_ids_opaque_count: int = 0
    unrecognized_labels: tuple[str, ...] = ()

    @property
    def known_application_refs(self) -> tuple[str, ...]:
        return (
            tuple(f"result-application-v1:{value}" for value in self.application_ids)
            if self.application_ref_profile is not None
            else ()
        )

    @property
    def full_context_available(self) -> bool | None:
        return (
            False
            if self.card_context_profile is not None
            or self.grouped_stage_evidence is not None
            or self.digest_upstream_semantics == "not_retained"
            else None
        )

    @classmethod
    def from_version(cls, version: ContentVersion) -> Self:
        source_ids = tuple(
            value
            for value in version.source_observation_ids
            if re.fullmatch(r"source-observation-v1:[0-9a-f]{64}", value)
        )
        result_ids = tuple(
            value
            for value in version.computation_ids
            if re.fullmatch(r"result-v1:[0-9a-f]{64}", value)
        )
        unknown = tuple(
            name
            for name, value in (
                ("language", version.language),
                ("formatter_version", version.formatter_version),
            )
            if value is not None and not re.fullmatch(r"[A-Za-z0-9_.:+-]{1,128}", value)
        )
        text = version.rendered_text.encode() if version.rendered_text is not None else None
        return cls(
            content_id=version.content_id,
            root_publication_id=version.root_publication_id,
            previous_version_id=version.previous_version_id,
            reason=version.reason,
            language=None if "language" in unknown else version.language,
            bill_snapshot_id=version.bill_snapshot_id,
            change_snapshot_id=version.change_snapshot_id,
            plan_snapshot_id=version.plan_snapshot_id,
            formatter_version=None if "formatter_version" in unknown else version.formatter_version,
            source_observation_ids=source_ids,
            source_scope=version.source_scope,
            card_context_profile=version.card_context_profile,
            digest_profile=version.digest_profile,
            digest_exact_parts_available=True
            if version.digest_profile == "digest-rendered-v1"
            else None,
            digest_upstream_semantics="not_retained"
            if version.digest_profile == "digest-rendered-v1"
            else None,
            grouped_stage_evidence=version.grouped_stage_evidence,
            computation_ids=result_ids,
            application_ids=version.application_ids,
            application_ref_profile=version.application_ref_profile,
            status_change_id=version.status_change_id,
            recorded_at=version.recorded_at,
            rendered_text_known=text is not None,
            rendered_utf8_bytes=len(text) if text is not None else None,
            rendered_sha256=hashlib.sha256(text).hexdigest() if text is not None else None,
            source_observation_ids_opaque_count=len(version.source_observation_ids)
            - len(source_ids),
            computation_ids_opaque_count=len(version.computation_ids) - len(result_ids),
            unrecognized_labels=unknown,
        )


class ContentHistoryView(BaseModel):
    model_config = ConfigDict(frozen=True)

    root_publication_id: int | None = None
    version_count: int | None = None
    summaries: tuple[ContentVersionSummary, ...] = ()
    inspected_id: str | None = None
    original_id: str | None = None
    current_id: str | None = None
    integrity: Literal["valid", "unavailable"] = "valid"
    ambiguous: bool = False

    def _summary(self, identity: str | None) -> ContentVersionSummary | None:
        return next((summary for summary in self.summaries if summary.content_id == identity), None)

    @property
    def inspected(self) -> ContentVersionSummary | None:
        return self._summary(self.inspected_id)

    @property
    def original(self) -> ContentVersionSummary | None:
        return self._summary(self.original_id)

    @property
    def current(self) -> ContentVersionSummary | None:
        return self._summary(self.current_id)


class ContentDecisionSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: Literal["confirm", "retry", "dismiss"]
    resolved_at: AwareDatetime
    command_update_id: int


class ContentDeliverySummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    publication_id: int
    status: PublicationStatus
    message_id: int | None
    attempts: int
    decisions: tuple[ContentDecisionSummary, ...] = Field(default=(), max_length=3)
    omitted_decisions: int = 0


OriginalAmendmentInputStatus = Literal["available", "legacy_unavailable", "invalid"]


class ContentHistoryPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["available", "unavailable", "out_of_range"]
    page: int
    pages: int = 0
    history: ContentHistoryView | None = None
    version: ContentVersionSummary | None = None
    deliveries: tuple[ContentDeliverySummary, ...] = Field(default=(), max_length=3)
    omitted_deliveries: int = 0
    original_amendment_input: OriginalAmendmentInputStatus | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    original_primary_lineage: Literal[True] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
