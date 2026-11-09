from collections.abc import Sequence
from dataclasses import dataclass, replace

from lexinform.document_candidates import CandidateSet, DocumentCandidate
from lexinform.models.candidate_observation import CandidateObservation
from lexinform.models.documents import DocumentPart
from lexinform.models.sejm import TextDocument


def with_candidate_read(
    observation: CandidateObservation[DocumentCandidate],
    text: str | None,
    parts: tuple[DocumentPart, ...],
    observed_parts: tuple[DocumentPart, ...],
) -> CandidateObservation[DocumentCandidate]:
    return replace(
        observation,
        candidate=replace(
            observation.candidate, text=text, parts=parts, observed_parts=observed_parts
        ),
    )


@dataclass(frozen=True)
class CandidateObservations:
    entries: tuple[CandidateObservation[DocumentCandidate], ...]

    @classmethod
    def from_sources(cls, requested: TextDocument) -> CandidateObservations:
        return cls(
            tuple(
                CandidateObservation(index, DocumentCandidate(source.url, None), source)
                for index, source in enumerate((requested, *requested.alternatives))
            )
        )

    def official_sources(self) -> tuple[TextDocument, ...]:
        return tuple(item.official for item in self.entries if item.official is not None)

    def comparison(self, slots: Sequence[int]) -> CandidateSet:
        entries = {item.slot: item.candidate for item in self.entries}
        return CandidateSet(tuple(entries[slot] for slot in slots))
