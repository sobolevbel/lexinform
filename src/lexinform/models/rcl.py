"""Government bills before the Sejm: the Rządowy Proces Legislacyjny (legislacja.rcl.gov.pl).

A project page lists up to 14 stages, each reached one with a catalog of folders. No I/O here:
`adapters/rcl_html.py` turns the HTML into these models. `StageGroup` says where in the
government path a stage sits.
"""

import datetime as dt
import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from lexinform.models.enums import BILL_DOCUMENT_TYPE, RCL_PREFIX, ApplicantType, DocumentType
from lexinform.models.sejm import ProcessSummary, Stage

RCL_BASE_URL = "https://legislacja.rcl.gov.pl"
RCL_STAGE_TYPE = "RclStage"
# `xml` is Word's Flat OPC, which ministries file a draft as; see `FlatOpcTextExtractor`.
READABLE_EXTENSIONS = frozenset({"pdf", "docx", "docm", "doc", "odt", "xml", "zip"})
OPEN_STATUS = "otwarty"

StageState = Literal["not_started", "reached", "active"]
FolderKind = Literal["project", "letters", "positions", "response", "conference", "other"]
TextRole = Literal["bill", "justification", "osr"]
StageGroup = Literal["opinions", "committees", "council", "sejm"]

_STAGE_NAME = re.compile(r"^\s*(\d+)\.\s*(.+?)\s*$")


def rcl_number(project_id: int) -> str:
    """The `bills.number` of an RCL project: `RCL/12414100`."""
    return f"{RCL_PREFIX}{project_id}"


def rcl_project_id(number: str) -> int:
    return int(number.removeprefix(RCL_PREFIX))


def project_web_url(project_id: int) -> str:
    return f"{RCL_BASE_URL}/projekt/{project_id}"


def normalize_wykaz_number(value: str | None) -> str | None:
    """`UD 247` and `UD247` are the same entry of the wykaz prac legislacyjnych."""
    if value is None:
        return None
    compact = "".join(value.split()).upper()
    return compact or None


class RclDocument(BaseModel):
    """One file in a stage folder (projekt, uzasadnienie, OSR, a letter, a submitted opinion)."""

    model_config = ConfigDict(frozen=True)

    id: int
    name: str
    url: str
    created: dt.date | None = None
    author: str | None = None

    @property
    def extension(self) -> str:
        tail = self.url.rsplit("/", 1)[-1]
        return tail.rsplit(".", 1)[-1].lower() if "." in tail else ""

    @property
    def readable(self) -> bool:
        return self.extension in READABLE_EXTENSIONS


class RclFolder(BaseModel):
    """A named folder inside a stage catalog."""

    model_config = ConfigDict(frozen=True)

    id: int
    name: str
    modified: dt.date | None = None
    documents: tuple[RclDocument, ...] = ()

    @property
    def kind(self) -> FolderKind:
        name = self.name.lower()
        if name.startswith("projekt"):
            return "project"
        if name.startswith("pisma kierujące"):
            return "letters"
        if name.startswith("stanowiska zgłoszone"):
            return "positions"
        if name.startswith("odniesienie się"):
            return "response"
        if "konferencja" in name:
            return "conference"
        return "other"


class RclStage(BaseModel):
    """One node of the project timeline; `folders` are known only after the catalog was read.

    `catalog_read` is that reading, and not the same question as "are there folders": 1,098 of the
    corpus's 4,307 reached stages have none after being read whole, so emptiness cannot say
    whether anybody opened the page. A stage from an older dump reads as unread and is fetched
    once.
    """

    model_config = ConfigDict(frozen=True)

    id: int
    number: int
    name: str
    state: StageState = "not_started"
    started: dt.date | None = None
    ended: dt.date | None = None
    modified: dt.date | None = None
    folders: tuple[RclFolder, ...] = ()
    catalog_read: bool = False

    @property
    def reached(self) -> bool:
        return self.state != "not_started"

    @property
    def is_consultation(self) -> bool:
        return "konsultacje publiczne" in self.name.lower()

    @property
    def is_sejm(self) -> bool:
        return "do sejmu" in self.name.lower()

    @property
    def group(self) -> StageGroup:
        name = self.name.lower()
        if self.is_sejm:
            return "sejm"
        if "rada ministrów" in name or "notyfikacja" in name:
            return "council"
        if "komitet" in name or "komisja prawnicza" in name:
            return "committees"
        return "opinions"

    def folder(self, kind: FolderKind) -> RclFolder | None:
        return next((f for f in self.folders if f.kind == kind), None)

    def documents(self, kind: FolderKind) -> tuple[RclDocument, ...]:
        folder = self.folder(kind)
        return folder.documents if folder else ()

    def consultation_letter(self) -> RclDocument | None:
        """The pismo kierujące projekt do konsultacji, wherever the ministry filed it.

        Its own folder first, then "Projekt": 3 of the 603 corpus projects whose consultation
        catalog was read leave "Pisma kierujące" empty and file the letter beside the bill, and
        the card then goes out with neither the deadline nor the address. The fallback asks
        `text_role` and not the name, because a name saying "pismo" can be the bill.
        """
        letter = next((d for d in self.documents("letters") if d.readable), None)
        if letter is not None:
            return letter
        return next(
            (
                d
                for d in self.documents("project")
                if d.readable and "pismo" in d.name.lower() and text_role(d.name) is None
            ),
            None,
        )


