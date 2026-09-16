"""Reading an RCL project: timeline, the catalogs of its stages, the consultation letter.

Shared by RCL discovery (first sight) and RCL tracking (refresh). Network only, no repository.
Every page takes seconds to render, so callers read only what they need: the timeline to decide
whether a project matters, the catalogs of a candidate, the newest text of a title miss.
"""

import logging

from lexinform.errors import ServiceUnavailableError
from lexinform.models import RclConsultation, RclDocument, RclProject, RclStage
from lexinform.ports import RclGateway
from lexinform.rcl_letters import deadline_of, parse_letter
from lexinform.services.documents import TextLoader

log = logging.getLogger(__name__)

# Over the 796 corpus projects with a readable "Projekt" folder, the newest reached stage has it
# for 209 (26%), the one below brings it to 99.25%, a third to 99.75%.
TEXT_STAGE_ATTEMPTS = 3


class RclProjectReader:
    def __init__(self, rcl: RclGateway, loader: TextLoader) -> None:
        self._rcl = rcl
        self._loader = loader

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
            if any(d.readable for d in read.documents("project")):
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
            if before is not None and before.catalog_read and before.modified == stage.modified:
                project = project.with_stage(before)
            else:
                project = project.with_stage(self._rcl.get_stage(stored.id, stage.id))
        consultation = self.consultation(project, known=stored.consultation)
        return project.model_copy(
            update={
                "consultation": consultation,
                "consultation_attempts": stored.consultation_attempts,
                "print_number": stored.print_number,
            }
        )

    def consultation(
        self, project: RclProject, *, known: RclConsultation | None = None
    ) -> RclConsultation | None:
        """What the consultation stage shows; the letter is read once (`known` keeps its data).

        A letter whose deadline the parser cannot read is logged for the operator's channel:
        without a date the card can only say "срок в письме" and no reminder is ever due.
        """
        stage = project.consultation_stage
        if stage is None or not any(f.documents for f in stage.folders):
            return None
        letter = stage.consultation_letter()
        facts = {
            "positions": len(stage.documents("positions")),
            "response_published": bool(stage.documents("response")),
        }
        if known is not None and known.letter_url == (letter.url if letter else None):
            return known.model_copy(update=facts)
        if letter is None:
            return RclConsultation(**facts)
        return self._read_letter(letter, stage, project).model_copy(update=facts)

    def _read_letter(
        self, letter: RclDocument, stage: RclStage, project: RclProject
    ) -> RclConsultation:
        url = letter.url
        try:
            text = self._loader.load(url)
        except ServiceUnavailableError:
            raise
        except Exception as exc:
            log.warning("consultation letter %s unreadable: %s", url, exc)
            text = None
        if text is None:
            return RclConsultation(letter_url=url)
        info = parse_letter(text)
        published = letter.created or stage.modified or project.modified
        deadline = deadline_of(info, published=published)
        if deadline is None:
            log.warning("consultation letter %s gives no deadline this run can use", url)
        return RclConsultation(
            letter_url=url,
            letter_date=info.letter_date,
            days=info.days,
            deadline=deadline,
            email=info.email,
        )
