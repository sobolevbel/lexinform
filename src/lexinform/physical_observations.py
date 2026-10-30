"""Bounded ordered physical bundles survive worker failures without owning persistence or policy."""

from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, suppress
from dataclasses import dataclass
from threading import Lock

from lexinform.models.artifacts import ArtifactIdentity
from lexinform.models.document_reads import DocumentPhysicalDiagnostics
from lexinform.models.extractions import ExtractionResult
from lexinform.observation_order import ObservationOrder
from lexinform.physical_root_assembly import PhysicalAssemblyPacket
from lexinform.physical_walk_capture import (
    PhysicalWalkCapture,
    PhysicalWalkObservation,
    physical_error,
    suspend_physical_walk_capture,
)

MAX_PENDING_PHYSICAL_BYTES = 16 * 1024 * 1024
MAX_PENDING_PHYSICAL_OBSERVATIONS = 256


@dataclass(frozen=True)
class OrderedPhysicalObservation:
    order: tuple[int, ...]
    observation: PhysicalWalkObservation
    reused: bool = False


@dataclass
class PhysicalInvocation:
    artifact: ArtifactIdentity
    capture: PhysicalWalkCapture | None = None
    observation: PhysicalWalkObservation | None = None
    extraction: ExtractionResult | None = None

    def bind_extraction(self, result: ExtractionResult) -> None:
        if result.artifact_sha256 != self.artifact.sha256:
            return
        self.extraction = result
        if self.capture is not None:
            try:
                self.capture.extraction(result)
            except BaseException as error:
                self.capture.unavailable("extraction_observation_unavailable", error)