class RclConsultation(BaseModel):
    """What the consultation letter (pismo kierujące) told us, plus what the folders show."""

    model_config = ConfigDict(frozen=True)

    letter_url: str | None = None
    letter_date: dt.date | None = None
    days: int | None = None
    deadline: dt.date | None = None
    email: str | None = None
    positions: int = 0
    response_published: bool = False

    def is_open(self, today: dt.date) -> bool:
        return self.deadline is not None and self.deadline >= today

    @property
    def results_published(self) -> bool:
        return self.positions > 0 or self.response_published


class RclProjectSummary(BaseModel):
    """One row of the project list; a date the parser does not recognise is None rather than
    "very old", which would stop the walk through the listing."""

    model_config = ConfigDict(frozen=True)

    id: int
    title: str
    applicant: str
    wykaz_number: str | None = None
    created: dt.date | None = None
    modified: dt.date | None = None

    @property
    def web_url(self) -> str:
        return project_web_url(self.id)


class RclProject(BaseModel):
    """A project page with its timeline; stage folders are filled in for reached stages.

    `term_label` is the Sejm term in Roman numerals ("X"), `rm_number` the RM-0610-139-26 the
    project gets when it goes to the Sejm, and `print_number` the druk, stamped by Sejm discovery
    once it appears.
    """

    model_config = ConfigDict(frozen=True)

    id: int
    title: str
    applicant: str
    wykaz_number: str | None = None
    wykaz_url: str | None = None
    created: dt.date
    modified: dt.date
    departments: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    status: str = OPEN_STATUS
    eu_note: str | None = None
    term_label: str | None = None
    rm_number: str | None = None
    sejm_url: str | None = None
    print_number: str | None = None
    stages: tuple[RclStage, ...] = ()
    consultation: RclConsultation | None = None

    @property
    def number(self) -> str:
        return rcl_number(self.id)

    @property
    def web_url(self) -> str:
        return project_web_url(self.id)

    @property
    def comment_url(self) -> str:
        return f"{self.web_url}/komentarz"

    @property
    def is_open(self) -> bool:
        return self.status.strip().lower() == OPEN_STATUS

    @property
    def is_over(self) -> bool:
        """Closed on RCL without reaching the Sejm: the government dropped the project."""
        return not self.is_open and not self.sent_to_sejm

    @property
    def reached_stages(self) -> tuple[RclStage, ...]:
        return tuple(st for st in self.stages if st.reached)

    @property
    def current_stage(self) -> RclStage | None:
        active = next((st for st in self.stages if st.state == "active"), None)
        if active is not None:
            return active
        reached = self.reached_stages
        return reached[-1] if reached else None

    @property
    def consultation_stage(self) -> RclStage | None:
        return next((st for st in self.stages if st.is_consultation and st.reached), None)

    @property
    def sent_to_sejm(self) -> bool:
        return self.rm_number is not None or any(st.is_sejm and st.reached for st in self.stages)

    def with_stage(self, stage: RclStage) -> RclProject:
        """A copy with one timeline node replaced by its catalog version."""
        stages = tuple(stage if st.id == stage.id else st for st in self.stages)
        return self.model_copy(update={"stages": stages})

    def without_documents(self) -> RclProject:
        """The skeleton kept for a project the pipeline gave up on: timeline, metadata and the
        consultation, but no folders or documents (most of the bytes, never read again)."""
        stages = tuple(st.model_copy(update={"folders": ()}) for st in self.stages)
        return self.model_copy(update={"stages": stages})

    def text_documents(self) -> dict[TextRole, RclDocument]:
        """The newest readable bill text with its uzasadnienie and OSR.

        Later stages carry newer versions of the text (after Komisja Prawnicza, after the Rada
        Ministrów), so the last reached stage with a "Projekt" folder wins. PDF is preferred over
        DOCX when a file is published in both formats.
        """
        for stage in reversed(self.reached_stages):
            documents = [d for d in stage.documents("project") if d.readable]
            if documents:
                return _classify(documents)
        return {}


