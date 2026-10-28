"""Optional RCL history collection is clock-free; only an explicit caller writes its snapshot."""

import datetime as dt
import hashlib
import platform
from dataclasses import asdict, dataclass
from threading import Lock
from typing import Literal

from lexinform.extraction_records import component_code
from lexinform.models import (
    ArtifactIdentity,
    ArtifactReadIdentity,
    ExtractionResult,
    RclConsultation,
    RclDocument,
    RclLetterUse,
    RclLetterUseObservation,
    RclProject,
    RclStage,
)
from lexinform.models.rcl_letter_use import (
    RclLetterComponents,
    RclLetterDateArgument,
    RclLetterDescriptor,
    RclLetterInfo,
    RclLetterInput,
    RclLetterOfficial,
    RclLetterPhysical,
    RclLetterUrl,
    RclLetterUsePayload,
    RclLetterWindow,
    RclLetterWindowFact,
    rcl_letter_sha256,
)
from lexinform.ports import BillRepository, RclLetterUseObserver
from lexinform.rcl_letters import LETTER_PARSER, LetterInfo
from lexinform.services.document_history import record_artifact_reads, record_artifacts


class RclLetterUseCollector:
    def __init__(self) -> None:
        self._lock = Lock()
        self._uses: dict[str, RclLetterUseObservation] = {}

    def observe_rcl_letter_use(self, observation: RclLetterUseObservation) -> None:
        observation = RclLetterUseObservation.model_validate_json(observation.model_dump_json())
        with self._lock:
            previous = self._uses.setdefault(observation.use.use_id, observation)
            if previous != observation:
                raise ValueError("immutable letter-use observations cannot change their parents")

    def read_rcl_letter_uses(self) -> tuple[RclLetterUseObservation, ...]:
        with self._lock:
            return tuple(self._uses[key] for key in sorted(self._uses))


@dataclass(frozen=True)
class RclLetterHistoryPersistence:
    observer: RclLetterUseObserver

    def persist_source_history(self, repo: BillRepository, *, recorded_at: dt.datetime) -> None:
        record_rcl_letter_uses(repo, self.observer.read_rcl_letter_uses(), recorded_at=recorded_at)


def _window_fact(window: RclConsultation | None) -> RclLetterWindowFact | None:
    if window is None:
        return None
    values = window.model_dump(
        mode="json", include={"letter_url", "letter_date", "days", "deadline", "email"}
    )
    projection = RclLetterWindow.model_validate(
        {
            **values,
            "letter_url": RclLetterUrl.of(window.letter_url)
            if window.letter_url is not None
            else None,
        }
    )
    return RclLetterWindowFact(
        projection=projection, canonical_projection_sha256=rcl_letter_sha256(values)
    )


def rcl_letter_observation(
    project: RclProject,
    stage: RclStage,
    letter: RclDocument,
    *,
    before: RclConsultation | None,
    result: RclConsultation | None,
    after: RclConsultation | None,
    parser_text: str | None,
    info: LetterInfo | None,
    published: dt.date | None,
    published_source: Literal["letter.created", "stage.modified", "project.modified", "not_used"],
    origins: tuple[ArtifactIdentity | None, ArtifactReadIdentity | None, ExtractionResult | None],
    error: BaseException | None,
    parser_source: str,
    merge_source: str,
) -> RclLetterUseObservation:
    url = RclLetterUrl.of(letter.url)
    artifact, read, extraction = origins
    if url.state != "complete":
        read = None
    input = (
        RclLetterInput(
            sha256=hashlib.sha256(parser_text.encode()).hexdigest(),
            chars=len(parser_text),
            utf8_bytes=len(parser_text.encode()),
        )
        if parser_text is not None
        else None
    )
    exact = (
        input is not None
        and extraction is not None
        and input.sha256 == extraction.output_text_sha256
        and input.chars == extraction.output_chars
    )
    physical = RclLetterPhysical(
        state="known_complete_body" if artifact is not None else "unknown_origin",
        artifact_sha256=artifact.sha256 if artifact else None,
        artifact_bytes=artifact.byte_size if artifact else None,
        artifact_read_id=read.read_id if read else None,
        extraction_id=extraction.result_id if extraction else None,
        input_extraction_relation="exact_output_text_sha256" if exact else "unknown",
    )
    outcome: Literal["returned", "no_text", "read_error", "raised"] = (
        "raised"
        if after is None
        else "read_error"
        if error is not None
        else "returned"
        if input is not None
        else "no_text"
    )
    payload = RclLetterUsePayload(
        official=RclLetterOfficial(
            project_id=project.id,
            stage_id=stage.id,
            stage_number=stage.number,
            letter_id=letter.id,
            letter=RclLetterDescriptor(name=letter.name, url=url, created=letter.created),
        ),
        date_argument=RclLetterDateArgument(value=published, source=published_source),
        physical=physical,
        input=input,
        components=RclLetterComponents(
            letter_parser=LETTER_PARSER,
            parser_sha256=component_code(parser_source),
            merge_sha256=component_code(merge_source),
            python=platform.python_version(),
        ),
        letter_info=RclLetterInfo(**asdict(info)) if info is not None else None,
        result=_window_fact(result),
        before=_window_fact(before),
        after=_window_fact(after),
        outcome=outcome,
        error_class=type(error).__name__ if error is not None else None,
    )
    return RclLetterUseObservation(
        use=RclLetterUse.of(payload), artifact=artifact, read=read, extraction=extraction
    )


def record_rcl_letter_uses(
    repo: BillRepository,
    observations: tuple[RclLetterUseObservation, ...],
    *,
    recorded_at: dt.datetime,
) -> None:
    """Persist matching parents before each use inside the caller's existing transaction."""
    for observation in observations:
        observation = RclLetterUseObservation.model_validate_json(observation.model_dump_json())
        if observation.artifact is not None:
            record_artifacts(repo, (observation.artifact,), recorded_at=recorded_at)
        if observation.read is not None:
            record_artifact_reads(repo, (observation.read,), recorded_at=recorded_at)
        if observation.extraction is not None:
            repo.save_extraction_result(observation.extraction, recorded_at=recorded_at)
        repo.save_rcl_letter_use(observation.use, recorded_at=recorded_at)
