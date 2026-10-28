"""Senate inspection facts retain candidate reasoning separately from admission and application."""

import datetime as dt
import hashlib
import json
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_SENATE_INSPECTION_BYTES = 8192
MAX_SENATE_OMISSION_BYTES = 2048
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
SqlInteger = Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]
Term = Annotated[int, Field(strict=True, ge=1, le=2**63 - 1)]
_MAX_TIME = dt.datetime.max.replace(tzinfo=dt.UTC)


def senate_canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def senate_sha256(value: object) -> str:
    return hashlib.sha256(senate_canonical(value)).hexdigest()


class _Fact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")


class SenateInspectionUrl(_Fact):
    value: str | None
    state: Literal["complete", "omitted_unsafe"]
    sha256: Sha256

    @staticmethod
    def safe(value: str) -> bool:
        try:
            parsed = urlsplit(value)
            return (
                not any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
                and parsed.scheme == "https"
                and parsed.hostname == "api.sejm.gov.pl"
                and parsed.port in (None, 443)
                and not parsed.username
                and not parsed.password
                and not parsed.query
                and not parsed.fragment
            )
        except ValueError:
            return False

    @classmethod
    def of(cls, value: str) -> Self:
        safe = cls.safe(value)
        return cls(
            value=value if safe else None,
            state="complete" if safe else "omitted_unsafe",
            sha256=hashlib.sha256(value.encode()).hexdigest(),
        )

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.value is None:
            if self.state != "omitted_unsafe":
                raise ValueError("absent Senate URLs require explicit omission")
        elif (
            self.state != "complete"
            or not self.safe(self.value)
            or hashlib.sha256(self.value.encode()).hexdigest() != self.sha256
        ):
            raise ValueError("retained Senate URLs require their exact safe identity")
        return self


class SenateInspectionOwner(_Fact):
    term: Term
    number: str


class SenateInspectionStage(_Fact):
    stage_type: Literal["SenatePosition"]
    stage_name: str
    date: dt.date | None
    position: str | None
    print_number: str


class SenateInspectionContext(_Fact):
    resolution_print: SenateInspectionOwner
    print_title: str
    document_date: dt.date | None
    stage: SenateInspectionStage
    passed_on: dt.date | None
    titles: tuple[str, ...]
    expected_subjects: tuple[str, ...]

    @model_validator(mode="after")
    def relation(self) -> Self:
        if self.stage.print_number != self.resolution_print.number:
            raise ValueError("the Senate stage must name its actual resolution print")
        return self


class SenateInspectionComponents(_Fact):
    capture_version: Literal["senate-inspection-v1"] = "senate-inspection-v1"
    resolver_sha256: Sha256 | None
    inspection_sha256: Sha256 | None
    python: str | None


class SenateInspectionDescriptor(_Fact):
    index: SqlInteger
    print_number: str
    name: str
    url: SenateInspectionUrl

    @property
    def descriptor_sha256(self) -> str:
        return senate_sha256(self.model_dump(mode="json"))


InspectionReason = Literal[
    "qualified_text",
    "no_resolution_heading",
    "multiple_resolutions",
    "non_cover_prefix",
    "missing_resolution_date",
    "invalid_resolution_date",
    "missing_resolution_subject",
    "missing_resolution_end",
    "unknown_resolution_decision",
    "missing_sejm_adoption",
    "negative_amendment_decision",
    "inconsistent_resolution_subject",
    "missing_amendment_body",
    "unreadable_amendment_page",
    "not_amendments",
    "subject_mismatch",
    "date_mismatch",
    "missing_hash",
    "inspection_error",
    "missing",
    "read_error",
    "source_unavailable",
    "over_limit",
    "foreign_print",
    "unsupported_extension",
    "word_precedence",
    "after_outage",
    "stage_not_eligible",
]


class SenateInspectionEvidence(_Fact):
    limitation: InspectionReason | Literal[""]
    outcome: Literal["amendments", "rejection", "acceptance"] | None
    subject: str
    adopted_on: dt.date | None
    passed_on: dt.date | None
    complete: bool

    @model_validator(mode="after")
    def completion(self) -> Self:
        if self.complete != (self.limitation == ""):
            raise ValueError("Senate inspection completion must match its captured limitation")
        return self


