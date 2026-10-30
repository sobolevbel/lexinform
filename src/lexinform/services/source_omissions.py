"""Invocation-scoped omission collectors leave all persistence and enrollment checks to callers."""

import datetime as dt
import logging
import threading
from collections.abc import Callable, Iterable, Iterator
from contextlib import ExitStack, contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from threading import Lock
from typing import Literal

from lexinform.metadata_omissions import MetadataUrlOmissionCollector
from lexinform.metadata_urls import MetadataUrlDecision
from lexinform.models import ArtifactIdentity, ArtifactReadIdentity, ExtractionResult
from lexinform.models.metadata_omissions import MetadataUrlOmissions
from lexinform.models.source_omissions import (
    SourceMetadataOmission,
    SourceOmissionOwner,
    SourceOmissionParent,
    source_omission_sha256,
)
from lexinform.ports import BillRepository
from lexinform.services.document_history import (
    record_artifact_reads,
    record_artifacts,
    record_source_omission,
)
from lexinform.source_omission_frames import source_omission_record

log = logging.getLogger(__name__)
_ACTIVE: ContextVar[SourceOmissionScope | None] = ContextVar("source_omission_scope", default=None)
_INPUT: ContextVar[SourceOmissionInput | None] = ContextVar("source_omission_input", default=None)
_DISABLED: ContextVar[bool] = ContextVar("source_omission_disabled", default=False)


class SourceOmissionInput:
    def __init__(
        self,
        collector: SourceOmissionCollector,
        subject: SourceOmissionOwner,
        order: tuple[int, ...],
    ) -> None:
        self.collector = collector
        self.subject = subject
        self.order = order
        self.active = False
        self.thread: int | None = None
        self.children = 0

    def child(self) -> tuple[int, ...]:
        value = self.order + (self.children,)
        self.children += 1
        return value


@dataclass(frozen=True)
class SourceOmissionObservation:
    record: SourceMetadataOmission
    artifacts: tuple[ArtifactIdentity, ...] = ()
    reads: tuple[ArtifactReadIdentity, ...] = ()
    extractions: tuple[ExtractionResult, ...] = ()
    order: tuple[int, ...] = ()


class SourceOmissionScope:
    def __init__(
        self,
        collector: SourceOmissionCollector,
        owner: SourceOmissionOwner,
        *,
        role: Literal["generic", "rcl", "senate"],
        view: object,
        decisions: Iterable[MetadataUrlDecision],
    ) -> None:
        self.collector = collector
        self.owner = owner
        self.role = role
        self.order: tuple[int, ...] = ()
        self.active = True
        self.failed = False
        self.thread = threading.get_ident()
        self._artifacts: dict[str, ArtifactIdentity] = {}
        self._reads: dict[str, ArtifactReadIdentity] = {}
        self._extractions: dict[str, ExtractionResult] = {}
        self._view_sha256 = ""
        self._diagnostics: MetadataUrlOmissions | None = None
        with self.capture():
            self.update(view, decisions)

    @contextmanager
    def capture(self) -> Iterator[None]:
        try:
            yield
        except BaseException as exc:
            self.failed = True
            with suppress(BaseException):
                log.warning("source omission capture failed (%s)", type(exc).__name__[:128])

    def update(self, view: object, decisions: Iterable[MetadataUrlDecision]) -> None:
        collector = MetadataUrlOmissionCollector()
        for decision in decisions:
            if decision.reason is not None:
                collector.observe(
                    decision.reason, requested_url_sha256=decision.requested_url_sha256
                )
        self._view_sha256 = source_omission_sha256(view)
        self._diagnostics = collector.snapshot()

    def artifact(self, artifact: ArtifactIdentity) -> None:
        self._artifacts.setdefault(artifact.sha256, artifact)

    def read(self, read: ArtifactReadIdentity) -> None:
        self.artifact(ArtifactIdentity(sha256=read.sha256, byte_size=read.byte_size))
        self._reads.setdefault(read.read_id, read)

    def extraction(self, result: ExtractionResult) -> None:
        if result.artifact_sha256 in self._artifacts:
            self._extractions.setdefault(result.result_id, result)

    def observation(self) -> SourceOmissionObservation | None:
        if self.failed or self._diagnostics is None:
            return None
        parents: list[SourceOmissionParent] = []
        for sha in sorted(self._artifacts):
            reads = [item for item in self._reads.values() if item.sha256 == sha]
            extractions = [
                item for item in self._extractions.values() if item.artifact_sha256 == sha
            ]
            if reads:
                for read in reads:
                    for extraction in extractions or (None,):
                        parents.append(
                            SourceOmissionParent(
                                artifact_sha256=sha,
                                artifact_read_id=read.read_id,
                                extraction_id=extraction.result_id if extraction else None,
                            )
                        )
            else:
                for extraction in extractions or (None,):
                    parents.append(
                        SourceOmissionParent(
                            artifact_sha256=sha,
                            extraction_id=extraction.result_id if extraction else None,
                        )
                    )
        record = source_omission_record(
            self.owner,
            source_view_sha256=self._view_sha256,
            diagnostics=self._diagnostics,
            parents=tuple(parents),
        )
        return SourceOmissionObservation(
            record,
            tuple(self._artifacts[key] for key in sorted(self._artifacts)),
            tuple(self._reads[key] for key in sorted(self._reads)),
            tuple(self._extractions[key] for key in sorted(self._extractions)),
            self.order,
        )


