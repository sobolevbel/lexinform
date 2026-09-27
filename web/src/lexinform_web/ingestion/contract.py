import datetime as dt
import re
from typing import Annotated, Final, Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lexinform.models.enums import BillStatus, Category
from lexinform_web.matters.identities import IdentitySource, SourceKey

SUPPORTED_SCHEMA: Final = 34
Nonempty = Annotated[str, Field(min_length=1)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PublicLink(ContractModel):
    url: str

    @field_validator("url")
    @classmethod
    def public_http_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or any(character.isspace() for character in value)
        ):
            raise ValueError("expected an HTTP(S) URL without credentials or whitespace")
        return value


class Identity(ContractModel):
    source: Literal["sejm", "rcl", "rpw", "wykaz"]
    scope: str
    external_id: str

    @model_validator(mode="after")
    def natural_key(self) -> Self:
        SourceKey(IdentitySource(self.source), self.scope, self.external_id)
        if self.source == "wykaz" and (
            self.scope != "gov.pl/premier"
            or not re.fullmatch(r"entry:[0-9a-f]{64}", self.external_id)
        ):
            raise ValueError("Wykaz requires a register entry evidence hash")
        return self


class SnapshotOrigin(ContractModel):
    source_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    dump_sha256: Sha256
    source_schema: int = Field(ge=1, le=SUPPORTED_SCHEMA)
    normalized_schema: Literal[34] = SUPPORTED_SCHEMA


class StageFact(ContractModel):
    name: Nonempty
    kind: Nonempty
    date: dt.date | None = None
    decision: str | None = None
    committee: str | None = None
    proposal: str | None = None
    children: tuple[StageFact, ...] = ()


class RclStageFact(ContractModel):
    number: int
    name: Nonempty
    state: Literal["not_started", "active", "reached"]
    started: dt.date | None
    ended: dt.date | None


class AgendaFact(ContractModel):
    kind: str
    date: dt.date
    end_date: dt.date | None
    start_time: dt.time | None
    committee: str | None
    text: str
    condition: str | None
    closed: bool
    apply_email: str | None
    apply_by: dt.date | None
    video: PublicLink | None


class ConsultationFact(ContractModel):
    source: Literal["sejm", "rcl"]
    start: dt.date | None = None
    end: dt.date | None = None
    links: tuple[PublicLink, ...] = ()
    email: str | None = None
    results_published: bool

    @model_validator(mode="after")
    def ordered_dates(self) -> Self:
        if self.start and self.end and self.start > self.end:
            raise ValueError("consultation closes before it opens")
        return self


class ActFact(ContractModel):
    eli: Nonempty
    title: Nonempty
    display_address: Nonempty
    promulgation_date: dt.date | None
    entry_into_force: dt.date | None
    links: tuple[PublicLink, ...] = ()


class WykazEvidence(ContractModel):
    number: Nonempty
    first_published_at: dt.datetime
    entry_link: PublicLink


class SourceFacts(ContractModel):
    title: Nonempty
    description: str | None
    source_link: PublicLink
    document_date: dt.date | None
    closure_date: dt.date | None
    passed: bool | None
    stages: tuple[StageFact, ...]
    rcl_stages: tuple[RclStageFact, ...] = ()
    agenda: tuple[AgendaFact, ...] = ()
    source_status: str | None = None
    consultation: ConsultationFact | None = None
    act: ActFact | None = None
    display_aliases: tuple[str, ...] = ()
    wykaz_evidence: WykazEvidence | None = None


class ProcessBaseline(ContractModel):
    stages: tuple[StageFact, ...]
    closure_date: dt.date | None
    passed: bool | None
    analysis_revision: int = Field(ge=0)
    analysis_sha256: Sha256 | None


class AppliedAnalysis(ContractModel):
    relevant: bool
    score: int = Field(ge=1, le=5)
    category: Category
    summary: Nonempty
    rationale: Nonempty
    key_changes: tuple[str, ...]
    affected_groups: tuple[str, ...]
    practical_impact: str
    effective_date: str | None
    changes_since_previous: tuple[str, ...]
    created_at: dt.datetime
    revision: int = Field(ge=1)
    source_link: PublicLink | None
    text_sha256: Sha256 | None
    text_checked_at: dt.datetime | None

    @model_validator(mode="after")
    def useful_explanation(self) -> Self:
        if not self.summary.strip() or not self.rationale.strip():
            raise ValueError("applied analysis requires summary and rationale")
        if self.relevant and not self.practical_impact.strip():
            raise ValueError("relevant analysis requires practical impact")
        return self


