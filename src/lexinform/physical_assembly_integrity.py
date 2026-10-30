"""Bounded independent proofs survive eviction of retained physical metadata."""

from dataclasses import dataclass
from typing import Literal

from pydantic import TypeAdapter

from lexinform.models.artifacts import ArtifactIdentity
from lexinform.models.physical_inventory import (
    MAX_PHYSICAL_ROOT_BYTES,
    HeaderDisposition,
    HeaderRow,
    OrderSlot,
    PhysicalContainerExecution,
    PhysicalHeaders,
    PhysicalMetadata,
    PhysicalSlot,
    Position,
    Sha256,
)
from lexinform.physical_assembly_encoding import validate_physical_record
from lexinform.physical_assembly_inputs import ObservedPhysicalContainer, ObservedPhysicalOrder

MAX_PHYSICAL_PROOF_BYTES = 2 * MAX_PHYSICAL_ROOT_BYTES
_MAX_ITEMS = MAX_PHYSICAL_PROOF_BYTES // 16
_STATUS = TypeAdapter(HeaderDisposition)
_ORDER_SLOT = TypeAdapter(OrderSlot)
_PHYSICAL_SLOT = TypeAdapter(PhysicalSlot)
_REJECTIONS = TypeAdapter(tuple[Position, Sha256])

PhysicalProofFailureReason = Literal[
    "invalid_observed_facts",
    "header_universe_unavailable",
    "parent_observation_unavailable",
    "integrity_proof_capacity",
]


class PhysicalAssemblyProofError(ValueError):
    def __init__(self, reason: PhysicalProofFailureReason, message: str) -> None:
        self.reason = reason
        super().__init__(message)


def validate_position(value: int) -> None:
    if type(value) is not int or not 0 <= value < 2**63:
        raise ValueError("physical occurrence positions require nonnegative signed integers")


def physical_identity(value: ArtifactIdentity) -> ArtifactIdentity:
    validate_position(value.byte_size)
    if not isinstance(value.sha256, str) or len(value.sha256) != 64:
        raise ValueError("complete body identities require their exact SHA256")
    return ArtifactIdentity(sha256=value.sha256, byte_size=value.byte_size)


@dataclass(frozen=True)
class _Context:
    parent: int | None
    ordinal: int | None
    body: ArtifactIdentity
    physical: int | None
    requests: int
    opens: int


