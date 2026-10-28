"""Optional Senate inspection collection stays clock-free until an explicit caller writes it."""

import datetime as dt
from dataclasses import dataclass, replace
from threading import Lock

from lexinform.models import ArtifactIdentity
from lexinform.models.senate_inspection import SenateInspectionLink, SenateInspectionRecord
from lexinform.ports import BillRepository
from lexinform.services.document_history import (
    DocumentSourceRead,
    record_artifacts,
    record_source_read,
)


@dataclass(frozen=True)
class SenateInspectionObservation:
    record: SenateInspectionRecord
    links: tuple[SenateInspectionLink, ...] = ()
    sources: tuple[DocumentSourceRead, ...] = ()

    def validated(self) -> SenateInspectionObservation:
        record = SenateInspectionRecord.model_validate_json(
            self.record.model_dump_json(exclude={"recorded_at"})
        )
        links = tuple(
            SenateInspectionLink.model_validate_json(link.model_dump_json()) for link in self.links
        )
        record.verify_links(links)
        source_ids = {source.selection.selection_id for source in self.sources}
        if len(source_ids) != len(self.sources) or source_ids != {
            link.selection_id for link in links if link.selection_id is not None
        }:
            raise ValueError("Senate source snapshots must exactly cover their linked selections")
        return replace(self, record=record, links=links)


class SenateInspectionCollector:
    def __init__(self) -> None:
        self._lock = Lock()
        self._records: dict[str, SenateInspectionObservation] = {}

    def observe(self, observation: SenateInspectionObservation) -> None:
        observation = observation.validated()
        with self._lock:
            previous = self._records.setdefault(observation.record.inspection_id, observation)
            if previous != observation:
                raise ValueError("immutable Senate inspection observations cannot change")

    def snapshot(self) -> tuple[SenateInspectionObservation, ...]:
        with self._lock:
            return tuple(self._records[key] for key in sorted(self._records))


def record_senate_inspections(
    repo: BillRepository,
    observations: tuple[SenateInspectionObservation, ...],
    *,
    recorded_at: dt.datetime,
) -> None:
    """The caller records ordered inspection facts and their exact physical graphs atomically."""
    with repo.atomic():
        for observation in observations:
            observation = observation.validated()
            record_artifacts(
                repo,
                tuple(
                    ArtifactIdentity(sha256=link.artifact_sha256, byte_size=link.artifact_bytes)
                    for link in observation.links
                ),
                recorded_at=recorded_at,
            )
            for source in observation.sources:
                record_source_read(
                    repo,
                    source,
                    term=observation.record.owner.term,
                    number=observation.record.owner.number,
                    purpose="analysis",
                    recorded_at=recorded_at,
                )
            repo.save_senate_inspection(
                observation.record, observation.links, recorded_at=recorded_at
            )