class SenateCandidateInspection(_Fact):
    phase: Literal["word", "pdf", "filter"]
    attempted: bool
    state: Literal["inspected", "read_error", "inspection_error", "unattempted"]
    reason: InspectionReason
    artifact_sha256: Sha256 | None = None
    artifact_bytes: SqlInteger | None = None
    pages: SqlInteger | None = None
    reader_text_sha256: Sha256 | None = None
    artifact_read_id: str | None = Field(default=None, pattern=r"^artifact-read-v1:[0-9a-f]{64}$")
    extraction_id: str | None = Field(default=None, pattern=r"^extraction-v1:[0-9a-f]{64}$")
    source_observation_id: str | None = Field(
        default=None, pattern=r"^source-observation-v1:[0-9a-f]{64}$"
    )
    selection_id: str | None = Field(default=None, pattern=r"^selection-v1:[0-9a-f]{64}$")
    error_class: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
    word_eof: bool | None = None
    inspection: SenateInspectionEvidence | None = None
    normalized_resolution_sha256: Sha256 | None = None
    visual_eligible: bool = False

    @model_validator(mode="after")
    def reached(self) -> Self:
        if self.attempted != (self.state != "unattempted"):
            raise ValueError("candidate attempts must match their reached state")
        if self.state == "unattempted":
            if self.phase != "filter" or self.reason not in (
                "foreign_print",
                "unsupported_extension",
                "word_precedence",
                "after_outage",
                "stage_not_eligible",
            ):
                raise ValueError("unattempted candidates require their exact skip reason")
        elif self.phase == "filter":
            raise ValueError("attempted candidates must retain their inspection phase")
        if self.state == "read_error":
            if self.error_class is None or self.reason not in (
                "missing",
                "read_error",
                "source_unavailable",
            ):
                raise ValueError("read failures require a bounded error class and reason")
        elif self.state == "inspection_error":
            if self.error_class is None or self.reason != "inspection_error":
                raise ValueError("failed inspection requires a class and its exact reached state")
        elif self.error_class is not None:
            raise ValueError("only a failed read or inspection carries an exception class")
        if self.state == "inspected":
            if self.inspection is None or self.word_eof is None:
                raise ValueError("inspected candidates require their actual inspection facts")
        elif self.state != "inspection_error" and (
            any(
                value is not None
                for value in (
                    self.artifact_sha256,
                    self.artifact_bytes,
                    self.pages,
                    self.reader_text_sha256,
                    self.inspection,
                    self.normalized_resolution_sha256,
                    self.word_eof,
                )
            )
            or self.visual_eligible
        ):
            raise ValueError(
                "uncompleted reads cannot invent completed body or inspection metadata"
            )
        if self.state == "inspection_error" and (
            self.inspection is not None
            or self.normalized_resolution_sha256 is not None
            or self.visual_eligible
        ):
            raise ValueError("a failed inspection cannot invent a completed interpretation")
        if self.state == "inspected" and self.reason in (
            "foreign_print",
            "unsupported_extension",
            "word_precedence",
            "after_outage",
            "stage_not_eligible",
            "missing",
            "read_error",
            "source_unavailable",
            "inspection_error",
        ):
            raise ValueError("an inspected candidate cannot claim a skip or failed read")
        if (self.artifact_sha256 is None) != (self.artifact_bytes is None):
            raise ValueError("known complete bodies require both SHA and size")
        if self.artifact_sha256 is None and any(
            value is not None
            for value in (
                self.artifact_read_id,
                self.extraction_id,
                self.source_observation_id,
                self.selection_id,
            )
        ):
            raise ValueError("physical parents require a known complete body")
        if (self.source_observation_id is None) != (self.selection_id is None):
            raise ValueError("a persisted candidate source must identify its selection")
        if self.inspection is not None:
            evidence = self.inspection
            contextual = {
                "qualified_text",
                "not_amendments",
                "subject_mismatch",
                "date_mismatch",
                "missing_hash",
                "over_limit",
            }
            if self.reason not in contextual and evidence.limitation != self.reason:
                raise ValueError("candidate rejection must retain its actual inspection limitation")
            if self.reason in {
                "qualified_text",
                "subject_mismatch",
                "date_mismatch",
                "missing_hash",
            } and (not evidence.complete or evidence.outcome != "amendments"):
                raise ValueError(
                    "contextual amendment checks require complete inspected amendments"
                )
            if self.reason == "not_amendments" and (
                not evidence.complete or evidence.outcome == "amendments"
            ):
                raise ValueError(
                    "a non-amendment rejection requires its actual completed chamber decision"
                )
        if self.reason == "missing_hash" and self.artifact_sha256 is not None:
            raise ValueError("missing-hash rejection cannot carry an invented complete body SHA")
        if self.reason == "qualified_text" and (
            self.artifact_sha256 is None
            or self.normalized_resolution_sha256 is None
            or self.inspection is None
            or not self.inspection.complete
            or self.inspection.outcome != "amendments"
        ):
            raise ValueError(
                "qualified candidates require complete amendment evidence and body identity"
            )
        return self


