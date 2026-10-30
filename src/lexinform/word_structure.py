"""Bounded sidecar hooks retain actual legacy Word events without changing their text view."""

import hashlib
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal

from lexinform.models.documents import WordDetailedExtraction
from lexinform.models.word_notes import WordNoteCall
from lexinform.models.word_structure import (
    WordStructureBlock,
    WordStructureCall,
    WordStructureExtraction,
    WordStructureSupport,
    WordStructureView,
    WordXmlPart,
    structure_sha256,
)

_CURRENT: ContextVar[WordStructureCollector | None] = ContextVar("word_structure", default=None)


@dataclass(frozen=True)
class _Event:
    kind: Literal["block", "call", "support"]
    xml_part: WordXmlPart
    path: tuple[int, ...]
    values: dict[str, object]


class WordStructureCollector:
    def __init__(self, *, max_events: int = 4096, max_bytes: int = 524288) -> None:
        if (
            type(max_events) is not int
            or not 1 <= max_events <= 65536
            or type(max_bytes) is not int
            or not 4096 <= max_bytes <= 1048576
        ):
            raise ValueError("Word structure limits are outside their supported bounds")
        self.max_events = max_events
        self.max_bytes = max_bytes
        self.part: WordXmlPart = "word/document.xml"
        self._records: list[_Event] = []
        self._counts = {"block": 0, "call": 0, "support": 0}
        self._bytes = 4096
        self._limited = False
        self._thread: int | None = None
        self._entered = False

    @contextmanager
    def scope(self) -> Iterator[None]:
        if self._entered:
            raise ValueError("Word structure scopes belong to one actual invocation")
        self._entered = True
        self._thread = threading.get_ident()
        token = _CURRENT.set(self)
        try:
            yield
        finally:
            self._thread = None
            _CURRENT.reset(token)

    def observe(self, event: _Event) -> None:
        if self._thread != threading.get_ident():
            return
        self._counts[event.kind] += 1
        if self._limited:
            return
        if len(self._records) >= self.max_events:
            self._limited = True
            return
        size = (
            len(
                json.dumps(
                    {"part": event.xml_part, "path": event.path, **event.values},
                    ensure_ascii=True,
                    separators=(",", ":"),
                ).encode("ascii")
            )
            + 256
            + (len(json.dumps(event.path)) if event.kind == "call" else 0)
        )
        if self._bytes + size > self.max_bytes:
            self._limited = True
            return
        self._bytes += size
        self._records.append(event)

    def support(
        self,
        path: tuple[int, ...],
        tag: str,
        child_count: int,
        view: WordStructureView,
        *,
        recognized: bool = True,
    ) -> None:
        if self._thread != threading.get_ident():
            return
        if len(tag) > 4096:
            self._counts["support"] += 1
            self._limited = True
            return
        self.observe(
            _Event(
                "support",
                self.part,
                path,
                {
                    "tag": tag,
                    "child_count": child_count,
                    "view": view,
                    "support": "recognized"
                    if recognized
                    else "legacy_recursion"
                    if view == "recursed"
                    else "unclassified",
                },
            )
        )

    def block(
        self,
        kind: Literal["paragraph", "table_row", "table_cell"],
        path: tuple[int, ...],
        text: str,
    ) -> None:
        self.observe(
            _Event(
                "block",
                self.part,
                path,
                {
                    "kind": kind,
                    "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "text_characters": len(text),
                },
            )
        )

    def call(
        self,
        kind: Literal["footnote", "endnote"],
        identity: str,
        path: tuple[int, ...],
        paragraph: tuple[int, ...],
        *,
        expanded: bool,
    ) -> None:
        if self._thread != threading.get_ident():
            return
        if len(identity) > self.max_bytes:
            self._counts["call"] += 1
            self._limited = True
            return
        self.observe(
            _Event(
                "call",
                self.part,
                path,
                {"kind": kind, "identity": identity, "paragraph": paragraph, "expanded": expanded},
            )
        )

    def finish(
        self, legacy: WordDetailedExtraction, targets: dict[str, int], seen: set[str]
    ) -> WordStructureExtraction:
        blocks: list[WordStructureBlock] = []
        calls: list[WordStructureCall] = []
        support: list[WordStructureSupport] = []
        needed = {(event.xml_part, event.path[0]) for event in self._records if event.path}
        note_ids: dict[tuple[WordXmlPart, int], str] = {
            (note.xml_part, note.path[0]): note.note_id
            for note in legacy.notes.notes
            if (note.xml_part, note.path[0]) in needed
        }
        for order, event in enumerate(self._records):
            location = {
                "order": order,
                "xml_part": event.xml_part,
                "path": event.path,
                "note_id": note_ids.get((event.xml_part, event.path[0])) if event.path else None,
            }
            if event.kind == "block":
                blocks.append(WordStructureBlock.model_validate({**location, **event.values}))
            elif event.kind == "support":
                support.append(WordStructureSupport.model_validate({**location, **event.values}))
            else:
                kind, identity = str(event.values["kind"]), str(event.values["identity"])
                key = f"{kind}/{identity}"
                target = targets.get(key)
                expected = "word/footnotes.xml" if kind == "footnote" else "word/endnotes.xml"
                state = (
                    ("expanded" if legacy.notes.notes[target].text else "empty")
                    if target is not None
                    else "unverified"
                    if key in seen
                    or any(
                        part.name == expected and part.state != "read"
                        for part in legacy.notes.parts
                    )
                    else "missing"
                )
                call = WordNoteCall.model_validate(
                    {
                        "path": event.path,
                        "paragraph_path": event.values["paragraph"],
                        "kind": kind,
                        "identity": identity,
                        "state": state,
                        "target": target,
                    }
                )
                calls.append(
                    WordStructureCall.model_validate(
                        {
                            **location,
                            "call": call,
                            "legacy_view": "expanded"
                            if event.values["expanded"]
                            else "missing_marker",
                        }
                    )
                )
        complete = legacy.body.body_state == "read" and all(
            part.state == "read" for part in legacy.notes.parts
        )
        values = {
            "input_sha256": legacy.body.input_sha256,
            "xml_sha256": legacy.body.xml_sha256,
            "body_manifest_sha256": legacy.body.manifest_sha256,
            "notes_manifest_sha256": legacy.notes.manifest_sha256,
            "note_parts": legacy.notes.parts,
            "blocks": tuple(blocks),
            "calls": tuple(calls),
            "support": tuple(support),
            "block_count": self._counts["block"],
            "call_count": self._counts["call"],
            "support_count": self._counts["support"],
            "walk_complete": complete,
            "limit_reached": self._limited,
            "max_events": self.max_events,
            "max_bytes": self.max_bytes,
            "coverage": "complete" if complete and not self._limited else "partial",
            "observed_stream_sha256": None,
        }
        if complete and not self._limited:
            records = [
                {"type": kind, **record.model_dump(mode="json")}
                for kind, items in (("block", blocks), ("call", calls), ("support", support))
                for record in items
            ]
            records.sort(key=lambda entry: int(str(entry["order"])))
            values["observed_stream_sha256"] = structure_sha256(records)
        return WordStructureExtraction.model_validate(values)


def current_word_structure() -> WordStructureCollector | None:
    value = _CURRENT.get()
    return value if value is not None and value._thread == threading.get_ident() else None
