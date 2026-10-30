"""Observe bounded ODT note metadata during the existing legacy append walk."""

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from lexinform.models import OdtNoteOrigins, OdtTextExtraction
from lexinform.models.odt_notes import (
    MAX_ODT_NOTE_BYTES,
    MAX_ODT_NOTE_EVENTS,
    MAX_ODT_NOTE_PATH,
    OdtNoteCall,
    OdtNoteEntry,
    _width,
    odt_note_association,
)

TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
MAX_COUNT = 2**63 - 1


@dataclass
class OdtNoteFrame:
    path: tuple[int, ...]
    entry: dict[str, object] | None
    call: dict[str, object] | None
    parent: OdtNoteFrame | None


def odt_note_event[T](collector: OdtNoteCollector, action: Callable[[], T]) -> T | None:
    try:
        return action()
    except Exception:
        with suppress(Exception):
            collector.failed = True
        return None


class OdtAppendParts(list[str]):
    def __init__(self, seed: str, collector: OdtNoteCollector) -> None:
        super().__init__((seed,))
        self.position = len(seed)
        self.collector = collector

    def append(self, value: str) -> None:
        super().append(value)
        odt_note_event(self.collector, lambda: self.advance(len(value)))

    def advance(self, count: int) -> None:
        self.position += count
        if self.position > MAX_COUNT:
            raise ValueError("ODT observed character count exceeds its profile")


class OdtNoteCollector:
    def __init__(self) -> None:
        self.entries: list[dict[str, object]] = []
        self.calls: list[dict[str, object]] = []
        self.note_count = 0
        self.call_count = 0
        self.reserved_bytes = 1024
        self.closed = False
        self.failed = False

    def child(self, path: tuple[int, ...] | None, index: int) -> tuple[int, ...] | None:
        if self.closed or self.failed or path is None:
            return None
        if len(path) >= MAX_ODT_NOTE_PATH or index < 0 or index > MAX_COUNT:
            self.closed = True
            return None
        return (*path, index)

    def _room(self) -> bool:
        if self.closed or self.failed:
            return False
        if len(self.entries) + len(self.calls) >= MAX_ODT_NOTE_EVENTS:
            self.closed = True
            return False
        return True

    def _reserve(self, value: dict[str, object], *, children: int = 0) -> bool:
        estimate = dict(value)
        if children and isinstance(value["path"], tuple):
            path = value["path"]
            if len(path) < MAX_ODT_NOTE_PATH:
                last = (*path, children - 1)
                estimate.update(citation_path=last, body_path=last)
        try:
            needed = _width(estimate) + 1
        except ValueError:
            self.closed = True
            return False
        if self.reserved_bytes + needed > MAX_ODT_NOTE_BYTES:
            self.closed = True
            return False
        self.reserved_bytes += needed
        return True

    def enter(
        self,
        node: ET.Element,
        path: tuple[int, ...] | None,
        paragraph: tuple[int, ...] | None,
        block: tuple[int, ...],
        parent: OdtNoteFrame | None,
        start: int,
    ) -> OdtNoteFrame | None:
        if self.failed:
            return None
        if node.tag not in (TEXT + "note", TEXT + "note-ref"):
            if (
                parent is not None
                and path is not None
                and path[:-1] == parent.path
                and parent.entry
            ):
                field = (
                    "citation_path"
                    if node.tag == TEXT + "note-citation"
                    else "body_path"
                    if node.tag == TEXT + "note-body"
                    else None
                )
                if field is not None:
                    parent.entry[field] = path
            return None
        order = self.note_count + self.call_count
        embedded = node.tag == TEXT + "note"
        if embedded:
            self.note_count += 1
            self.call_count += 1
        else:
            self.call_count += 1
        if self.note_count > MAX_COUNT or self.call_count > MAX_COUNT:
            raise ValueError("ODT observed event count exceeds its profile")
        if path is None or paragraph is None or not self._room():
            return None
        span = {"start": start, "end": MAX_COUNT, "evidence": "observed-legacy-appends"}
        identity = node.get(TEXT + ("id" if embedded else "ref-name"))
        common: dict[str, object] = {
            "order": order,
            "path": path,
            "paragraph_path": paragraph,
            "block_path": block,
            "emitted": span,
        }
        entry = None
        if embedded:
            entry = {
                **common,
                "identity": identity,
                "note_class": node.get(TEXT + "note-class"),
                "citation_path": None,
                "body_path": None,
                "parent_note_path": parent.path if parent is not None else None,
            }
            if not self._reserve(entry, children=len(node)):
                return None
            self.entries.append(entry)
            order += 1
        reference = None
        if self._room():
            reference = {
                **common,
                "order": order,
                "kind": "embedded" if embedded else "reference",
                "identity": identity,
                "association": "unverified",
                "target_path": None,
            }
            estimate = dict(reference)
            estimate["target_path"] = (MAX_COUNT,) * MAX_ODT_NOTE_PATH
            if self._reserve(estimate):
                self.calls.append(reference)
            else:
                reference = None
        return OdtNoteFrame(path, entry, reference, parent)

    def leave(self, frame: OdtNoteFrame | None, end: int) -> None:
        if frame is None or self.failed:
            return
        for event in (frame.entry, frame.call):
            if event is not None:
                span = event["emitted"]
                if isinstance(span, dict):
                    span["end"] = end

    def finish(self, body: OdtTextExtraction) -> OdtNoteOrigins | None:
        if self.failed:
            return None
        entries = tuple(OdtNoteEntry.model_validate(value) for value in self.entries)
        calls = []
        for value in self.calls:
            raw = dict(value)
            kind = raw["kind"]
            path = raw["path"]
            identity = raw["identity"]
            if (
                kind not in ("embedded", "reference")
                or not isinstance(path, tuple)
                or identity is not None
                and not isinstance(identity, str)
            ):
                raise ValueError("ODT observed call has an invalid local shape")
            state, target = odt_note_association(
                kind, path, identity, entries, inventory_complete=self.note_count == len(entries)
            )
            raw.update(association=state, target_path=target)
            calls.append(OdtNoteCall.model_validate(raw))
        unavailable = body.body_state != "read"
        coverage = (
            "unknown"
            if unavailable
            else "partial"
            if (self.note_count, self.call_count) != (len(entries), len(calls))
            else "complete"
        )
        return OdtNoteOrigins(
            input_sha256=body.input_sha256,
            xml_sha256=body.xml_sha256,
            body_manifest_sha256=body.manifest_sha256,
            walk_state="unavailable" if unavailable else "finished",
            note_count=self.note_count,
            call_count=self.call_count,
            entries=entries,
            calls=tuple(calls),
            coverage=coverage,
        )