class PhysicalObservationCollector:
    def __init__(
        self,
        ordering: ObservationOrder,
        factory: Callable[[ArtifactIdentity], PhysicalWalkCapture] = PhysicalWalkCapture,
    ) -> None:
        self.ordering = ordering
        self._factory = factory
        self._lock = Lock()
        self._pending: dict[tuple[int, ...], tuple[OrderedPhysicalObservation, int]] = {}
        self._bytes = 0
        self._cutoff: tuple[int, ...] | None = None
        self._counts: Counter[str] = Counter()
        self._reasons: Counter[str] = Counter()

    def reset(self) -> None:
        with self._lock:
            self._pending.clear()
            self._bytes = 0
            self._cutoff = None
            self._counts.clear()
            self._reasons.clear()

    def _reason(self, value: str) -> None:
        reason = (
            value
            if len(value) <= 64 and value.isascii() and value.replace("_", "").isalnum()
            else "invalid_observation_reason"
        )
        if reason not in self._reasons and len(self._reasons) >= 7:
            reason = "other"
        self._reasons[reason] += 1

    def note_failure(self, reason: str) -> None:
        with suppress(BaseException), self._lock:
            self._counts["observer_failures"] += 1
            self._reason(reason)

    @contextmanager
    def invocation(self, artifact: ArtifactIdentity) -> Iterator[PhysicalInvocation]:
        slot = PhysicalInvocation(artifact)
        order = None
        epoch = self.ordering.epoch
        manager: AbstractContextManager[PhysicalWalkCapture] | None = None
        failure = None
        try:
            order = self.ordering.next()
            with self._lock:
                self._counts["invocations"] += 1
            capture = self._factory(artifact)
            candidate = capture.scope()
            candidate.__enter__()
            manager = candidate
            slot.capture = capture
        except BaseException as error:
            failure = physical_error(error)
        body_error: BaseException | None = None
        try:
            if manager is None:
                with suspend_physical_walk_capture():
                    yield slot
            else:
                yield slot
        except BaseException as error:
            body_error = error
            raise
        finally:
            try:
                if manager is not None:
                    try:
                        manager.__exit__(
                            type(body_error) if body_error is not None else None,
                            body_error,
                            body_error.__traceback__ if body_error is not None else None,
                        )
                    except BaseException as error:
                        failure = physical_error(error)
                observation = slot.capture.observation if slot.capture is not None else None
                slot.observation = observation or PhysicalWalkObservation(
                    artifact,
                    None,
                    "invocation_scope_unavailable",
                    failure,
                    extraction=slot.extraction,
                )
                if failure is not None:
                    self.note_failure("invocation_cleanup_unavailable")
                if epoch == self.ordering.epoch and order is not None:
                    self._retain(order, slot.observation)
                else:
                    self.note_failure("invocation_order_unavailable")
            except BaseException as error:
                if slot.observation is None:
                    slot.observation = PhysicalWalkObservation(
                        artifact,
                        None,
                        "invocation_transport_unavailable",
                        physical_error(error),
                        extraction=slot.extraction,
                    )
                self.note_failure("invocation_transport_unavailable")

    def cached(self, observation: PhysicalWalkObservation | None) -> None:
        order = self.ordering.next()
        with self._lock:
            self._counts["cache_reuses"] += 1
            if observation is None:
                self._reason("cached_observation_unavailable")
                self._counts["unavailable"] += 1
                return
        self._retain(order, observation, reused=True)

    def _retain(
        self,
        order: tuple[int, ...],
        observation: PhysicalWalkObservation,
        *,
        reused: bool = False,
    ) -> None:
        packet = observation.assembly
        size = 1024
        if isinstance(packet, PhysicalAssemblyPacket):
            size += sum(row.budget_bytes for row in packet.metadata) + packet.capture.budget_bytes
            size += sum(len(body.model_dump_json().encode()) for body in packet.artifacts)
        if observation.draft is not None:
            size += 1_048_576
        if observation.extraction is not None:
            size += len(observation.extraction.model_dump_json().encode())
        with self._lock:
            if reused:
                matches = [
                    key
                    for key, (entry, _) in self._pending.items()
                    if entry.observation is observation
                ]
                if matches:
                    first = min(matches)
                    if first <= order:
                        return
                    for key in matches:
                        _, previous_size = self._pending.pop(key)
                        self._bytes -= previous_size
            if reused and not isinstance(packet, PhysicalAssemblyPacket):
                self._counts["unavailable"] += 1
                self._reason(observation.unavailable or "assembly_unavailable")
            if not reused:
                if isinstance(packet, PhysicalAssemblyPacket):
                    self._counts["captured"] += 1
                    self._counts["partial"] += any(
                        row.coverage == "partial" for row in packet.metadata
                    )
                else:
                    self._counts["unavailable"] += 1
                    self._reason(observation.unavailable or "assembly_unavailable")
            if self._cutoff is not None and order >= self._cutoff:
                self._counts["dropped"] += 1
                return
            self._pending[order] = OrderedPhysicalObservation(order, observation, reused), size
            self._bytes += size
            while (
                self._bytes > MAX_PENDING_PHYSICAL_BYTES
                or len(self._pending) > MAX_PENDING_PHYSICAL_OBSERVATIONS
            ):
                removed = max(self._pending)
                _, removed_size = self._pending.pop(removed)
                self._bytes -= removed_size
                self._cutoff = removed
                self._counts["dropped"] += 1

    def snapshot(self) -> tuple[OrderedPhysicalObservation, ...]:
        with self._lock:
            return tuple(self._pending[key][0] for key in sorted(self._pending))

    def diagnostics(self) -> DocumentPhysicalDiagnostics | None:
        with self._lock:
            if not self._counts:
                return None
            return DocumentPhysicalDiagnostics(
                invocations=self._counts["invocations"],
                cache_reuses=self._counts["cache_reuses"],
                captured=self._counts["captured"],
                partial=self._counts["partial"],
                unavailable=self._counts["unavailable"],
                dropped=self._counts["dropped"],
                observer_failures=self._counts["observer_failures"],
                unknown_remainders=bool(
                    self._counts["dropped"] or self._counts["observer_failures"]
                ),
                reasons=dict(self._reasons),
            )
