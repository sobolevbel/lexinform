import datetime as dt
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

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

    def without_text(self) -> DocumentSelection:
        return self.model_copy(
            update={
                "parts": tuple(part.model_copy(update={"text": ""}) for part in self.parts),
            }
        )

    @model_validator(mode="after")
    def selected_requires_evidence(self) -> Self:
        if self.state == "selected" and not all(
            (
                self.belonging,
                self.role,
                self.version,
                self.completeness,
                self.parts,
            )
        ):
            raise ValueError("selected documents require four independent evidence checks")
        return self


class ExtractedDocument(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str = ""
    selection: DocumentSelection = DocumentSelection()


class DocumentWork(BaseModel):
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
