"""Reading an RCL project: timeline, the catalogs of its stages, the consultation letter.

Shared by RCL discovery (first sight) and RCL tracking (refresh). Network only, no repository.
Every page takes seconds to render, so callers read only what they need: the timeline to decide
whether a project matters, the catalogs of a candidate, the newest text of a title miss.
"""

import datetime as dt
import logging
from contextlib import suppress
from typing import Literal

from lexinform import rcl_letters as letter_parser
from lexinform.errors import ServiceUnavailableError
from lexinform.models import RclConsultation, RclDocument, RclProject, RclStage
from lexinform.ports import RclGateway, RclLetterUseObserver
from lexinform.rcl_letters import LETTER_PARSER, LetterInfo, deadline_of, parse_letter
from lexinform.services.documents import LoadedFile, TextLoader
from lexinform.services.rcl_letter_history import rcl_letter_observation

log = logging.getLogger(__name__)

# Over the 796 corpus projects with a readable "Projekt" folder, the newest reached stage has it
# for 209 (26%), the one below brings it to 99.25%, a third to 99.75%.
TEXT_STAGE_ATTEMPTS = 3


class RclProjectReader:
    def __init__(
        self, rcl: RclGateway, loader: TextLoader, *, observer: RclLetterUseObserver | None = None
    ) -> None:
        self._rcl = rcl
        self._loader = loader
        self._observer = observer

    def timeline(self, project_id: int) -> RclProject:
        """The project page alone: metadata and stage states, no folders."""
        return self._rcl.get_project(project_id)

    def complete(self, project: RclProject) -> RclProject:
        """Every reached stage's catalog and the consultation letter."""
        for stage in project.reached_stages:
            project = project.with_stage(self._rcl.get_stage(project.id, stage.id))
        return project.model_copy(update={"consultation": self.consultation(project)})

    def with_text(self, project: RclProject) -> RclProject:
        """The newest stage that carries the bill text, reading at most `TEXT_STAGE_ATTEMPTS`
        catalogs backwards and stopping at the first that has it.

        The stages that work on a project republish the current text in their own "Projekt"
        folder; the one it ends on does not — the hand-over to the Sejm carries the covering
        letter to the Marshal and nothing else. A page takes some ten seconds, which is why this
        is a few catalogs and not the whole timeline (`complete`)."""
        for stage in list(reversed(project.reached_stages))[:TEXT_STAGE_ATTEMPTS]:
            read = self._rcl.get_stage(project.id, stage.id)
            project = project.with_stage(read)
            if any(d.readable for d in project.stage_documents(read)):
                break
        return project

    def with_consultation(self, project: RclProject) -> RclProject:
        """The consultation stage's catalog and the letter in it, for a project read for its
        newest text alone (`with_text`).

        One page, and it is the page with the two facts the card is written around: the day the
        window shuts and the address remarks are sent to.
        """
        stage = project.consultation_stage
        if stage is None:
            return project
        project = project.with_stage(self._rcl.get_stage(project.id, stage.id))
        return project.model_copy(update={"consultation": self.consultation(project)})

    def refresh(self, stored: RclProject) -> RclProject:
        """The timeline again, and only the catalogs whose stage changed since `stored`."""
        project = self._rcl.get_project(stored.id)
        known = {st.id: st for st in stored.stages}
        for stage in project.reached_stages:
            before = known.get(stage.id)
            # `catalog_read` and not `folders`: a quarter of the reached stages of the corpus
            # publish nothing at all, so an empty stage says nothing about whether it was opened.
            # A project read for its newest text alone kept every other catalog closed for the
            # life of the row — the consultation letter among them — because this branch took the
            # timeline's own copy for a reading of the catalog.
            if (
                before is not None
                and before.catalog_read
                and (not stage.is_review_body or before.loose_documents is not None)
                and before.modified == stage.modified
            ):
                project = project.with_stage(before)
            else:
                project = project.with_stage(self._rcl.get_stage(stored.id, stage.id))
        consultation = self.consultation(project, known=stored.consultation)
        return project.model_copy(
            update={
                "consultation": consultation,
                "consultation_attempts": stored.consultation_attempts,
                "print_number": stored.print_number,
                "rm_number": project.rm_number or stored.rm_number,
            }
        )

    def consultation(
        self, project: RclProject, *, known: RclConsultation | None = None
    ) -> RclConsultation | None:
        """What the consultation stage shows; each letter is read once (`known` keeps its data).

        A letter replaces the window only with a deadline, the later one winning (an extension);
        a closing notice with neither date nor address must not erase the window already read.
        """
        stage = project.consultation_stage
        if stage is None or not any(f.documents for f in stage.folders):
            return None
        facts = {
            "positions": len(stage.documents("positions")),
            "response_published": bool(stage.documents("response")),
        }
        read = set(known.letters_read) if known is not None else set()
        if known is not None and known.letter_url is not None:
            read.add(known.letter_url)
        if known is not None and known.deadline is None and known.letter_parser < LETTER_PARSER:
            read = set()
        unread = [d for d in stage.consultation_letters() if d.url not in read]
        window = known
        for letter in unread:
            window = self._read_window(letter, stage, project, window)
            read.add(letter.url)
        if window is None:
            return RclConsultation(**facts)
        if unread and window.deadline is None:
            log.warning("consultation letters of %s give no deadline this run can use", project.id)
        return window.model_copy(
            update={**facts, "letters_read": tuple(sorted(read)), "letter_parser": LETTER_PARSER}
        )

    def _read_window(
        self,
        letter: RclDocument,
        stage: RclStage,
        project: RclProject,
        before: RclConsultation | None,
    ) -> RclConsultation:
        url = letter.url
        loaded: LoadedFile | None = None
        text: str | None = None
        parser_called = False
        info: LetterInfo | None = None
        published: dt.date | None = None
        published_source: Literal[
            "letter.created", "stage.modified", "project.modified", "not_used"
        ] = "not_used"
        result: RclConsultation | None = None
        after: RclConsultation | None = None
        error: BaseException | None = None
        try:
            try:
                loaded = self._loader.read(url)
                text = loaded.text
            except ServiceUnavailableError:
                raise
            except Exception as exc:
                error = exc
                log.warning("consultation letter %s unreadable: %s", url, exc)
            if text is None:
                result = RclConsultation(letter_url=url)
            else:
                parser_called = True
                info = parse_letter(text)
                published = letter.created or stage.modified or project.modified
                published_source = (
                    "letter.created"
                    if letter.created
                    else "stage.modified"
                    if stage.modified
                    else "project.modified"
                )
                deadline = deadline_of(info, published=published)
                result = RclConsultation(
                    letter_url=url,
                    letter_date=info.letter_date,
                    days=info.days,
                    deadline=deadline,
                    email=info.email,
                )
            after = _better_window(before, result)
            return after
        except BaseException as exc:
            error = exc
            raise
        finally:
            if self._observer is not None:
                try:
                    origins = (
                        self._loader.file_origins(url, loaded)
                        if loaded is not None
                        else (None, None, None)
                    )
                    observation = rcl_letter_observation(
                        project,
                        stage,
                        letter,
                        before=before,
                        result=result,
                        after=after,
                        parser_text=text if parser_called else None,
                        info=info,
                        published=published,
                        published_source=published_source,
                        origins=origins,
                        error=error,
                        parser_source=letter_parser.__file__,
                        merge_source=__file__,
                    )
                    self._observer.observe_rcl_letter_use(observation)
                except BaseException as exc:
                    with suppress(BaseException):
                        log.warning("RCL letter-use capture failed (%s)", type(exc).__name__[:128])


def _better_window(known: RclConsultation | None, read: RclConsultation) -> RclConsultation:
    """The window after one more letter: a later deadline wins, and an address is never lost."""
    if known is None:
        return read
    if read.deadline is not None and (known.deadline is None or read.deadline > known.deadline):
        return read.model_copy(update={"email": read.email or known.email})
    return known.model_copy(update={"email": known.email or read.email})