class PhysicalAssemblyIntegrity:
    def __init__(self, artifact: ArtifactIdentity) -> None:
        self.artifact = physical_identity(artifact)
        self._contexts: dict[int, _Context] = {}
        self._bodies: dict[str, int] = {artifact.sha256: artifact.byte_size}
        self._children: dict[int, dict[int, int]] = {}
        self._bytes = 40
        self._transient = 0
        self.peak_bytes = self._bytes

    @property
    def context_count(self) -> int:
        return len(self._contexts)

    def _reserve(self, amount: int) -> None:
        if self._bytes + self._transient + amount > MAX_PHYSICAL_PROOF_BYTES:
            raise PhysicalAssemblyProofError(
                "integrity_proof_capacity", "physical integrity proof capacity is exhausted"
            )
        self._bytes += amount
        self.peak_bytes = max(self.peak_bytes, self._bytes + self._transient)

    def _body(self, body: ArtifactIdentity) -> ArtifactIdentity:
        body = physical_identity(body)
        previous = self._bodies.get(body.sha256)
        if previous is not None:
            if previous != body.byte_size:
                raise ValueError("an observed complete body cannot have conflicting byte sizes")
        else:
            self._reserve(40)
            self._bodies[body.sha256] = body.byte_size
        return body

    def observe(self, observed: ObservedPhysicalContainer) -> PhysicalMetadata | None:
        validate_position(observed.index)
        if observed.index in self._contexts:
            raise ValueError("physical occurrence index was already verified")
        if type(observed.complete) is not bool:
            raise ValueError("physical observation completeness requires an actual boolean")
        if (observed.parent is None) != (observed.parent_ordinal is None):
            raise ValueError("physical parent context and ordinal require one actual pair")
        if observed.index == 0:
            if observed.parent is not None or observed.body != self.artifact:
                raise ValueError("the actual root context must own its complete root body")
        elif observed.parent is None or observed.parent >= observed.index:
            raise ValueError("physical parent must precede its actual child occurrence")
        if observed.parent is not None:
            validate_position(observed.parent)
            assert observed.parent_ordinal is not None, "parent and ordinal are paired above"
            validate_position(observed.parent_ordinal)
            if observed.parent in self._contexts:
                raise ValueError("parents finalize after their actual child contexts")
        if observed.physical_index is not None:
            validate_position(observed.physical_index)
        total = (
            len(observed.known_members)
            + len(observed.statuses)
            + len(observed.requests)
            + len(observed.opens)
        )
        if total > _MAX_ITEMS:
            raise PhysicalAssemblyProofError(
                "integrity_proof_capacity", "supplied observations exceed bounded integrity work"
            )
        self._transient = (len(observed.known_members) + len(observed.opens)) * 24
        try:
            self._reserve(0)
            return self._observe(observed)
        finally:
            self._transient = 0

    def _observe(self, observed: ObservedPhysicalContainer) -> PhysicalMetadata | None:
        body = self._body(observed.body)
        header = validate_physical_record(observed.header) if observed.header is not None else None
        count = 0
        rows: tuple[HeaderRow, ...] = ()
        if header is not None:
            if (
                not isinstance(header.metadata, PhysicalHeaders)
                or header.body_sha256 != body.sha256
            ):
                raise ValueError("container header must identify its actual body")
            if header.metadata.central_directory_span[1] > body.byte_size:
                raise ValueError("header directory span exceeds its known complete body size")
            rows = header.metadata.rows
            count = header.total("rows", len(rows))
            validate_position(count)
        if len(observed.statuses) > count or observed.complete and len(observed.statuses) != count:
            raise ValueError("complete dispositions require their actual full header universe")
        for status in observed.statuses:
            if not isinstance(status, str) or len(status) > 64:
                raise ValueError("physical disposition must be a closed status")
            _STATUS.validate_python(status, strict=True)
        for error in (observed.propagated_error, observed.format_error):
            if error is not None and (not isinstance(error, str) or len(error) > 128):
                raise ValueError("physical errors retain bounded class identities only")
        PhysicalContainerExecution.model_validate(
            {
                "graph": "f" * 64,
                "route": observed.route,
                "propagated_error": observed.propagated_error,
                "format_error": observed.format_error,
                "statuses": (),
                "requests": (),
                "opens": (),
            }
        )
        members: dict[int, ArtifactIdentity] = {}
        for ordinal, member in observed.known_members:
            validate_position(ordinal)
            if ordinal >= count:
                if header is None or header.omission is not None and header.omission.unknown_fields:
                    raise PhysicalAssemblyProofError(
                        "header_universe_unavailable",
                        "known member ordinal requires its actual header universe",
                    )
                raise ValueError(
                    "known member occurrence differs from the observed header universe"
                )
            if ordinal in members:
                raise ValueError("known member occurrence was reported twice")
            actual = self._body(member)
            if ordinal < len(rows) and actual.byte_size != rows[ordinal][2]:
                raise ValueError("known complete member differs from its retained declared size")
            members[ordinal] = actual
        children = self._children.get(observed.index, {})
        for ordinal, child_index in children.items():
            child = self._contexts[child_index]
            if members.get(ordinal) != child.body:
                raise ValueError("child occurrence requires its exact observed member body")
        targets: dict[int, list[int]] = {}
        for opened in observed.opens:
            single = PhysicalContainerExecution(
                graph="f" * 64,
                route="non_zip",
                propagated_error=None,
                format_error=None,
                statuses=(),
                requests=(),
                opens=(opened,),
            ).opens[0]
            local, _, _, resolved, open_outcome, _ = single
            if local is not None and local >= len(observed.requests):
                raise ValueError("opened occurrence refers to an unobserved local request")
            if resolved is not None and resolved >= count:
                raise ValueError("opened occurrence is outside its observed header universe")
            if open_outcome == "opened" and local is not None:
                assert resolved is not None, "the unit model requires actual opened ordinals"
                targets.setdefault(local, []).append(resolved)
        for local, request in enumerate(observed.requests):
            single_request = PhysicalContainerExecution(
                graph="f" * 64,
                route="non_zip",
                propagated_error=None,
                format_error=None,
                statuses=(),
                requests=(request,),
                opens=(),
            ).requests[0]
            _, form, requested, lookup, outcome, size, sha, _ = single_request
            if lookup is not None and lookup >= count:
                raise ValueError("lookup occurrence is outside its observed header universe")
            if form == "ordinal" and isinstance(requested, int) and requested >= count:
                raise ValueError("requested occurrence is outside its observed header universe")
            if outcome != "complete":
                continue
            actual_targets = targets.get(local) or ([lookup] if lookup is not None else [])
            if not actual_targets:
                raise ValueError("completed member read requires its actual occurrence")
            for target in actual_targets:
                known = members.get(target)
                if known is None or (known.sha256, known.byte_size) != (sha, size):
                    raise ValueError("completed read differs from its actual member body identity")
        self._reserve(128 + (24 if observed.parent is not None else 0))
        if observed.parent is not None:
            assert observed.parent_ordinal is not None, "parent and ordinal remain paired"
            siblings = self._children.setdefault(observed.parent, {})
            if observed.parent_ordinal in siblings:
                raise ValueError("an actual member occurrence cannot acquire two child contexts")
            siblings[observed.parent_ordinal] = observed.index
        self._children.pop(observed.index, None)
        self._contexts[observed.index] = _Context(
            observed.parent,
            observed.parent_ordinal,
            body,
            observed.physical_index,
            len(observed.requests),
            len(observed.opens),
        )
        return header

    def verify_order(self, order: ObservedPhysicalOrder) -> None:
        if self._children or sorted(self._contexts) != list(range(len(self._contexts))):
            raise PhysicalAssemblyProofError(
                "parent_observation_unavailable",
                "actual context parents or entry-order addresses are unavailable",
            )
        for slots, field in ((order.requests, "requests"), (order.opens, "opens")):
            expected = sum(getattr(context, field) for context in self._contexts.values())
            if len(slots) != expected:
                raise ValueError("actual global order must include every local event exactly once")
        total = (
            len(order.requests) + len(order.opens) + len(order.physical) + len(order.zip_failures)
        )
        if total > _MAX_ITEMS:
            raise PhysicalAssemblyProofError(
                "integrity_proof_capacity", "global order exceeds bounded integrity work"
            )
        for slots, field in ((order.requests, "requests"), (order.opens, "opens")):
            seen: dict[int, bytearray] = {}
            needed = sum((getattr(context, field) + 7) // 8 for context in self._contexts.values())
            self._transient = needed
            try:
                self._reserve(0)
                for raw in slots:
                    owner, local = _ORDER_SLOT.validate_python(raw, strict=True)
                    context = self._contexts.get(owner)
                    if context is None or local >= getattr(context, field):
                        raise ValueError("actual event order refers to an unobserved local event")
                    bitmap = seen.get(owner)
                    if bitmap is None:
                        bitmap = bytearray((getattr(context, field) + 7) // 8)
                        seen[owner] = bitmap
                    byte, offset = divmod(local, 8)
                    bit = 1 << offset
                    if bitmap[byte] & bit:
                        raise ValueError("actual global order repeats a local event")
                    bitmap[byte] |= bit
            finally:
                self._transient = 0
        expected_physical = sum(context.physical is not None for context in self._contexts.values())
        if len(order.physical) != expected_physical:
            raise ValueError("actual physical order must retain every observed leaf")
        for index, raw_physical in enumerate(order.physical):
            owner, _, _ = _PHYSICAL_SLOT.validate_python(raw_physical, strict=True)
            context = self._contexts.get(owner)
            if context is None or context.physical != index:
                raise ValueError("physical leaf order differs from its actual context index")
        for owner, error in order.zip_failures:
            if not isinstance(error, str) or len(error) > 128:
                raise ValueError("ZIP failure retains a bounded error class identity")
            validate_position(owner)
            if owner not in self._contexts:
                raise ValueError("ZIP failure refers to an unobserved context")
            PhysicalContainerExecution(
                graph="f" * 64,
                route="non_zip",
                propagated_error=error,
                format_error=None,
                statuses=(),
                requests=(),
                opens=(),
            )
        _REJECTIONS.validate_python(order.legacy_rejections, strict=True)
