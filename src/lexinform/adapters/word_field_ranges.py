"""Transient paragraph coordinates for emitted text nodes inside Word body fields."""

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import accumulate
from typing import Literal
from xml.etree import ElementTree as ET

from pydantic import ValidationError

from lexinform.models.documents import WordDetailedExtraction, WordTextExtraction
from lexinform.models.extractions import extraction_metadata_sha256
from lexinform.models.word_field_ranges import (
    MAX_WORD_FIELD_RANGES,
    WordFieldParagraph,
    WordFieldPrefix,
    WordFieldSlice,
)

type FieldGroup = Literal["simple_body_text", "before_separator", "after_separator"]
type FieldState = Literal["checked", "partial", "unknown"]
type XmlPath = tuple[int, ...]

_MAX_CANDIDATES = 256
_MAX_BODY_NODES = 250_000
_MAX_PATH_DEPTH = 64


@dataclass(frozen=True)
class WordFieldRange:
    group: FieldGroup
    path: XmlPath
    paragraph_path: XmlPath
    paragraph_sha256: str
    start: int
    end: int
    text_sha256: str


@dataclass(frozen=True)
class WordFieldExtraction:
    legacy: WordTextExtraction
    state: FieldState
    ranges: tuple[WordFieldRange, ...] = ()
    unobserved_nodes: int = 0


class WordFieldRanges:
    def __init__(self) -> None:
        self._groups: dict[ET.Element, FieldGroup] | None = None
        self._pending: dict[XmlPath, list[tuple[ET.Element, XmlPath, int]]] = {}
        self._ranges: list[WordFieldRange] = []
        self._seen: set[ET.Element] = set()
        self._ambiguous = False
        self._markers: set[ET.Element] = set()
        self._expected_markers: list[ET.Element] = []
        self._visited_markers: list[ET.Element] = []

    def classify(self, body: ET.Element, namespace: str) -> None:
        groups: dict[ET.Element, FieldGroup] = {}
        stack: list[Literal["before_separator", "after_separator"]] = []
        pending: list[tuple[Iterator[ET.Element], bool]] = [(iter((body,)), False)]
        nodes_seen = 0
        while pending:
            children, in_simple = pending[-1]
            node = next(children, None)
            if node is None:
                pending.pop()
                continue
            nodes_seen += 1
            if nodes_seen > _MAX_BODY_NODES or len(pending) > _MAX_PATH_DEPTH:
                return
            if node.tag == namespace + "fldSimple":
                in_simple = True
            elif node.tag == namespace + "fldChar":
                if len(self._expected_markers) >= 3 * _MAX_CANDIDATES:
                    return
                self._expected_markers.append(node)
                marker = node.get(namespace + "fldCharType")
                if marker == "begin":
                    stack.append("before_separator")
                elif marker == "separate" and stack and stack[-1] == "before_separator":
                    stack[-1] = "after_separator"
                elif marker == "end" and stack:
                    stack.pop()
                else:
                    return
            elif node.tag == namespace + "t" and node.text and (in_simple or stack):
                group: FieldGroup = "simple_body_text" if in_simple else stack[-1]
                groups[node] = group
                if len(groups) > _MAX_CANDIDATES:
                    return
            pending.append((iter(node), in_simple))
        if not stack:
            self._groups = groups
            self._markers = set(self._expected_markers)

    def visit_marker(self, node: ET.Element) -> None:
        if node in self._markers:
            self._visited_markers.append(node)

    def fragment(
        self, node: ET.Element, path: XmlPath, paragraph: XmlPath, fragment_index: int
    ) -> None:
        if self._groups is not None and node in self._groups:
            if node in self._seen:
                self._ambiguous = True
                return
            self._seen.add(node)
            self._pending.setdefault(paragraph, []).append((node, path, fragment_index))

    def paragraph(self, path: XmlPath, fragments: list[str], text: str) -> None:
        events = self._pending.pop(path, None)
        groups = self._groups
        if events is None or groups is None:
            return
        offsets = tuple(accumulate(map(len, fragments), initial=0))
        paragraph_sha256 = hashlib.sha256(text.encode()).hexdigest()
        for node, node_path, index in events:
            self._ranges.append(
                WordFieldRange(
                    group=groups[node],
                    path=node_path,
                    paragraph_path=path,
                    paragraph_sha256=paragraph_sha256,
                    start=offsets[index],
                    end=offsets[index + 1],
                    text_sha256=hashlib.sha256(fragments[index].encode()).hexdigest(),
                )
            )

    def prefix(self, detailed: WordDetailedExtraction) -> WordFieldPrefix | None:
        legacy = detailed.body
        observed = self.finish(legacy)
        if observed.state != "checked" or not observed.ranges:
            return None
        blocks = {block.path: block for block in legacy.blocks if block.kind == "paragraph"}
        slices: dict[XmlPath, list[WordFieldSlice]] = {}
        for event in observed.ranges[:MAX_WORD_FIELD_RANGES]:
            relative = event.path[len(event.paragraph_path) :]
            slices.setdefault(event.paragraph_path, []).append(
                WordFieldSlice(relative, event.group, event.start, event.end, event.text_sha256)
            )
        paragraphs = [
            WordFieldParagraph(
                path=path,
                text_sha256=blocks[path].text_sha256,
                text_chars=len(blocks[path].text),
                ranges=tuple(ranges),
            )
            for path, ranges in slices.items()
        ]
        manifest = extraction_metadata_sha256(
            (legacy.manifest_sha256, detailed.notes.manifest_sha256)
        )
        while paragraphs:
            try:
                return WordFieldPrefix(
                    input_sha256=legacy.input_sha256,
                    manifest_sha256=manifest,
                    paragraphs=tuple(paragraphs),
                )
            except ValidationError:
                tail = paragraphs.pop()
                if len(tail.ranges) > 1:
                    paragraphs.append(tail.model_copy(update={"ranges": tail.ranges[:-1]}))
        return None

    def finish(self, legacy: WordTextExtraction) -> WordFieldExtraction:
        if (
            self._groups is None
            or self._ambiguous
            or self._pending
            or self._visited_markers != self._expected_markers
        ):
            return WordFieldExtraction(legacy, "unknown")
        paragraphs = {block.path: block for block in legacy.blocks if block.kind == "paragraph"}
        for event in self._ranges:
            block = paragraphs.get(event.paragraph_path)
            if (
                block is None
                or block.text_sha256 != event.paragraph_sha256
                or not 0 <= event.start < event.end <= len(block.text)
                or hashlib.sha256(block.text[event.start : event.end].encode()).hexdigest()
                != event.text_sha256
            ):
                return WordFieldExtraction(legacy, "unknown")
        missing = len(self._groups) - len(self._ranges)
        return WordFieldExtraction(
            legacy, "partial" if missing else "checked", tuple(self._ranges), missing
        )
