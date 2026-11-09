from dataclasses import dataclass

from lexinform.models.sejm import TextDocument


@dataclass(frozen=True)
class CandidateObservation[Candidate]:
    slot: int
    candidate: Candidate
    official: TextDocument | None = None
    archive_ordinals: tuple[int, ...] = ()
