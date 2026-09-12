"""Government bills before the Sejm: the Rządowy Proces Legislacyjny (legislacja.rcl.gov.pl).

A project page lists up to 14 stages (uzgodnienia, konsultacje publiczne, opiniowanie, the
committees of the Council of Ministers, Komisja Prawnicza, Rada Ministrów, skierowanie do Sejmu);
each reached stage has a catalog of folders with documents. Nothing here does I/O: the HTML is
turned into these models by `adapters/rcl_html.py`.
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
RCL_STAGE_TYPE = "RclStage"  # `Stage.stage_type` of an RCL stage stored in `Bill.stages`
READABLE_EXTENSIONS = frozenset({"pdf", "docx", "docm", "doc", "odt", "zip"})
OPEN_STATUS = "otwarty"

StageState = Literal["not_started", "reached", "active"]
FolderKind = Literal["project", "letters", "positions", "response", "conference", "other"]
TextRole = Literal["bill", "justification", "osr"]
# Where in the government path a stage sits: consultations and opinions, the committees of the
# Council of Ministers (incl. Komisja Prawnicza), the Council itself, the hand-over to the Sejm.
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
    """One node of the project timeline; `folders` are known only after the catalog was read."""

    model_config = ConfigDict(frozen=True)

    id: int
    number: int
    name: str
    state: StageState = "not_started"
    started: dt.date | None = None
    ended: dt.date | None = None
    modified: dt.date | None = None
    folders: tuple[RclFolder, ...] = ()

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


class RclConsultation(BaseModel):
    """What the consultation letter (pismo kierujące) told us, plus what the folders show."""

    model_config = ConfigDict(frozen=True)

    letter_url: str | None = None
    letter_date: dt.date | None = None
    days: int | None = None
    deadline: dt.date | None = None
    email: str | None = None
    positions: int = 0  # opinions published in "Stanowiska zgłoszone"
    response_published: bool = False  # "Odniesienie się wnioskodawcy do uwag" has documents

    def is_open(self, today: dt.date) -> bool:
        return self.deadline is not None and self.deadline >= today

    @property
    def results_published(self) -> bool:
        return self.positions > 0 or self.response_published


class RclProjectSummary(BaseModel):
    """One row of the project list."""

    model_config = ConfigDict(frozen=True)

    id: int
    title: str
    applicant: str
    wykaz_number: str | None = None
    created: dt.date
    modified: dt.date

    @property
    def web_url(self) -> str:
        return project_web_url(self.id)


class RclProject(BaseModel):
    """A project page with its timeline; stage folders are filled in for reached stages."""

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
    term_label: str | None = None  # "X" for the 10th Sejm term
    rm_number: str | None = None  # RM-0610-139-26, shown once the bill went to the Sejm
    sejm_url: str | None = None
    print_number: str | None = None  # set by Sejm discovery when the druk appears
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

    def with_stage(self, stage: RclStage) -> "RclProject":
        """A copy with one timeline node replaced by its catalog version."""
        stages = tuple(stage if st.id == stage.id else st for st in self.stages)
        return self.model_copy(update={"stages": stages})

    def without_documents(self) -> "RclProject":
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
            picked[role] = min(candidates, key=lambda d: (_name_rank(d), _format_rank(d)))
        else:
            picked[role] = min(candidates, key=_format_rank)
    return picked


_OSR_RE = re.compile(r"(?:^|[^a-ząćęłńóśźż])osr(?:$|[^a-ząćęłńóśźż])|ocena skutk")
_NOT_A_TEXT_RE = re.compile(r"tabel|zgodno|załącznik|zalacznik|pismo|rozdzielnik")
_BILL_RE = re.compile(r"projekt|ustaw")


def text_role(name: str) -> TextRole | None:
    """What a file of a "Projekt" folder (or of a zip package) is, by its name: the bill, its
    uzasadnienie, the OSR, or None for what is not a bill text (compliance tables, letters,
    appendices). A name that says nothing is taken for the bill."""
    lowered = name.lower()
    if "uzasad" in lowered:
        return "justification"
    if _OSR_RE.search(lowered):
        return "osr"
    if _NOT_A_TEXT_RE.search(lowered):
        return None
    return "bill"


def _name_rank(document: RclDocument) -> int:
    """A file that calls itself the bill ("projekt ustawy") beats one that only fails to say
    what it is (a note, an information sheet) whatever their formats."""
    return 0 if _BILL_RE.search(document.name.lower()) else 1


def _format_rank(document: RclDocument) -> int:
    """PDF first, then the Word formats, then ODT; a zip package only when nothing else is there."""
    return {"pdf": 0, "docx": 1, "docm": 2, "doc": 3, "odt": 4, "zip": 8}.get(document.extension, 9)


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
