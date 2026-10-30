"""Invocation-owned bounded physical events remain separate from business results and errors."""

import hashlib
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Literal

from lexinform.models.artifacts import ArtifactIdentity
from lexinform.models.documents import DocumentPart
from lexinform.models.extractions import ExtractionResult
from lexinform.models.physical_inventory import (
    HeaderDisposition,
    OrderSlot,
    PayloadOpen,
    PhysicalMetadata,
    PhysicalOmission,
    PhysicalOmittedCount,
    PhysicalSlot,
    ReadRequest,
)
from lexinform.physical_assembly_encoding import physical_encoded_size, physical_json_chunks
from lexinform.physical_assembly_inputs import (
    ObservedPhysicalContainer,
    ObservedPhysicalOrder,
    ObservedPhysicalPrefix,
    ObservedPhysicalTotals,
)
from lexinform.physical_root_assembly import (
    PhysicalAssemblyGap,
    PhysicalAssemblyPacket,
    PhysicalRootAssembler,
)

MAX_OBSERVATION_BYTES = 1_048_576

_CURRENT: ContextVar[PhysicalWalkCapture | None] = ContextVar("physical_walk_capture", default=None)


def physical_error(error: BaseException) -> str:
    name = type(error).__name__
    return name if len(name) <= 128 and name.isascii() and name.isidentifier() else "CaptureError"


def physical_hash(value: object) -> str:
    digest = hashlib.sha256()
    for chunk in physical_json_chunks(value):
        digest.update(chunk)
    return digest.hexdigest()


@dataclass
class PhysicalWalkContext:
    index: int
    parent: int | None
    parent_ordinal: int | None
    body: ArtifactIdentity | None
    route: Literal["archive", "word", "odt", "non_zip", "zip_unreadable"]
    retained: bool = True
    header_count: int | None = None
    request_count: int = 0
    open_count: int = 0
    header: PhysicalMetadata | None = None
    minimum_header: PhysicalMetadata | None = None
    known: dict[int, ArtifactIdentity] = field(default_factory=dict)
    statuses: list[HeaderDisposition] = field(default_factory=list)
    requests: list[ReadRequest] = field(default_factory=list)
    opens: list[PayloadOpen] = field(default_factory=list)
    active_ordinal: int | None = None
    active_request: int | None = None
    physical_index: int | None = None
    format_error: str | None = None


@dataclass(frozen=True)
class PhysicalWalkOrderPrefix:
    requests: tuple[OrderSlot, ...]
    opens: tuple[OrderSlot, ...]
    physical: tuple[PhysicalSlot, ...]
    zip_failures: tuple[tuple[int, str], ...]
    legacy_rejections: tuple[int, str] | None


@dataclass(frozen=True)
class PhysicalWalkDraft:
    containers: tuple[ObservedPhysicalContainer, ...]
    order_prefix: PhysicalWalkOrderPrefix
    observed_contexts: int
    observed_requests: int
    observed_opens: int
    observed_physical: int
    observed_zip_failures: int
    header_universes: tuple[tuple[int, int], ...]
    recipe: PhysicalMetadata | None
    counts_complete: bool
    full_order_sha256: str | None = None


@dataclass(frozen=True)
class PhysicalWalkObservation:
    artifact: ArtifactIdentity | None
    assembly: PhysicalAssemblyPacket | PhysicalAssemblyGap | None
    unavailable: str | None
    failure_class: str | None
    draft: PhysicalWalkDraft | None = None
    limitations: tuple[str, ...] = ()
    extraction: ExtractionResult | None = None


