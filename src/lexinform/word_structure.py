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
from lexinform.models.extractions import WordViewStatus
from lexinform.models.word_notes import WordNoteCall
from lexinform.models.word_structure import (
    SEMANTIC_BYTE_QUOTAS,
    WORD_EXCEPTIONAL_VIEWS,
    WordPrefixLimit,
    WordRetention,
    WordSemanticBlock,
    WordSemanticCall,
    WordSemanticStructureExtraction,
    WordSemanticSupport,
    WordStructureBlock,
    WordStructureCall,
    WordStructureExtraction,
    WordStructureSupport,
    WordStructureView,
    WordXmlPart,
    semantic_event_quotas,
    structure_sha256,
    word_envelope_bytes,
)

_CURRENT: ContextVar[WordStructureCollector | None] = ContextVar("word_structure", default=None)


@dataclass(frozen=True)
class _Event:
    kind: Literal["block", "call", "support"]
    xml_part: WordXmlPart
    path: tuple[int, ...]
    values: dict[str, object]


class WordStructureCollector:
    def __init__(
        self,
        *,
        max_events: int = 4096,
        max_bytes: int = 524288,
        retention: WordRetention = "event-prefix-v1",
        _view_status_only: bool = False,
    ) -> None:
        if (
            type(max_events) is not int
            or not 1 <= max_events <= 65536
            or type(max_bytes) is not int
            or not 4096 <= max_bytes <= 1048576
        ):
            raise ValueError("Word structure limits are outside their supported bounds")
        if (
            retention not in ("event-prefix-v1", "semantic-first-v2")
            or retention == "semantic-first-v2"
            and (max_bytes != 524288 or max_events < 3)
        ):
            raise ValueError("Semantic Word retention requires its bounded versioned profile")
        self._view_status_only = _view_status_only
        self._view_failed = False
        self._view_counts = {
            "skipped_blocks": 0,
            "unclassified_nodes": 0,
            "legacy_recursion": 0,
            "ignored_fallbacks": 0,
        }
        self.retention = retention
        self._orders: list[int] = []
        self._semantic_order = 0
        self._recognized: dict[tuple[WordXmlPart, str], int] = {}
        self._exceptional = 0
        self._prefix_counts = {"block": 0, "call": 0, "support": 0}
        self._prefix_bytes = {"block": 2, "call": 2, "support": 2}
        self._prefix_limits: dict[str, WordPrefixLimit] = {
            "block": "none",
            "call": "none",
            "support": "none",
        }
        self.max_events = max_events
        self.max_bytes = max_bytes
        self.part: WordXmlPart = "word/document.xml"
        self._records: list[_Event] = []
        self._counts = {"block": 0, "call": 0, "support": 0}
        self._bytes = 4096
        self._limited = False
        self._thread: int | None = None
        self._entered = False

    @classmethod
    def for_view_status(cls) -> WordStructureCollector:
        return cls(_view_status_only=True)

    @property
    def collects_coordinates(self) -> bool:
        return not self._view_status_only

    def finish_view_status(self, legacy: WordDetailedExtraction) -> WordViewStatus | None:
        if not self._view_status_only:
            raise ValueError("View status requires its actual status-only observer")
        if self._view_failed:
            return None
        finished = legacy.body.body_state == "read" and all(
            part.state == "read" for part in legacy.notes.parts
        )
        return WordViewStatus(
            known_walk="finished" if finished else "interrupted",
            observed_skipped_blocks=self._view_counts["skipped_blocks"],
            observed_unclassified_nodes=self._view_counts["unclassified_nodes"],
            observed_legacy_recursion=self._view_counts["legacy_recursion"],
            observed_ignored_fallbacks=self._view_counts["ignored_fallbacks"],
        )

    @contextmanager
    def view_scope(self) -> Iterator[None]:
        token = _CURRENT.set(None)
        entered = False
        context = None
        active_error: BaseException | None = None
        try:
            try:
                context = self.scope()
                context.__enter__()
                entered = True
            except Exception:
                self._view_failed = True
                _CURRENT.set(None)
            try:
                yield
            except BaseException as error:
                active_error = error
                raise
        finally:
            try:
                if entered and context is not None:
                    try:
                        context.__exit__(None, None, None)
                    except BaseException as error:
                        if active_error is None and not isinstance(error, Exception):
                            raise
                        self._view_failed = True
            finally:
                _CURRENT.reset(token)

    def report_support(
        self,
        path: tuple[int, ...],
        tag: str,
        child_count: int,
        view: WordStructureView,
        *,
        recognized: bool = True,
    ) -> None:
        if not self._view_status_only:
            self.support(path, tag, child_count, view, recognized=recognized)
            return
        if self._view_failed:
            return
        try:
            self.support(path, tag, child_count, view, recognized=recognized)
        except Exception:
            self._view_failed = True

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
        if self.retention == "semantic-first-v2":
            self._observe_semantic(event)
            return
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

    @staticmethod
    def _semantic_variables_fit(event: _Event, allowance: int) -> bool:
        paths = (
            (event.path, event.path, event.values["paragraph"])
            if event.kind == "call"
            else (event.path,)
        )
        width = 0
        for path in paths:
            if not isinstance(path, tuple):
                raise ValueError("Word event paths must be actual immutable child indices")
            if len(path) > allowance // 2:
                return False
            for index in path:
                if type(index) is not int or not 0 <= index <= 2**63 - 1:
                    raise ValueError("Word event child indices exceed their supported range")
                width += len(str(index)) + 1
                if width > allowance:
                    return False
        field = "identity" if event.kind == "call" else "tag" if event.kind == "support" else None
        if field is None:
            return True
        text = event.values[field]
        if not isinstance(text, str):
            raise ValueError("Word event identity and tag must retain actual text")
        if len(text) > allowance or field == "tag" and len(text) > 4096:
            return False
        for character in text:
            number = ord(character)
            width += (
                2
                if character in ('"', "\\", "\b", "\f", "\n", "\r", "\t")
                else 6
                if number < 32 or 126 < number <= 65535
                else 12
                if number > 65535
                else 1
            )
            if width > allowance:
                return False
        return True

    def _observe_semantic(self, event: _Event) -> None:
        order = self._semantic_order
        self._semantic_order += 1
        if event.kind == "support":
            view = str(event.values["view"])
            if event.values["support"] == "recognized" and view not in WORD_EXCEPTIONAL_VIEWS:
                bucket = (event.xml_part, view)
                self._recognized[bucket] = self._recognized.get(bucket, 0) + 1
                return
            self._exceptional += 1
        kind = event.kind
        if self._prefix_limits[kind] != "none":
            return
        if self._prefix_counts[kind] >= semantic_event_quotas(self.max_events)[kind]:
            self._prefix_limits[kind] = "events"
            return
        if not self._semantic_variables_fit(
            event, SEMANTIC_BYTE_QUOTAS[kind] - self._prefix_bytes[kind]
        ):
            self._prefix_limits[kind] = "bytes"
            return
        location: dict[str, object] = {
            "order": order,
            "ordinal": self._prefix_counts[kind],
            "xml_part": event.xml_part,
            "path": event.path,
            "note_id": f"word-note:{event.xml_part}:{event.path[0]}"
            if event.path and event.xml_part != "word/document.xml"
            else None,
        }
        if kind == "call":
            # Reserve the longest final state and a bounded target before actual note resolution.
            location.update(
                call={
                    "path": event.path,
                    "paragraph_path": event.values["paragraph"],
                    "kind": event.values["kind"],
                    "identity": event.values["identity"],
                    "state": "unverified",
                    "target": 2**63 - 1,
                },
                legacy_view="expanded" if event.values["expanded"] else "missing_marker",
            )
        else:
            location.update(event.values)
        size = word_envelope_bytes(location) + int(self._prefix_counts[kind] > 0)
        if (
            self._prefix_bytes[kind] + size > SEMANTIC_BYTE_QUOTAS[kind]
            or kind == "support"
            and len(str(event.values["tag"])) > 4096
        ):
            self._prefix_limits[kind] = "bytes"
            return
        self._prefix_bytes[kind] += size
        self._prefix_counts[kind] += 1
        self._orders.append(order)
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
        if self._view_status_only:
            if view == "skipped_block":
                self._view_counts["skipped_blocks"] += 1
            if view == "ignored_fallback":
                self._view_counts["ignored_fallbacks"] += 1
            if not recognized:
                field = "legacy_recursion" if view == "recursed" else "unclassified_nodes"
                self._view_counts[field] += 1
            return
        if len(tag) > 4096 and self.retention == "event-prefix-v1":
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
        if self._view_status_only:
            return
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
        if self._view_status_only:
            return
        if len(identity) > self.max_bytes and self.retention == "event-prefix-v1":
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
    ) -> WordStructureExtraction | WordSemanticStructureExtraction:
        blocks: list[WordStructureBlock] = []
        calls: list[WordStructureCall] = []
        support: list[WordStructureSupport] = []
        needed = {(event.xml_part, event.path[0]) for event in self._records if event.path}
        note_ids: dict[tuple[WordXmlPart, int], str] = {
            (note.xml_part, note.path[0]): note.note_id
            for note in legacy.notes.notes
            if (note.xml_part, note.path[0]) in needed
        }
        for index, event in enumerate(self._records):
            order = self._orders[index] if self.retention == "semantic-first-v2" else index
            location = {
                "order": order,
                "xml_part": event.xml_part,
                "path": event.path,
                "note_id": note_ids.get((event.xml_part, event.path[0])) if event.path else None,
            }
            if event.kind == "block":
                block_fields = {**location, **event.values}
                blocks.append(
                    WordSemanticBlock.model_validate({**block_fields, "ordinal": len(blocks)})
                    if self.retention == "semantic-first-v2"
                    else WordStructureBlock.model_validate(block_fields)
                )
            elif event.kind == "support":
                support_fields = {**location, **event.values}
                support.append(
                    WordSemanticSupport.model_validate({**support_fields, "ordinal": len(support)})
                    if self.retention == "semantic-first-v2"
                    else WordStructureSupport.model_validate(support_fields)
                )
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
                call_fields = {
                    **location,
                    "call": call,
                    "legacy_view": "expanded" if event.values["expanded"] else "missing_marker",
                }
                calls.append(
                    WordSemanticCall.model_validate({**call_fields, "ordinal": len(calls)})
                    if self.retention == "semantic-first-v2"
                    else WordStructureCall.model_validate(call_fields)
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
        if self.retention == "semantic-first-v2":
            values.update(
                version="word-semantic-structure-v2",
                scope="legacy-visited-semantic-prefixes",
                blocks=tuple(block.model_dump() for block in blocks),
                calls=tuple(call.model_dump() for call in calls),
                support=tuple(row.model_dump() for row in support),
                exceptional_support_count=self._exceptional,
                recognized_support=tuple(
                    {"xml_part": part, "view": view, "count": count}
                    for (part, view), count in sorted(self._recognized.items())
                ),
                block_limit=self._prefix_limits["block"],
                call_limit=self._prefix_limits["call"],
                support_limit=self._prefix_limits["support"],
                coverage="semantic_complete"
                if complete
                and self._counts["block"] == len(blocks)
                and self._counts["call"] == len(calls)
                else "semantic_partial",
                support_coverage="aggregated"
                if complete and self._exceptional == len(support)
                else "partial",
            )
            values.pop("limit_reached")
            return WordSemanticStructureExtraction.model_validate(values)
        if complete and not self._limited:
            records = [
                {"type": kind, **record.model_dump(mode="json")}
                for kind, items in (("block", blocks), ("call", calls), ("support", support))
                for record in items
            ]
            records.sort(key=lambda entry: int(str(entry["order"])))
            values["observed_stream_sha256"] = structure_sha256(records)
        return WordStructureExtraction.model_validate(values)


@contextmanager
def suspend_word_structure() -> Iterator[None]:
    token = _CURRENT.set(None)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def current_word_structure() -> WordStructureCollector | None:
    value = _CURRENT.get()
    return value if value is not None and value._thread == threading.get_ident() else None
