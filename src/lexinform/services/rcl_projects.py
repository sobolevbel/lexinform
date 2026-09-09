"""Reading a whole RCL project: timeline, the catalogs of reached stages, the consultation letter.

Shared by RCL discovery (first sight) and RCL tracking (refresh). Network only, no repository.
"""

import logging

from lexinform.errors import ServiceUnavailableError
from lexinform.models import RclConsultation, RclProject, RclStage
from lexinform.ports import RclGateway
from lexinform.rcl_letters import deadline_of, parse_letter
from lexinform.services.documents import TextLoader

log = logging.getLogger(__name__)


class RclProjectReader:
    """Fetches a project with the folders of its reached stages and reads the consultation letter
    for the deadline and the address for comments."""

    def __init__(self, rcl: RclGateway, loader: TextLoader) -> None:
        self._rcl = rcl
        self._loader = loader

    def read(self, project_id: int) -> RclProject:
        project = self._rcl.get_project(project_id)
        for stage in project.reached_stages:
            project = project.with_stage(self._rcl.get_stage(project_id, stage.id))
        return project.model_copy(update={"consultation": self.consultation(project)})

    def refresh(self, stored: RclProject) -> RclProject:
        """Re-read the timeline and only the catalogs whose stage changed since `stored`
        (every project page takes seconds; most stages do not move between runs)."""
        project = self._rcl.get_project(stored.id)
        known = {st.id: st for st in stored.stages}
        for stage in project.reached_stages:
            before = known.get(stage.id)
            if before is not None and before.reached and before.modified == stage.modified:
                project = project.with_stage(before)
            else:
                project = project.with_stage(self._rcl.get_stage(stored.id, stage.id))
        consultation = self.consultation(project, known=stored.consultation)
        return project.model_copy(
            update={"consultation": consultation, "print_number": stored.print_number}
        )

    def consultation(
        self, project: RclProject, *, known: RclConsultation | None = None
    ) -> RclConsultation | None:
        """What the consultation stage shows; the letter is read once (`known` keeps its data)."""
        stage = project.consultation_stage
        if stage is None or not any(f.documents for f in stage.folders):
            return None
        letter = next((d for d in stage.documents("letters") if d.readable), None)
        facts = {
            "positions": len(stage.documents("positions")),
            "response_published": bool(stage.documents("response")),
        }
        if known is not None and known.letter_url == (letter.url if letter else None):
            return known.model_copy(update=facts)
        if letter is None:
            return RclConsultation(**facts)
        return self._read_letter(letter.url, stage, project).model_copy(update=facts)

    def _read_letter(self, url: str, stage: RclStage, project: RclProject) -> RclConsultation:
        letter = next(d for d in stage.documents("letters") if d.url == url)
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
        return RclConsultation(
            letter_url=url,
            letter_date=info.letter_date,
            days=info.days,
            deadline=deadline_of(info, published=published),
            email=info.email,
        )
