import datetime as dt
import hashlib
import json
import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lexinform.models.scan_input import ScanInputManifest
from lexinform.models.word_notes import WordNoteExtraction
from lexinform.pdf_projection import legacy_pdf_text
from lexinform.word_projection import legacy_word_text

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

    @property
    def source_sha256(self) -> str:
        return self.container_sha256 if self.members else self.sha256


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
    policy_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^\S+$",
        exclude_if=lambda value: value is None,
    )
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
    scan_input: ScanInputManifest | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

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


class PdfPageText(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    number: int = Field(ge=1)
    text: str = ""
    error_type: str | None = None

    @property
    def block_id(self) -> str:
        return f"pdf-page:{self.number}:0"

    @property
    def text_sha256(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()

    @model_validator(mode="after")
    def failed_page_has_no_text(self) -> Self:
        if self.error_type is not None and self.text:
            raise ValueError("A failed page cannot claim extracted text")
        return self


class PdfTextExtraction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["pdf-pages-v1"] = "pdf-pages-v1"
    text_view: Literal["pypdf-default"] = "pypdf-default"
    legacy_projection: Literal["pdf-whitespace-v1"] = "pdf-whitespace-v1"
    input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    pypdf_version: str = Field(min_length=1)
    pages: tuple[PdfPageText, ...] = ()
    error_type: str | None = None

    @property
    def legacy_text(self) -> str:
        if self.error_type is not None:
            return ""
        return legacy_pdf_text(page.text for page in self.pages)

    @property
    def page_count(self) -> int | None:
        return len(self.pages) if self.error_type is None else None

    @property
    def manifest_sha256(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    @model_validator(mode="after")
    def pages_follow_physical_order(self) -> Self:
        if any(page.number != number for number, page in enumerate(self.pages, 1)):
            raise ValueError("PDF page records must retain consecutive physical positions")
        return self


WordBodyState = Literal["read", "missing", "unsupported"]


class WordTextBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    xml_part: Literal["word/document.xml"] = "word/document.xml"
    kind: Literal["paragraph", "table_row", "table_cell"]
    path: tuple[int, ...] = Field(min_length=1)
    text: str = ""

    @property
    def block_id(self) -> str:
        return f"word-xml:{self.xml_part}:{'/'.join(str(index) for index in self.path)}"

    @property
    def text_sha256(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()

    @model_validator(mode="after")
    def child_positions_are_nonnegative(self) -> Self:
        if any(index < 0 for index in self.path):
            raise ValueError("Word XML child positions must be nonnegative")
        return self


class WordTextExtraction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["word-blocks-v1"] = "word-blocks-v1"
    text_view: Literal["legacy-word-body-v1"] = "legacy-word-body-v1"
    input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    xml_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    python_version: str = Field(min_length=1)
    body_state: WordBodyState
    blocks: tuple[WordTextBlock, ...] = ()
    legacy_blocks: tuple[str, ...] = ()

    @property
    def legacy_text(self) -> str:
        blocks = {block.block_id: block.text for block in self.blocks}
        return legacy_word_text(blocks[identity] for identity in self.legacy_blocks)

    @property
    def manifest_sha256(self) -> str:
        data = json.dumps(
            self.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(data.encode()).hexdigest()

    @model_validator(mode="after")
    def physical_blocks_and_projection_agree(self) -> Self:
        paths = [block.path for block in self.blocks]
        if paths != sorted(paths) or len(set(paths)) != len(paths):
            raise ValueError("Word blocks require unique paths in XML order")
        blocks = {block.block_id: block for block in self.blocks}
        if len(set(self.legacy_blocks)) != len(self.legacy_blocks) or any(
            identity not in blocks or blocks[identity].kind == "table_cell"
            for identity in self.legacy_blocks
        ):
            raise ValueError("Word projection must refer to distinct paragraph or row blocks")
        if self.body_state != "read" and (self.blocks or self.legacy_blocks):
            raise ValueError("An unread Word body cannot claim physical blocks")
        return self


class WordDetailedExtraction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    body: WordTextExtraction
    notes: WordNoteExtraction

    @model_validator(mode="after")
    def notes_refer_to_the_same_body(self) -> Self:
        if (
            self.body.input_sha256 != self.notes.input_sha256
            or self.body.manifest_sha256 != self.notes.body_manifest_sha256
        ):
            raise ValueError("Word notes refer to a different body extraction")
        paragraphs = {block.path for block in self.body.blocks if block.kind == "paragraph"}
        if any(call.paragraph_path not in paragraphs for call in self.notes.calls):
            raise ValueError("Word note call refers to an absent paragraph")
        return self


class PdfPartExtraction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    part_index: int = Field(ge=0)
    extraction: PdfTextExtraction


class WordPartExtraction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    part_index: int = Field(ge=0)
    extraction: WordTextExtraction


class WordPartNoteExtraction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    part_index: int = Field(ge=0)
    extraction: WordNoteExtraction


class ExtractedParts(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    parts: tuple[DocumentPart, ...] = ()
    rejected: tuple[str, ...] = ()
    pdf_pages: tuple[PdfPartExtraction, ...] = ()
    word_blocks: tuple[WordPartExtraction, ...] = ()
    word_notes: tuple[WordPartNoteExtraction, ...] = ()

    @model_validator(mode="after")
    def manifests_refer_to_physical_parts(self) -> Self:
        seen: set[int] = set()
        for result in self.pdf_pages:
            index = result.part_index
            if index >= len(self.parts) or index in seen:
                raise ValueError("PDF page records require one existing physical part")
            if self.parts[index].sha256 != result.extraction.input_sha256:
                raise ValueError("PDF page records refer to different physical bytes")
            seen.add(index)
        seen.clear()
        for word_result in self.word_blocks:
            index = word_result.part_index
            if index >= len(self.parts) or index in seen:
                raise ValueError("Word blocks require one existing physical part")
            part = self.parts[index]
            if part.sha256 != word_result.extraction.input_sha256:
                raise ValueError("Word blocks refer to different physical bytes")
            if part.text != word_result.extraction.legacy_text:
                raise ValueError("Word blocks differ from the physical part text")
            seen.add(index)
        bodies = {result.part_index: result.extraction for result in self.word_blocks}
        seen.clear()
        for note_result in self.word_notes:
            index = note_result.part_index
            if index not in bodies or index in seen:
                raise ValueError("Word notes require one existing body manifest")
            WordDetailedExtraction(body=bodies[index], notes=note_result.extraction)
            seen.add(index)
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
