"""Actual observed facts keep source order separate from dependency assembly order."""

from dataclasses import dataclass
from typing import Literal

from lexinform.models.artifacts import ArtifactIdentity
from lexinform.models.physical_inventory import (
    HeaderDisposition,
    OrderSlot,
    PayloadOpen,
    PhysicalMetadata,
    PhysicalSlot,
    ReadRequest,
)


@dataclass(frozen=True)
class ObservedPhysicalContainer:
    index: int
    parent: int | None
    parent_ordinal: int | None
    body: ArtifactIdentity
    header: PhysicalMetadata | None
    known_members: tuple[tuple[int, ArtifactIdentity], ...]
    route: Literal["archive", "word", "odt", "non_zip", "zip_unreadable"]
    propagated_error: str | None
    format_error: str | None
    statuses: tuple[HeaderDisposition, ...]
    requests: tuple[ReadRequest, ...]
    opens: tuple[PayloadOpen, ...]
    physical_index: int | None = None
    complete: bool = True


@dataclass(frozen=True)
class ObservedPhysicalOrder:
    requests: tuple[OrderSlot, ...]
    opens: tuple[OrderSlot, ...]
    physical: tuple[PhysicalSlot, ...]
    zip_failures: tuple[tuple[int, str], ...]
    legacy_rejections: tuple[int, str]