class SourceOmissionCollector:
    def __init__(self) -> None:
        self._lock = Lock()
        self._records: dict[str, SourceOmissionObservation] = {}
        self._phase = 0

    def reset(self) -> None:
        with self._lock:
            self._records.clear()
            self._phase = 0

    def _input(self) -> SourceOmissionInput | None:
        current = _INPUT.get()
        return (
            current
            if (
                current is not None
                and current.collector is self
                and current.active
                and current.thread == threading.get_ident()
            )
            else None
        )

    def reserve(self, owners: tuple[SourceOmissionOwner, ...]) -> tuple[SourceOmissionInput, ...]:
        current = self._input()
        with self._lock:
            prefix: tuple[int, ...]
            if current is None:
                prefix = (self._phase,)
                self._phase += 1
            else:
                prefix = current.child()
        return tuple(
            SourceOmissionInput(self, owner, prefix + (index,))
            for index, owner in enumerate(owners)
        )

    @contextmanager
    def disabled(self) -> Iterator[None]:
        active_token = _ACTIVE.set(None)
        input_token = _INPUT.set(None)
        disabled_token = _DISABLED.set(True)
        try:
            yield None
        finally:
            _DISABLED.reset(disabled_token)
            _INPUT.reset(input_token)
            _ACTIVE.reset(active_token)

    @contextmanager
    def input(self, slot: SourceOmissionInput | None) -> Iterator[None]:
        if slot is None:
            with self.disabled():
                yield
            return
        slot.active = True
        slot.thread = threading.get_ident()
        token = _INPUT.set(slot)
        try:
            yield
        finally:
            slot.active = False
            _INPUT.reset(token)

    @contextmanager
    def observed_input(self, slot: SourceOmissionInput | None) -> Iterator[None]:
        active_token = _ACTIVE.set(None)
        input_token = _INPUT.set(_INPUT.get())
        disabled_token = _DISABLED.set(_DISABLED.get() or slot is None)
        stack = ExitStack()
        try:
            try:
                stack.enter_context(self.input(slot))
            except BaseException as exc:
                _ACTIVE.set(None)
                _INPUT.set(None)
                _DISABLED.set(True)
                with suppress(BaseException):
                    log.warning("source omission input unavailable (%s)", type(exc).__name__[:128])
            try:
                yield
            finally:
                try:
                    stack.close()
                except BaseException as exc:
                    with suppress(BaseException):
                        log.warning(
                            "source omission input cleanup failed (%s)", type(exc).__name__[:128]
                        )
        finally:
            if slot is not None:
                slot.active = False
            _DISABLED.reset(disabled_token)
            _INPUT.reset(input_token)
            _ACTIVE.reset(active_token)

    def _order(self) -> tuple[int, ...]:
        current = self._input()
        with self._lock:
            if current is not None:
                return current.child()
            order = (self._phase, 0)
            self._phase += 1
            return order

    def current(self) -> SourceOmissionScope | None:
        scope = _ACTIVE.get()
        return (
            scope
            if (
                scope is not None
                and scope.collector is self
                and scope.active
                and not scope.failed
                and scope.thread == threading.get_ident()
            )
            else None
        )

    @contextmanager
    def suspended(self) -> Iterator[None]:
        token = _ACTIVE.set(None)
        try:
            yield None
        finally:
            _ACTIVE.reset(token)

    @contextmanager
    def scope(
        self,
        owner: SourceOmissionOwner,
        *,
        role: Literal["generic", "rcl", "senate"],
        view: object,
        decisions: Iterable[MetadataUrlDecision],
    ) -> Iterator[SourceOmissionScope | None]:
        if _DISABLED.get():
            with self.suspended():
                yield None
            return
        scope = None
        try:
            scope = SourceOmissionScope(self, owner, role=role, view=view, decisions=decisions)
            scope.order = self._order()
        except BaseException as exc:
            if scope is not None:
                scope.failed = True
            scope = None
            with suppress(BaseException):
                log.warning("source omission setup failed (%s)", type(exc).__name__[:128])
        token = _ACTIVE.set(scope)
        try:
            yield scope
        finally:
            if scope is not None:
                scope.active = False
            _ACTIVE.reset(token)
            if scope is not None and not scope.failed:
                with scope.capture():
                    observation = scope.observation()
                    if observation is not None:
                        with self._lock:
                            previous = self._records.get(observation.record.omission_id)
                            if previous is None or observation.order < previous.order:
                                self._records[observation.record.omission_id] = observation

    def snapshot(self) -> tuple[SourceOmissionObservation, ...]:
        with self._lock:
            return tuple(sorted(self._records.values(), key=lambda item: item.order))


@dataclass(frozen=True)
class SourceOmissionPersistence:
    read: Callable[[], tuple[SourceOmissionObservation, ...]]

    def persist_source_history(self, repo: BillRepository, *, recorded_at: dt.datetime) -> None:
        with repo.atomic():
            for observation in self.read():
                record_artifacts(repo, observation.artifacts, recorded_at=recorded_at)
                record_artifact_reads(repo, observation.reads, recorded_at=recorded_at)
                for result in observation.extractions:
                    repo.save_extraction_result(result, recorded_at=recorded_at)
                record_source_omission(repo, observation.record, recorded_at=recorded_at)