class PendingWork(ContractModel):
    work_key: Nonempty
    identity: Identity
    kind: Literal["analysis", "reanalysis", "joint", "supplement", "amendments"]
    state: Literal["queued", "unknown", "submitted", "result_staged"]
    since: dt.datetime | None


class ImportedBill(ContractModel):
    identity: Identity
    bot_status: BillStatus
    facts: SourceFacts
    processed: ProcessBaseline | None
    observed_closure_date: dt.date | None
    applied_analysis: AppliedAnalysis | None
    awaiting_batch_since: dt.datetime | None
    has_staged_analysis: bool
    visibility: Literal["editorial"] = "editorial"
    aspect_freshness: Literal["unknown"] = "unknown"


class EventExplanation(ContractModel):
    kind: Literal["amendments", "supplement"]
    source_link: PublicLink
    title: str | None = None
    summary: str | None
    points: tuple[str, ...] = ()
    created_at: dt.datetime | None


class IdentityRelation(ContractModel):
    source: Identity
    target: Identity
    kind: Literal["lifecycle", "joint", "alternative", "continuation"]

    @model_validator(mode="after")
    def different_identities(self) -> Self:
        if self.source == self.target:
            raise ValueError("self relation")
        return self


class ImportedEvent(ContractModel):
    event_key: Nonempty
    identity: Identity
    provenance: Literal["status_changes"] = "status_changes"
    observed_at: dt.datetime
    occurred_at: dt.datetime | None = None
    stages: tuple[StageFact, ...]
    closure_detected: bool
    passed: bool | None
    content_changed: bool
    withdrawn: bool
    discontinued: bool
    consultation_opened: bool
    explanations: tuple[EventExplanation, ...] = ()


class TelegramReference(ContractModel):
    identity: Identity
    link: PublicLink

    @model_validator(mode="after")
    def public_message(self) -> Self:
        if not re.fullmatch(r"https://t\.me/[A-Za-z][A-Za-z0-9_]{3,31}/[1-9]\d*", self.link.url):
            raise ValueError("expected a public Telegram message URL")
        return self


class Coverage(ContractModel):
    total: int = Field(ge=0)
    with_applied_analysis: int = Field(ge=0)
    analyzed_candidates: int = Field(ge=0)


class ImportDocumentV1(ContractModel):
    contract_version: Literal[1] = 1
    origin: SnapshotOrigin
    bills: tuple[ImportedBill, ...]
    relations: tuple[IdentityRelation, ...] = ()
    pending_work: tuple[PendingWork, ...] = ()
    events: tuple[ImportedEvent, ...] = ()
    telegram: tuple[TelegramReference, ...] = ()
    coverage: Coverage

    @model_validator(mode="after")
    def complete_graph(self) -> Self:
        identities = {bill.identity for bill in self.bills}
        if len(identities) != len(self.bills):
            raise ValueError("duplicate source identity; reconciliation required")
        references = {r.source for r in self.relations} | {r.target for r in self.relations}
        references.update(item.identity for item in self.pending_work)
        references.update(event.identity for event in self.events)
        references.update(item.identity for item in self.telegram)
        if not references <= identities:
            raise ValueError("dangling identity reference")
        for entries in (self.relations, self.telegram):
            if len(set(entries)) != len(entries):
                raise ValueError("duplicate import entry")
        if len({item.work_key for item in self.pending_work}) != len(self.pending_work):
            raise ValueError("duplicate pending work key")
        if len({event.event_key for event in self.events}) != len(self.events):
            raise ValueError("duplicate event key")
        lifecycle = {r.source: r.target for r in self.relations if r.kind == "lifecycle"}
        if len(lifecycle) != sum(r.kind == "lifecycle" for r in self.relations):
            raise ValueError("conflicting lifecycle targets")
        for start in lifecycle:
            seen: set[Identity] = set()
            node = start
            while node in lifecycle:
                if node in seen:
                    raise ValueError("lifecycle cycle")
                seen.add(node)
                node = lifecycle[node]
        expected = Coverage(
            total=len(self.bills),
            with_applied_analysis=sum(b.applied_analysis is not None for b in self.bills),
            analyzed_candidates=sum(
                b.bot_status == BillStatus.ANALYZED and b.applied_analysis is not None
                for b in self.bills
            ),
        )
        if self.coverage != expected:
            raise ValueError("coverage does not match the complete document")
        return self