def _classify(documents: list[RclDocument]) -> dict[TextRole, RclDocument]:
    picked: dict[TextRole, RclDocument] = {}
    for role in ("justification", "osr", "bill"):
        candidates = [d for d in documents if text_role(d.name) == role]
        if not candidates:
            continue
        if role == "bill":
            picked[role] = min(candidates, key=_rank)
        else:
            picked[role] = min(candidates, key=_format_rank)
    if "bill" in picked:
        return picked
    fallback = _bill_of_last_resort(documents)
    if fallback is None:
        return picked
    # One file can be all three documents at once, and then it is the bill: handing it to the
    # analysis a second time as an "extra" would pay for the same text twice.
    kept = {role: d for role, d in picked.items() if d.url != fallback.url}
    kept["bill"] = fallback
    return kept


def _bill_of_last_resort(documents: list[RclDocument]) -> RclDocument | None:
    """The bill when no file in the folder calls itself one.

    Of the corpus's 824 projects with readable files in their newest "Projekt" folder, nine end
    with no bill: six publish it in one file with the uzasadnienie, which `text_role` tests first,
    and three file it as a numbered appendix, which the appendix rule drops. Reached only where
    the answer would otherwise be nothing, so it changes no project with a recognised bill.
    """
    candidates = [d for d in documents if _names_a_bill(d.name)]
    return min(candidates, key=_rank) if candidates else None


def _names_a_bill(name: str) -> bool:
    """Whether a name not taken for the bill still says it carries one. The appendix mark is
    ignored, because the bill can be an appendix; everything else `text_role` refuses stands — a
    tabela zgodności, a pismo or an autopoprawka is not the bill, whatever else its name says."""
    lowered = name.lower()
    return bool(_BILL_RE.search(lowered)) and not _NOT_A_TEXT_RE.search(
        _APPENDIX_RE.sub(" ", lowered)
    )


_OSR_RE = re.compile(r"(?:^|[^a-ząćęłńóśźż])osr(?:$|[^a-ząćęłńóśźż])|ocena skutk")
_NOT_A_TEXT_RE = re.compile(
    # Measured on the "Projekt" folders and packages of the followed projects (13 Sept 2026):
    # every kind below is published beside the bill and none of it is the bill. "protokół" is
    # deliberately not here — a bill ratifying a Protokół is a bill — and neither is "raport"
    # on its own, for "o raportowaniu"; the rejected-comments tables come as "zestawienie".
    # A package names its members with underscores where the folder uses spaces, so every
    # multi-word pattern has to accept both.
    # "opinia" is word-bounded because "opiniowanie" is a stage, and a file called
    # "projekt - opiniowanie.pdf" is the bill as published for it.
    r"tabel|zgodno|załącznik|zalacznik|zał\.|pismo|rozdzielnik"
    r"|rozbieżno|rozbiezno|raport[\s_]+z|zestawienie|formatka|wyliczenia|akty[\s_]+wykonawcze"
    r"|autopoprawka|\bopinia\b|materia[łl][\s_]+uzupe[łl]niaj"
)
# A ministry that files the bill as an attachment to its letter says which is which in a tag at
# the end of the name: "załącznik do pismo 07.08.2026 uzgodnienia [projekt].pdf". The tag is the
# one part of such a name that is about the document rather than about its envelope.
_APPENDIX_RE = re.compile(r"załącznik|zalacznik|zał\.")
_ROLE_TAG_RE = re.compile(r"\[\s*(projekt|uzasadnienie|osr)\s*\]")
_TAGGED_ROLE: dict[str, TextRole] = {
    "projekt": "bill",
    "uzasadnienie": "justification",
    "osr": "osr",
}
_BILL_RE = re.compile(r"projekt|ustaw")
_FORMAT_RANK = {"pdf": 0, "docx": 1, "docm": 2, "doc": 3, "odt": 4, "xml": 5, "zip": 8}


