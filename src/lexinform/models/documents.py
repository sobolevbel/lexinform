import datetime as dt
import hashlib
import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

SelectionState = Literal["unverified", "selected", "waiting", "ambiguous", "unsupported"]
DocumentRole = Literal[
    "bill", "normative_annex", "justification", "osr", "self_amendment", "unknown"
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


class DocumentFact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["official_link", "bill_structure", "draft_date", "coverage"]
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


class DocumentSelection(BaseModel):
    model_config = ConfigDict(frozen=True)

    question: str = "bill_text"
    state: SelectionState = "unverified"
    belonging: tuple[str, ...] = ()
    role: tuple[str, ...] = ()
    version: tuple[str, ...] = ()
    completeness: tuple[str, ...] = ()
    parts: tuple[DocumentPart, ...] = ()
    rejected: tuple[str, ...] = ()
    facts: tuple[DocumentFact, ...] = ()
    limitations: tuple[Literal["support_applicability_unknown", "missing_evidence"], ...] = ()

    def without_text(self) -> DocumentSelection:
        return self.model_copy(
            update={
                "parts": tuple(part.model_copy(update={"text": ""}) for part in self.parts),
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
