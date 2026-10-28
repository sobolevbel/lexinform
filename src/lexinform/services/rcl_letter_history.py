"""Optional RCL history collection is clock-free; only an explicit caller writes its snapshot."""

import datetime as dt
from threading import Lock

from lexinform.models import RclLetterUseObservation
from lexinform.ports import BillRepository
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