class SenateInspectionCandidate(_Fact):
    descriptor: SenateInspectionDescriptor
    inspection: SenateCandidateInspection


class SenateInspectionPhase(_Fact):
    phase: Literal["word", "pdf"]
    incomplete: bool
    qualified_indices: tuple[SqlInteger, ...]
    equivalence_groups: tuple[Sha256, ...]


class SenateInspectionIdentity(_Fact):
    sha256: Sha256
    subject: str
    adopted_on: dt.date
    word_eof: bool = False
    scan: bool = False
    expected_subjects: tuple[str, ...] = ()
    document_date: dt.date | None = None
    passed_on: dt.date | None = None
    pages: SqlInteger = 0


InspectionOutcome = Literal[
    "matching_equivalent_word",
    "matching_equivalent_pdf",
    "unique_visual_fallback",
    "incomplete_competing_phase",
    "conflicting_competing_phase",
    "nonunique_visual_fallback",
    "all_rejected",
    "propagated_failure",
    "guard_rejected",
]


class SenateInspectionResult(_Fact):
    outcome: InspectionOutcome
    descriptor_index: SqlInteger | None
    identity: SenateInspectionIdentity | None
    error_class: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")

    @model_validator(mode="after")
    def result(self) -> Self:
        chosen = self.outcome in (
            "matching_equivalent_word",
            "matching_equivalent_pdf",
            "unique_visual_fallback",
        )
        if chosen != (self.descriptor_index is not None and self.identity is not None) or (
            self.descriptor_index is None
        ) != (self.identity is None):
            raise ValueError("a chosen Senate result requires its descriptor and actual identity")
        if (self.error_class is not None) != (self.outcome == "propagated_failure"):
            raise ValueError("propagated failure requires its bounded class only")
        return self


