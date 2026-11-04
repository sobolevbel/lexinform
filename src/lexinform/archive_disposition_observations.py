"""Canonical priority v1 retains selected main/annex/support, other policy, then walk."""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

from lexinform.archive_dispositions import MAX_ARCHIVE_VALIDATION_BYTES, enrich_archive_trace
from lexinform.archive_policy_capture import ArchivePolicyDecision
from lexinform.models import ArchiveMemberDisposition, ArtifactIdentity, CandidateDispositionTrace
from lexinform.models.physical_inventory import (
    PhysicalContainerExecution,
    PhysicalGraph,
    PhysicalHeaders,
    PhysicalRootOrder,
)
from lexinform.physical_root_assembly import PhysicalAssemblyPacket
from lexinform.physical_walk_capture import PhysicalWalkObservation

ARCHIVE_DISPOSITION_PRIORITY_VERSION = 1

Phase = Literal["version_comparison", "fallback"]


@dataclass(frozen=True)
class ArchiveRootObservation:
    artifact: ArtifactIdentity
    physical: PhysicalWalkObservation | None
    policy: tuple[ArchivePolicyDecision, ...] | None
    completed: bool


class _UnavailableError(Exception):
    pass


def _packet(root: ArchiveRootObservation) -> PhysicalAssemblyPacket | None:
    observation = root.physical
    if observation is None or not isinstance(observation.assembly, PhysicalAssemblyPacket):
        return None
    packet = observation.assembly
    if (
        observation.artifact != root.artifact
        or packet.capture.artifact_sha256 != root.artifact.sha256
    ):
        raise _UnavailableError
    return packet


def _is_archive(root: ArchiveRootObservation) -> bool:
    packet = _packet(root)
    if packet is not None:
        return any(
            isinstance(item.metadata, PhysicalContainerExecution)
            and item.metadata.route in ("archive", "zip_unreadable")
            for item in packet.metadata
        )
    observation = root.physical
    return (
        observation is not None
        and observation.draft is not None
        and any(
            frame.route in ("archive", "zip_unreadable") for frame in observation.draft.containers
        )
    )


