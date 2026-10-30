"""Physical observations commit with caller-owned artifact parents and recording time."""

import datetime as dt
from dataclasses import dataclass

from lexinform.physical_root_assembly import PhysicalAssemblyPacket
from lexinform.ports import BillRepository, DocumentPhysicalObserver
from lexinform.services.document_history import record_artifacts


@dataclass(frozen=True)
class PhysicalHistoryPersistence:
    observer: DocumentPhysicalObserver

    def persist_source_history(self, repo: BillRepository, *, recorded_at: dt.datetime) -> None:
        with repo.atomic():
            for entry in self.observer.read_physical_observations():
                observation = entry.observation
                packet = observation.assembly
                artifacts = (
                    packet.artifacts
                    if isinstance(packet, PhysicalAssemblyPacket)
                    else ((observation.artifact,) if observation.artifact is not None else ())
                )
                record_artifacts(repo, artifacts, recorded_at=recorded_at)
                if observation.extraction is not None:
                    if (
                        observation.artifact is None
                        or observation.extraction.artifact_sha256 != observation.artifact.sha256
                    ):
                        raise ValueError("physical extraction must belong to its actual root")
                    repo.save_extraction_result(observation.extraction, recorded_at=recorded_at)
                if isinstance(packet, PhysicalAssemblyPacket):
                    if packet.capture.extraction_id is not None:
                        result = repo.get_extraction_result(packet.capture.extraction_id)
                        if (
                            result is None
                            or result.artifact_sha256 != packet.capture.artifact_sha256
                        ):
                            raise ValueError(
                                "physical capture requires its exact extraction parent"
                            )
                    repo.save_physical_inventory(
                        packet.metadata, (packet.capture,), recorded_at=recorded_at
                    )