class SenateInspectionPayload(_Fact):
    contract: Literal["senate-inspection-v1"] = "senate-inspection-v1"
    scope: Literal["candidate_inspection_not_admission_or_application"] = (
        "candidate_inspection_not_admission_or_application"
    )
    owner: SenateInspectionOwner
    context: SenateInspectionContext
    components: SenateInspectionComponents
    universe: tuple[SenateInspectionCandidate, ...]
    phases: tuple[SenateInspectionPhase, ...]
    result: SenateInspectionResult

    @model_validator(mode="after")
    def coverage(self) -> Self:
        if self.owner.term != self.context.resolution_print.term:
            raise ValueError("owner and Senate resolution must belong to the same term")
        if tuple(item.descriptor.index for item in self.universe) != tuple(
            range(len(self.universe))
        ):
            raise ValueError("the candidate universe must preserve every ordered descriptor")
        names = tuple(phase.phase for phase in self.phases)
        if len(names) != len(set(names)):
            raise ValueError("captured phase summaries must have distinct phase names")
        for phase in self.phases:
            candidates = [
                (index, item.inspection)
                for index, item in enumerate(self.universe)
                if item.inspection.phase == phase.phase
            ]
            qualified = {index for index, fact in candidates if fact.reason == "qualified_text"}
            groups = {
                fact.normalized_resolution_sha256
                for _, fact in candidates
                if fact.reason == "qualified_text" and fact.normalized_resolution_sha256 is not None
            }
            if (
                len(phase.qualified_indices) != len(set(phase.qualified_indices))
                or set(phase.qualified_indices) != qualified
            ):
                raise ValueError("phase indices must match distinct captured qualified candidates")
            if (
                len(phase.equivalence_groups) != len(set(phase.equivalence_groups))
                or set(phase.equivalence_groups) != groups
            ):
                raise ValueError("phase groups must match captured normalized-resolution digests")
        index = self.result.descriptor_index
        if index is not None:
            if index >= len(self.universe):
                raise ValueError("the chosen descriptor must exist in the universe")
            assert self.result.identity is not None, (
                "SenateInspectionResult pairs the chosen locator and identity"
            )
            if self.result.identity.sha256 != self.universe[index].inspection.artifact_sha256:
                raise ValueError("the chosen identity must match its actual captured body")
        return self

    def physical_links(self) -> tuple[SenateInspectionLink, ...]:
        links: list[SenateInspectionLink] = []
        for item in self.universe:
            fact = item.inspection
            if fact.artifact_sha256 is None:
                continue
            assert fact.artifact_bytes is not None, (
                "SenateCandidateInspection pairs known body SHA and size"
            )
            links.append(
                SenateInspectionLink(
                    descriptor_index=item.descriptor.index,
                    descriptor_sha256=item.descriptor.descriptor_sha256,
                    requested_url_sha256=item.descriptor.url.sha256,
                    artifact_sha256=fact.artifact_sha256,
                    artifact_bytes=fact.artifact_bytes,
                    observation_id=fact.source_observation_id,
                    selection_id=fact.selection_id,
                    reader_text_sha256=fact.reader_text_sha256,
                    artifact_read_id=fact.artifact_read_id,
                    extraction_id=fact.extraction_id,
                )
            )
        return tuple(links)