def text_role(name: str) -> TextRole | None:
    """What a file of a "Projekt" folder (or of a zip package) is, by its name: the bill, its
    uzasadnienie, the OSR, or None for what is not one. A name saying nothing is taken for the
    bill. An appendix is ruled out before the OSR is recognised, or "załącznik do OSR" would
    stand in the OSR's place; a tag in brackets beats everything, being the ministry saying so.
    """
    lowered = name.lower()
    tagged = _ROLE_TAG_RE.search(lowered)
    if tagged is not None:
        return _TAGGED_ROLE[tagged.group(1)]
    if "uzasad" in lowered:
        return "justification"
    if _NOT_A_TEXT_RE.search(lowered):
        return None
    if _OSR_RE.search(lowered):
        return "osr"
    return "bill"


def text_rank(name: str, extension: str) -> tuple[int, int]:
    """How good a file is as the text of its role: a name that calls itself the bill ("projekt
    ustawy") beats one that only fails to say what it is (a note, an information sheet), and PDF
    beats the Word formats, which beat a zip package."""
    return (0 if _BILL_RE.search(name.lower()) else 1, _FORMAT_RANK.get(extension, 9))


def _rank(document: RclDocument) -> tuple[int, int]:
    return text_rank(document.name, document.extension)


def _format_rank(document: RclDocument) -> int:
    return _rank(document)[1]


def parse_stage_label(text: str) -> tuple[int, str]:
    """`" 3. Konsultacje publiczne"` -> `(3, "Konsultacje publiczne")`."""
    match = _STAGE_NAME.match(text)
    if match is None:
        raise ValueError(f"not a stage label: {text!r}")
    return int(match.group(1)), " ".join(match.group(2).split())


def rcl_stages(project: RclProject) -> tuple[Stage, ...]:
    """Reached stages as generic `Stage` nodes, so that storage, diffing and the "new stages"
    block of an update work unchanged. The state and the modification date stay out of the
    fingerprint: uploads into a stage folder are not "news" by themselves."""
    return tuple(
        Stage(stage_name=f"{st.number}. {st.name}", stage_type=RCL_STAGE_TYPE, date=st.started)
        for st in project.reached_stages
    )


def process_summary(project: RclProject, *, term: int) -> ProcessSummary:
    """The `bills` row summary of an RCL project (no Sejm process exists yet).

    `change_date` is the project's last modification at midnight UTC: discovery refreshes it from
    the list, and tracking picks up bills changed since the last run by it.
    """
    documents = project.text_documents()
    dated = [d.created for d in documents.values() if d.created]
    about = "; ".join([*project.keywords, *project.departments])
    if project.eu_note:
        about = f"{about}. {project.eu_note}" if about else project.eu_note
    return ProcessSummary(
        term=term,
        number=project.number,
        title=project.title,
        description=about or None,
        document_type=BILL_DOCUMENT_TYPE,
        document_type_enum=DocumentType.BILL,
        process_start_date=project.created,
        document_date=max(dated) if dated else project.created,
        change_date=dt.datetime.combine(project.modified, dt.time(0, 0), tzinfo=dt.UTC),
        closure_date=project.modified if project.is_over else None,
        eu_related=project.eu_note is not None or (project.wykaz_number or "").startswith("UC"),
        rcl_num=project.rm_number,
        rcl_link=project.web_url,
        applicant=ApplicantType.GOVERNMENT,
    )


def rcl_fingerprint(project: RclProject) -> str:
    """What counts as a change worth an update: stages reached or activated, the folders that
    got their first documents (consultation opened, opinions published), a new text version,
    the project closed or sent to the Sejm."""
    payload = {
        "stages": [
            [st.number, st.state, sorted({f.kind for f in st.folders if f.documents})]
            for st in project.reached_stages
        ],
        "text": sorted(d.id for d in project.text_documents().values()),
        "status": project.status,
        "sejm": project.sent_to_sejm,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
