import datetime as dt
import hashlib
import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

SelectionState = Literal["unverified", "selected", "waiting", "ambiguous", "unsupported"]
DocumentRole = Literal[
    "bill",
    "normative_annex",
    "normative_continuation",
    "justification",
    "osr",
    "self_amendment",
    "unknown",
]


class DocumentPart(BaseModel):
    model_config = ConfigDict(frozen=True)

    url: str = ""
    members: tuple[str, ...] = ()
    container_sha256: str = ""
    sha256: str = ""
    role: DocumentRole = "unknown"
    text: str = ""
    pages: int = 0
    page_start: int | None = None
    page_end: int | None = None
    text_start: int = 0
    text_end: int | None = None
    text_sha256: str = ""
    losses: tuple[str, ...] = ()


FactKind = Literal[
    "official_link",
    "bill_structure",
    "draft_date",
    "coverage",
    "annex_reference",
    "annex_heading",
    "support_reference",
    "norm_value",
    "version_conflict",
]


class DocumentFact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: FactKind
    value: str
    provenance: Literal["source_relation", "extracted_text"]
    part_index: int = Field(ge=0)
    source_url: str
    text_sha256: str
    block_id: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    quote: str

    @model_validator(mode="after")
    def fact_value(self) -> Self:
        if not re.fullmatch(r"[0-9a-f]{64}", self.text_sha256):
            raise ValueError("evidence requires an extracted text hash")
        if self.kind == "draft_date":
            dt.date.fromisoformat(self.value)
        if self.kind == "bill_structure" and self.value != "bill":
            raise ValueError("bill structure requires the bill role")
        if self.kind == "coverage" and self.value != "body_through_commencement":
            raise ValueError("unknown coverage scope")
        return self


class DocumentLink(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["normative_annex", "supporting"]
    source_part: int = Field(ge=0)
    target_part: int = Field(ge=0)
    status: Literal["confirmed", "unknown", "conflicting"]
    evidence: tuple[int, ...] = ()


class DocumentCoverage(BaseModel):
    model_config = ConfigDict(frozen=True)

    part_index: int = Field(ge=0)
    stage: Literal["extraction", "analysis"] = "extraction"
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    page_start: int | None = None
    page_end: int | None = None
    losses: tuple[str, ...] = ()


DocumentLimitation = Literal[
    "support_applicability_unknown",
    "missing_evidence",
    "missing_own_annex",
    "annex_link_unknown",
    "foreign_annex",
    "support_conflict",
    "normative_text_omitted",
    "draft_version_conflict",
]


class DocumentSelection(BaseModel):
    model_config = ConfigDict(frozen=True)

    question: str = "bill_text"
    state: SelectionState = "unverified"
    belonging: tuple[str, ...] = ()
    role: tuple[str, ...] = ()
    version: tuple[str, ...] = ()
    completeness: tuple[str, ...] = ()
    parts: tuple[DocumentPart, ...] = ()
    observed_parts: tuple[DocumentPart, ...] = ()
    rejected: tuple[str, ...] = ()
    facts: tuple[DocumentFact, ...] = ()
    limitations: tuple[DocumentLimitation, ...] = ()
    links: tuple[DocumentLink, ...] = ()
    coverage: tuple[DocumentCoverage, ...] = ()
    normative_sha256: str = ""

    def without_text(self) -> DocumentSelection:
        return self.model_copy(
            update={
                "parts": tuple(part.model_copy(update={"text": ""}) for part in self.parts),
                "observed_parts": tuple(
                    part.model_copy(update={"text": ""}) for part in self.observed_parts
                ),
            }
        )

    @model_validator(mode="before")
    @classmethod
    def legacy_selection(cls, value: object) -> object:
        if isinstance(value, dict) and value.get("state") == "selected" and not value.get("facts"):
            return {**value, "state": "unverified"}
        return value

    @model_validator(mode="after")
    def selected_requires_evidence(self) -> Self:
        for fact in self.facts:
            if fact.part_index >= len(self.parts):
                raise ValueError("evidence refers to an absent part")
            part = self.parts[fact.part_index]
            if part.text and hashlib.sha256(part.text.encode()).hexdigest() != part.text_sha256:
                raise ValueError("part text differs from its evidence hash")
            if fact.source_url != part.url or fact.text_sha256 != part.text_sha256:
                raise ValueError("evidence source identity differs from the part")
            if fact.block_id != f"part:{fact.part_index}" or fact.end < fact.start:
                raise ValueError("invalid evidence coordinates")
            if fact.kind == "official_link":
                if (
                    fact.provenance != "source_relation"
                    or not fact.source_url
                    or not fact.value
                    or fact.start != 0
                    or fact.end != 0
                    or fact.quote
                ):
                    raise ValueError("official link requires a source relation")
            elif (
                fact.provenance != "extracted_text"
                or not fact.quote
                or fact.end - fact.start != len(fact.quote)
                or (part.text and part.text[fact.start : fact.end] != fact.quote)
                or (part.text_end is not None and fact.end > part.text_end)
            ):
                raise ValueError("evidence quote differs from the source span")
        for link in self.links:
            if max(link.source_part, link.target_part) >= len(self.parts):
                raise ValueError("document link refers to an absent part")
            if any(index >= len(self.facts) for index in link.evidence):
                raise ValueError("document link refers to an absent fact")
        for span in self.coverage:
            if span.part_index >= len(self.parts) or span.end < span.start:
                raise ValueError("invalid coverage coordinates")
            part = self.parts[span.part_index]
            if part.text_end is not None and span.end > part.text_end:
                raise ValueError("coverage exceeds the source part")
        if self.state == "selected" and (
            any(link.status == "conflicting" for link in self.links)
            or "normative_text_omitted" in self.limitations
            or "missing_own_annex" in self.limitations
        ):
            raise ValueError("selected bundle has unresolved normative evidence")
        if self.state == "selected":
            required = {"official_link", "bill_structure", "draft_date", "coverage"}
            complete = any(
                {fact.kind for fact in self.facts if fact.part_index == index} >= required
                for index, part in enumerate(self.parts)
                if part.role == "bill"
            )
            if not complete or any(part.losses for part in self.parts):
                raise ValueError("selected documents require four independent evidence checks")
        return self


class ExtractedDocument(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str = ""
    selection: DocumentSelection = DocumentSelection()


class DocumentWork(BaseModel):
    """Saved Senate amendment continuation; generalise only for a second consumer."""

    key: str
    term: int
    number: str
    change_id: int
    bill_json: str
    detail_json: str
    stage_json: str
    created_at: dt.datetime
    next_at: dt.datetime
    attempts: int = 0
    state: Literal["waiting", "complete", "unsupported"] = "waiting"
    problem: str = ""
    document_json: str | None = None
    context_json: str | None = None