class SenateInspectionLink(_Fact):
    descriptor_index: SqlInteger
    descriptor_sha256: Sha256
    requested_url_sha256: Sha256
    artifact_sha256: Sha256
    artifact_bytes: SqlInteger
    observation_id: str | None = Field(
        default=None, pattern=r"^source-observation-v1:[0-9a-f]{64}$"
    )
    selection_id: str | None = Field(default=None, pattern=r"^selection-v1:[0-9a-f]{64}$")
    reader_text_sha256: Sha256 | None = None
    artifact_read_id: str | None = Field(default=None, pattern=r"^artifact-read-v1:[0-9a-f]{64}$")
    extraction_id: str | None = Field(default=None, pattern=r"^extraction-v1:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def source_pair(self) -> Self:
        if (self.observation_id is None) != (self.selection_id is None):
            raise ValueError("known Senate source observations require their actual selection ID")
        return self


class SenateInspectionOmission(_Fact):
    contract: Literal["senate-inspection-omission-v1"] = "senate-inspection-omission-v1"
    scope: Literal["candidate_inspection_not_admission_or_application"] = (
        "candidate_inspection_not_admission_or_application"
    )
    owner: SenateInspectionOwner
    resolution_print: SenateInspectionOwner
    stage_sha256: Sha256
    context_sha256: Sha256
    universe_sha256: Sha256
    decision_sha256: Sha256
    descriptor_count: SqlInteger
    attempted_count: SqlInteger
    known_body_count: SqlInteger
    terminal: InspectionOutcome
    chosen_descriptor_index: SqlInteger | None
    chosen_identity_sha256: Sha256 | None
    error_class_sha256: Sha256 | None
    omitted: Literal["whole_universe_context_decisions_over_cap"] = (
        "whole_universe_context_decisions_over_cap"
    )
    complete_metadata_revalidation: Literal["unavailable"] = "unavailable"

    @classmethod
    def of(cls, payload: SenateInspectionPayload) -> Self:
        values = payload.model_dump(mode="json")
        return cls(
            owner=payload.owner,
            resolution_print=payload.context.resolution_print,
            stage_sha256=senate_sha256(values["context"]["stage"]),
            context_sha256=senate_sha256(values["context"]),
            universe_sha256=senate_sha256([value["descriptor"] for value in values["universe"]]),
            decision_sha256=senate_sha256(
                {key: values[key] for key in ("universe", "phases", "result")}
            ),
            descriptor_count=len(payload.universe),
            attempted_count=sum(item.inspection.attempted for item in payload.universe),
            known_body_count=sum(
                item.inspection.artifact_sha256 is not None for item in payload.universe
            ),
            terminal=payload.result.outcome,
            chosen_descriptor_index=payload.result.descriptor_index,
            chosen_identity_sha256=senate_sha256(values["result"]["identity"])
            if payload.result.identity
            else None,
            error_class_sha256=hashlib.sha256(payload.result.error_class.encode()).hexdigest()
            if payload.result.error_class is not None
            else None,
        )

    @model_validator(mode="after")
    def bounds(self) -> Self:
        if (
            self.owner.term != self.resolution_print.term
            or not self.known_body_count <= self.attempted_count <= self.descriptor_count
        ):
            raise ValueError("omission owner and candidate counts must remain coherent")
        if (self.chosen_descriptor_index is None) != (self.chosen_identity_sha256 is None):
            raise ValueError("omission must preserve both chosen locator and identity hash")
        if (self.error_class_sha256 is not None) != (self.terminal == "propagated_failure"):
            raise ValueError("omitted failure must retain its exception-class hash")
        chosen = self.terminal in (
            "matching_equivalent_word",
            "matching_equivalent_pdf",
            "unique_visual_fallback",
        )
        if chosen != (self.chosen_descriptor_index is not None):
            raise ValueError("omission terminal must preserve whether a source was chosen")
        if (
            self.chosen_descriptor_index is not None
            and self.chosen_descriptor_index >= self.descriptor_count
        ):
            raise ValueError("omitted choice must stay within its original descriptor universe")
        return self


def _senate_identity(
    semantic: str, link_count: int, links_sha256: str, summary: SenateInspectionOmission
) -> str:
    return "senate-inspection-v1:" + senate_sha256(
        {
            "contract": "senate-inspection-identity-v1",
            "semantic_sha256": semantic,
            "link_count": link_count,
            "links_sha256": links_sha256,
            "retained_summary": summary.model_dump(mode="json"),
        }
    )


class SenateInspectionRecord(_Fact):
    inspection_id: str = Field(pattern=r"^senate-inspection-v1:[0-9a-f]{64}$")
    semantic_sha256: Sha256
    coverage: Literal["complete", "omitted_over_limit"]
    link_count: SqlInteger
    links_sha256: Sha256
    payload: Annotated[
        SenateInspectionPayload | SenateInspectionOmission, Field(discriminator="contract")
    ]
    recorded_at: dt.datetime | None = None

    @classmethod
    def of(
        cls, payload: SenateInspectionPayload, links: tuple[SenateInspectionLink, ...] | None = None
    ) -> Self:
        payload = SenateInspectionPayload.model_validate_json(payload.model_dump_json())
        links = (
            payload.physical_links()
            if links is None
            else tuple(
                SenateInspectionLink.model_validate_json(link.model_dump_json()) for link in links
            )
        )
        values = payload.model_dump(mode="json")
        manifest = {
            "link_count": len(links),
            "links_sha256": senate_sha256([link.model_dump(mode="json") for link in links]),
        }
        semantic = senate_sha256({"payload": values, **manifest})
        summary = SenateInspectionOmission.of(payload)
        identity = _senate_identity(semantic, len(links), str(manifest["links_sha256"]), summary)
        base = {"inspection_id": identity, "semantic_sha256": semantic, **manifest}
        cls.model_construct(
            inspection_id=identity,
            semantic_sha256=semantic,
            link_count=len(links),
            links_sha256=str(manifest["links_sha256"]),
            coverage="complete",
            payload=payload,
            recorded_at=None,
        ).verify_links(links)
        complete = {
            **base,
            "coverage": "complete",
            "payload": values,
            "recorded_at": _MAX_TIME.isoformat(),
        }
        result = (
            cls(**base, coverage="complete", payload=payload)
            if len(senate_canonical(complete)) <= MAX_SENATE_INSPECTION_BYTES
            else cls(**base, coverage="omitted_over_limit", payload=summary)
        )
        result.verify_links(links)
        return result

    @field_validator("recorded_at")
    @classmethod
    def recording_time(cls, value: dt.datetime | None) -> dt.datetime | None:
        if value is None:
            return None
        if value.utcoffset() is None:
            raise ValueError("Senate inspection recording time requires a timezone")
        return value.astimezone(dt.UTC)

    @model_validator(mode="after")
    def identity_and_size(self) -> Self:
        complete = isinstance(self.payload, SenateInspectionPayload)
        if complete != (self.coverage == "complete"):
            raise ValueError("Senate inspection payload and coverage must agree")
        if (
            complete
            and senate_sha256(
                {
                    "payload": self.payload.model_dump(mode="json"),
                    "link_count": self.link_count,
                    "links_sha256": self.links_sha256,
                }
            )
            != self.semantic_sha256
        ):
            raise ValueError("complete Senate metadata must reproduce its semantic identity")
        summary = (
            SenateInspectionOmission.of(self.payload)
            if isinstance(self.payload, SenateInspectionPayload)
            else self.payload
        )
        if self.inspection_id != _senate_identity(
            self.semantic_sha256, self.link_count, self.links_sha256, summary
        ):
            raise ValueError("immutable Senate inspection identity differs from retained metadata")
        if len(
            senate_canonical({**self.model_dump(mode="json"), "recorded_at": _MAX_TIME.isoformat()})
        ) > (MAX_SENATE_INSPECTION_BYTES if complete else MAX_SENATE_OMISSION_BYTES):
            raise ValueError("Senate inspection exceeds its whole-record UTF-8 byte cap")
        return self

    @property
    def owner(self) -> SenateInspectionOwner:
        return self.payload.owner

    @property
    def resolution_print(self) -> SenateInspectionOwner:
        return (
            self.payload.context.resolution_print
            if isinstance(self.payload, SenateInspectionPayload)
            else self.payload.resolution_print
        )

    @property
    def descriptor_count(self) -> int:
        return (
            len(self.payload.universe)
            if isinstance(self.payload, SenateInspectionPayload)
            else self.payload.descriptor_count
        )

    def verify_links(self, links: tuple[SenateInspectionLink, ...]) -> None:
        if tuple(link.descriptor_index for link in links) != tuple(
            sorted({link.descriptor_index for link in links})
        ):
            raise ValueError("Senate source links require distinct ordered descriptor locators")
        if (
            len(links) != self.link_count
            or senate_sha256([link.model_dump(mode="json") for link in links]) != self.links_sha256
        ):
            raise ValueError("Senate source links must reproduce the complete immutable manifest")
        known_bodies = (
            sum(item.inspection.artifact_sha256 is not None for item in self.payload.universe)
            if isinstance(self.payload, SenateInspectionPayload)
            else self.payload.known_body_count
        )
        if self.link_count != known_bodies:
            raise ValueError("every known complete candidate body must retain its physical link")
        if any(link.descriptor_index >= self.descriptor_count for link in links):
            raise ValueError("Senate source links must stay inside the original universe")
        if isinstance(self.payload, SenateInspectionPayload):
            expected = tuple(
                item.descriptor.index
                for item in self.payload.universe
                if item.inspection.artifact_sha256 is not None
            )
            if expected != tuple(link.descriptor_index for link in links):
                raise ValueError(
                    "full inspection refs must match all of its persisted source links"
                )
            for link in links:
                item = self.payload.universe[link.descriptor_index]
                fact = item.inspection
                if (
                    item.descriptor.descriptor_sha256,
                    item.descriptor.url.sha256,
                    fact.source_observation_id,
                    fact.selection_id,
                    fact.artifact_sha256,
                    fact.artifact_bytes,
                    fact.reader_text_sha256,
                    fact.artifact_read_id,
                    fact.extraction_id,
                ) != (
                    link.descriptor_sha256,
                    link.requested_url_sha256,
                    link.observation_id,
                    link.selection_id,
                    link.artifact_sha256,
                    link.artifact_bytes,
                    link.reader_text_sha256,
                    link.artifact_read_id,
                    link.extraction_id,
                ):
                    raise ValueError("Senate source links differ from their exact candidate facts")