class PhysicalWalkCapture:
    def __init__(self, artifact: ArtifactIdentity | None = None) -> None:
        self.observation: PhysicalWalkObservation | None = None
        self._capacity = MAX_OBSERVATION_BYTES
        self._bytes = 0
        self._thread: int | None = None
        self._entered = False
        self._started = False
        self._walk_complete = False
        self._unavailable: str | None = None
        self._failure: str | None = None
        self._artifact: ArtifactIdentity | None = artifact
        self._recipe: PhysicalMetadata | None = None
        self._assembler: PhysicalRootAssembler | None = None
        self._stack: list[PhysicalWalkContext] = []
        self._count = 0
        self._request_count = 0
        self._open_count = 0
        self._physical_count = 0
        self._zip_failure_count = 0
        self._frames: list[ObservedPhysicalContainer] = []
        self._header_universes: list[tuple[int, int]] = []
        self._limitations: set[str] = set()
        self._requests: list[OrderSlot] = []
        self._opens: list[OrderSlot] = []
        self._physical: list[PhysicalSlot] = []
        self._zip_failures: list[tuple[int, str]] = []
        self._rejections: tuple[int, str] | None = None
        self._extraction: ExtractionResult | None = None

    @contextmanager
    def scope(self) -> Iterator[PhysicalWalkCapture]:
        if self._entered:
            raise ValueError("physical observation scopes belong to one actual invocation")
        self._entered = True
        self._thread = threading.get_ident()
        token = _CURRENT.set(self)
        try:
            yield self
        finally:
            self._thread = None
            _CURRENT.reset(token)
            try:
                self._finish()
            except BaseException as error:
                self.unavailable("capture_finalization_failed", error)
                self.observation = PhysicalWalkObservation(
                    self._artifact,
                    None,
                    self._unavailable,
                    self._failure,
                    self._draft(),
                    tuple(sorted(self._limitations)),
                    self._extraction,
                )
            self._stack.clear()
            self._assembler = None
            self._frames.clear()
            self._header_universes.clear()
            self._requests.clear()
            self._opens.clear()
            self._physical.clear()
            self._zip_failures.clear()
            self._recipe = None
            self._extraction = None

    def unavailable(self, reason: str, error: BaseException | None = None) -> None:
        if self._unavailable is None or (
            self._unavailable == "observation_capacity" and error is not None
        ):
            self._unavailable = reason
        if self._failure is None and error is not None:
            self._failure = physical_error(error)

    @property
    def counts_complete(self) -> bool:
        return (
            self._walk_complete
            and self._failure is None
            and "opaque_format_helper" not in self._limitations
        )

    def reserve(self, value: object, *, fixed: int = 0) -> bool:
        if self._unavailable is not None:
            return False
        size = physical_encoded_size(value, max(0, self._capacity - self._bytes - fixed))
        if size is None:
            self.unavailable("observation_capacity")
            return False
        self._bytes += size + fixed
        return True

    def start(self, data: bytes, *, sha256: str | None = None) -> None:
        if self._started:
            self.unavailable("multiple_extractor_invocations")
            return
        self._started = True
        actual = ArtifactIdentity(
            sha256=sha256 if sha256 is not None else hashlib.sha256(data).hexdigest(),
            byte_size=len(data),
        )
        if self._artifact is not None and self._artifact != actual:
            self.unavailable("invocation_root_differs")
            return
        self._artifact = actual
        self._assembler = PhysicalRootAssembler(self._artifact)

    def recipe(self, record: PhysicalMetadata) -> None:
        if self.reserve(record, fixed=128):
            self._recipe = record

    @property
    def context(self) -> PhysicalWalkContext | None:
        return self._stack[-1] if self._stack else None

    @contextmanager
    def collect(self, data: bytes, *, zip_input: bool) -> Iterator[None]:
        parent = self.context
        retained = False
        body = None
        try:
            retained = self.reserve((), fixed=1024)
            if retained:
                known = (
                    parent.known.get(parent.active_ordinal)
                    if parent is not None and parent.active_ordinal is not None
                    else self._artifact
                    if parent is None
                    else None
                )
                body = (
                    known
                    if known is not None and known.byte_size == len(data)
                    else ArtifactIdentity(
                        sha256=hashlib.sha256(data).hexdigest(), byte_size=len(data)
                    )
                )
        except BaseException as exc:
            self.unavailable("context_identity_unavailable", exc)
            retained = False
        context = PhysicalWalkContext(
            self._count,
            parent.index if parent else None,
            parent.active_ordinal if parent else None,
            body,
            "zip_unreadable" if zip_input else "non_zip",
            retained=retained,
        )
        self._count += 1
        self._stack.append(context)
        error = None
        try:
            yield
        except BaseException as exc:
            error = physical_error(exc)
            raise
        finally:
            self._stack.pop()
            if context.retained and context.body is not None:
                try:
                    requests: tuple[ReadRequest, ...]
                    opens: tuple[PayloadOpen, ...]
                    statuses: tuple[HeaderDisposition, ...]
                    pending = context.active_request
                    if pending is None:
                        requests = tuple(context.requests)
                        opens = tuple(context.opens)
                        statuses = tuple(context.statuses)
                    else:
                        self.unavailable("read_outcome_unavailable")
                        requests = tuple(context.requests[:pending])
                        open_count = next(
                            (
                                index
                                for index, event in enumerate(context.opens)
                                if event[0] is not None and event[0] >= pending
                            ),
                            len(context.opens),
                        )
                        opens = tuple(context.opens[:open_count])
                        statuses = ()
                    observed = ObservedPhysicalContainer(
                        index=context.index,
                        parent=context.parent,
                        parent_ordinal=context.parent_ordinal,
                        body=context.body,
                        header=context.header or context.minimum_header,
                        known_members=tuple(context.known.items()),
                        route=context.route,
                        propagated_error=error,
                        format_error=context.format_error,
                        statuses=statuses,
                        requests=requests,
                        opens=opens,
                        physical_index=context.physical_index,
                        complete=error is None
                        and self._unavailable is None
                        and not self._limitations,
                    )
                    self._frames.append(observed)
                    if self._unavailable is None and self._assembler is not None:
                        self._assembler.add_container(observed)
                except BaseException as exc:
                    self.unavailable("capture_assembly_failed", exc)

    def parsed(self, count: int, route: Literal["archive", "word", "odt"]) -> None:
        context = self.context
        if context is not None:
            context.route = route
            if context.header_count is None:
                context.header_count = count
                if context.retained:
                    self._header_universes.append((context.index, count))

    def header_prefix(self, record: PhysicalMetadata) -> None:
        context = self.context
        if context is not None and context.retained:
            context.minimum_header = record

    def header(
        self, record: PhysicalMetadata, count: int, route: Literal["archive", "word", "odt"]
    ) -> None:
        context = self.context
        if context is None:
            return
        if not self.reserve(record, fixed=count * 32):
            previous = record.omission
            digest = previous.full_metadata_sha256 if previous else record.metadata_id
            if context.minimum_header is not None and digest is not None:
                context.minimum_header = PhysicalMetadata.of(
                    context.minimum_header.metadata,
                    omission=PhysicalOmission(
                        reason="over_limit",
                        full_metadata_sha256=digest,
                        omitted=(PhysicalOmittedCount(field="rows", count=count),) if count else (),
                    ),
                )
            return
        if context.header is not None:
            if context.header != record:
                self.unavailable("reopened_header_differs")
            return
        context.header = record
        context.route = route
        context.statuses = ["uninspected_format_part"] * count

    def disposition(self, ordinal: int, status: HeaderDisposition) -> None:
        context = self.context
        if context is not None:
            context.active_ordinal = ordinal
            if ordinal < len(context.statuses):
                context.statuses[ordinal] = status

    def request(
        self, mode: Literal["member", "xml"], target: int | str, lookup: int | None
    ) -> int | None:
        context = self.context
        if context is None:
            return None
        self._request_count += 1
        context.request_count += 1
        request: ReadRequest = (
            mode,
            "ordinal" if isinstance(target, int) else "name",
            target,
            lookup,
            "failed",
            None,
            None,
            "CaptureError",
        )
        if not self.reserve(request, fixed=80):
            return None
        index = len(context.requests)
        context.requests.append(request)
        context.active_request = index
        self._requests.append((context.index, index))
        return index

    def returned(
        self,
        index: int | None,
        data: bytes | None,
        declared: int | None,
        error: BaseException | None,
    ) -> None:
        context = self.context
        if context is None or index is None:
            return
        mode, form, target, lookup, _, _, _, _ = context.requests[index]
        size = None
        digest = None
        outcome: Literal["complete", "failed", "returned_length_differs"] = "failed"
        if error is None and data is not None:
            size = len(data)
            outcome = "complete" if declared == size else "returned_length_differs"
            digest = hashlib.sha256(data).hexdigest() if outcome == "complete" else None
            if digest is not None and lookup is not None:
                context.known[lookup] = ArtifactIdentity(sha256=digest, byte_size=size)
        context.requests[index] = (
            mode,
            form,
            target,
            lookup,
            outcome,
            size,
            digest,
            physical_error(error) if error else None,
        )
        context.active_request = None
        if lookup is not None:
            status: HeaderDisposition
            if mode == "member":
                status = (
                    "member_complete"
                    if outcome == "complete"
                    else "member_failed"
                    if outcome == "failed"
                    else "member_returned_length_differs"
                )
            else:
                status = (
                    "format_complete"
                    if outcome == "complete"
                    else "format_failed"
                    if outcome == "failed"
                    else "format_returned_length_differs"
                )
            self.disposition(lookup, status)

    def opened(self, target: int | str, resolved: int | None, error: BaseException | None) -> None:
        context = self.context
        if context is None:
            return
        self._open_count += 1
        context.open_count += 1
        event: PayloadOpen = (
            context.active_request,
            "ordinal" if isinstance(target, int) else "name",
            target,
            resolved,
            "failed" if error else "opened",
            physical_error(error) if error else None,
        )
        if self.reserve(event, fixed=48):
            self._opens.append((context.index, len(context.opens)))
            context.opens.append(event)

    def zip_failed(self, error: BaseException) -> None:
        context = self.context
        self._zip_failure_count += 1
        self._limitations.add("zip_header_unavailable")
        if context is not None and self.reserve((), fixed=160):
            self._zip_failures.append((context.index, physical_error(error)))

    def format_failed(self, error: BaseException) -> None:
        context = self.context
        if context is not None:
            context.format_error = physical_error(error)

    def part(self, index: int, part: DocumentPart) -> None:
        context = self.context
        self._physical_count += 1
        if context is not None:
            context.physical_index = index
        if context is not None and self.reserve((), fixed=256):
            self._physical.append((context.index, part.text_sha256, physical_hash(part.losses)))

    def opaque_helper(self) -> None:
        self._limitations.add("opaque_format_helper")

    def rejected(self, rejected: list[str], *, complete: bool) -> None:
        self._rejections = len(rejected), physical_hash(rejected)
        self._walk_complete = complete

    def extraction(self, result: ExtractionResult) -> None:
        if self._artifact is not None and result.artifact_sha256 == self._artifact.sha256:
            self._extraction = result

    def _finish(self) -> None:
        assembly = None
        if not self._started:
            self.unavailable("cooperative_invocation_unavailable")
        elif self._rejections is None:
            self.unavailable("walk_finalization_unavailable")
        elif self._unavailable is None and self._assembler is not None and self._recipe is not None:
            assembly = self._assembler.finish(
                self._recipe,
                ObservedPhysicalOrder(
                    tuple(self._requests),
                    tuple(self._opens),
                    tuple(self._physical),
                    tuple(self._zip_failures),
                    self._rejections,
                ),
                complete=self._walk_complete and not self._limitations,
                extraction=self._extraction,
            )
        elif self._unavailable is None:
            self.unavailable("recipe_unavailable")
        capacity_prefix = False
        if (
            self._unavailable == "observation_capacity"
            and self.counts_complete
            and self._artifact is not None
            and self._recipe is not None
            and self._rejections is not None
        ):
            root = next((frame for frame in self._frames if frame.index == 0), None)
            if root is not None:
                assembly = PhysicalRootAssembler(self._artifact).finish_prefix(
                    self._recipe,
                    ObservedPhysicalPrefix(
                        self._artifact,
                        root.header,
                        ObservedPhysicalTotals(
                            self._count,
                            self._request_count,
                            self._open_count,
                            self._physical_count,
                            self._zip_failure_count,
                        ),
                        self._rejections,
                    ),
                    extraction=self._extraction,
                )
                if isinstance(assembly, PhysicalAssemblyPacket):
                    capacity_prefix = True
                    self._limitations.add("observation_capacity")
                    self._unavailable = None
        self.observation = PhysicalWalkObservation(
            self._artifact,
            assembly,
            self._unavailable,
            self._failure,
            self._draft() if self._unavailable is not None or capacity_prefix else None,
            tuple(sorted(self._limitations)),
            self._extraction,
        )

    def _draft(self) -> PhysicalWalkDraft | None:
        if not self._started:
            return None
        return PhysicalWalkDraft(
            tuple(self._frames),
            PhysicalWalkOrderPrefix(
                self._completed_order_prefix("requests"),
                self._completed_order_prefix("opens"),
                tuple(self._physical),
                tuple(self._zip_failures),
                self._rejections,
            ),
            self._count,
            self._request_count,
            self._open_count,
            self._physical_count,
            self._zip_failure_count,
            tuple(self._header_universes),
            self._recipe,
            self.counts_complete,
        )

    def _completed_order_prefix(self, field: Literal["requests", "opens"]) -> tuple[OrderSlot, ...]:
        frames = {frame.index: frame for frame in self._frames}
        prefix: list[OrderSlot] = []
        for owner, local in self._requests if field == "requests" else self._opens:
            frame = frames.get(owner)
            if frame is None:
                break
            count = len(frame.requests) if field == "requests" else len(frame.opens)
            if local >= count:
                break
            prefix.append((owner, local))
        return tuple(prefix)


def current_physical_walk_capture() -> PhysicalWalkCapture | None:
    current = _CURRENT.get()
    return current if current is not None and current._thread == threading.get_ident() else None


@contextmanager
def suspend_physical_walk_capture() -> Iterator[None]:
    token = _CURRENT.set(None)
    try:
        yield
    finally:
        _CURRENT.reset(token)