def _rows(
    root: ArchiveRootObservation, phase: Phase, index: int
) -> Iterator[ArchiveMemberDisposition]:
    packet = _packet(root)
    if packet is None or root.physical is None:
        raise _UnavailableError
    if root.physical.unavailable is not None or root.physical.limitations:
        raise _UnavailableError
    if (
        sum(len(item.model_dump_json().encode()) for item in packet.metadata)
        > MAX_ARCHIVE_VALIDATION_BYTES
    ):
        raise _UnavailableError
    metadata = {item.metadata_id: item for item in packet.metadata}
    if len(metadata) != len(packet.metadata) or any(
        item.coverage != "complete" for item in packet.metadata
    ):
        raise _UnavailableError
    record = metadata.get(packet.capture.execution_id)
    if record is None or not isinstance(record.metadata, PhysicalRootOrder):
        raise _UnavailableError
    order = record.metadata
    contexts = order.contexts
    paths: list[tuple[int, ...]] = []
    units: list[PhysicalContainerExecution] = []
    graphs: list[PhysicalGraph] = []
    slots: dict[int, int] = {}
    physical: dict[int, str] = {}
    for context, physical_text_sha, _ in order.physical:
        if context in physical:
            raise _UnavailableError
        physical[context] = physical_text_sha
    for position, (parent, ordinal, slot, unit_id) in enumerate(contexts):
        if parent is None:
            if position or ordinal is not None:
                raise _UnavailableError
            path: tuple[int, ...] = ()
        else:
            if parent >= position or ordinal is None:
                raise _UnavailableError
            path = (*paths[parent], ordinal)
        if len(path) > 4:
            raise _UnavailableError
        paths.append(path)
        unit_record = metadata.get(unit_id)
        if unit_record is None or not isinstance(unit_record.metadata, PhysicalContainerExecution):
            raise _UnavailableError
        unit = unit_record.metadata
        graph_record = metadata.get(unit.graph)
        if graph_record is None or not isinstance(graph_record.metadata, PhysicalGraph):
            raise _UnavailableError
        graph = graph_record.metadata
        if unit_record.body_sha256 != graph.body_sha:
            raise _UnavailableError
        if position == 0:
            if graph.body_sha != root.artifact.sha256:
                raise _UnavailableError
        elif (
            parent is not None
            and ordinal is not None
            and (
                ordinal >= len(graphs[parent].edges)
                or graphs[parent].edges[ordinal] != (graph.body_sha, unit.graph)
            )
        ):
            raise _UnavailableError
        units.append(unit)
        graphs.append(graph)
        if slot is not None:
            if slot in slots or position not in physical:
                raise _UnavailableError
            slots[slot] = position
    if not any(unit.route == "archive" for unit in units):
        raise _UnavailableError
    occurrences = {path: position for position, path in enumerate(paths)}
    if len(occurrences) != len(paths):
        raise _UnavailableError
    if root.completed:
        if (
            root.policy is None
            or len(root.policy) != len(slots)
            or {item.index for item in root.policy} != set(slots)
            or set(physical) != set(slots.values())
        ):
            raise _UnavailableError
        for decision in root.policy:
            policy_position = slots.get(decision.index)
            if (
                policy_position is None
                or physical.get(policy_position) != decision.text_sha256
                or graphs[policy_position].body_sha != decision.leaf_sha256
            ):
                raise _UnavailableError
            path = paths[policy_position]
            if not path:
                raise _UnavailableError
        priority = {"selected_main": 0, "selected_annex": 1, "selected_support": 2}
        for decision in sorted(
            root.policy, key=lambda item: (priority.get(item.reason, 3), item.index)
        ):
            policy_position = slots[decision.index]
            path = paths[policy_position]
            yield ArchiveMemberDisposition(
                stage="policy",
                phase=phase,
                index=index,
                root_sha256=root.artifact.sha256,
                ordinals=path,
                physical_part_index=decision.index,
                outcome=decision.outcome,
                reason=decision.reason,
                leaf_sha256=decision.leaf_sha256,
                text_sha256=decision.text_sha256,
            )
    for position, unit in enumerate(units):
        if unit.route != "archive":
            continue
        graph = graphs[position]
        header_record = metadata.get(graph.header or "")
        if header_record is None or not isinstance(header_record.metadata, PhysicalHeaders):
            raise _UnavailableError
        if len(unit.statuses) != len(header_record.metadata.rows) or len(graph.edges) != len(
            unit.statuses
        ):
            raise _UnavailableError
        for ordinal, reason in enumerate(unit.statuses):
            path = (*paths[position], ordinal)
            if len(path) > 4:
                raise _UnavailableError
            child = occurrences.get(path)
            slot = contexts[child][2] if child is not None else None
            text_sha = physical.get(child) if child is not None else None
            yield ArchiveMemberDisposition(
                stage="walk",
                phase=phase,
                index=index,
                root_sha256=root.artifact.sha256,
                ordinals=path,
                physical_part_index=slot,
                outcome="observed",
                reason=reason,
                leaf_sha256=graph.edges[ordinal][0],
                text_sha256=text_sha,
            )


class ArchiveDispositionAccumulator:
    """Retain bounded primitive rows, never the invocation packets that established them."""

    def __init__(self) -> None:
        self._rows: list[ArchiveMemberDisposition] = []
        self._bytes = 2
        self._seen = False
        self._unavailable = False
        self._failed = False
        self._walk: Literal["finished", "interrupted"] = "finished"
        self._unvisited: Literal["none_known", "known"] = "none_known"

    def observe(self, phase: Phase, index: int, root: ArchiveRootObservation) -> None:
        if self._unavailable or self._failed:
            return
        try:
            if not _is_archive(root):
                return
            self._seen = True
            if not root.completed:
                self._walk = "interrupted"
            for row in _rows(root, phase, index):
                size = len(row.model_dump_json(exclude_defaults=True).encode()) + bool(self._rows)
                if self._bytes + size > MAX_ARCHIVE_VALIDATION_BYTES:
                    raise _UnavailableError
                self._bytes += size
                if row.reason in (
                    "not_visited_depth_limit",
                    "unpacking_limit_source_pinned_inference",
                    "uninspected_format_part",
                ):
                    self._unvisited = "known"
                self._rows.append(row)
        except _UnavailableError:
            self._seen = self._unavailable = True
            self._rows.clear()
        except Exception:
            self._failed = True
            self._rows.clear()

    def enrich(self, trace: CandidateDispositionTrace) -> CandidateDispositionTrace:
        if not self._seen or self._failed:
            return trace
        return enrich_archive_trace(
            trace,
            None if self._unavailable else self._rows,
            walk=self._walk,
            unvisited=self._unvisited,
        )
