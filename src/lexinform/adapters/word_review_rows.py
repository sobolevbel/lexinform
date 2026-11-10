import re
from collections.abc import Iterator, Sequence
from xml.etree import ElementTree as ET

from lexinform.models.documents import WordTextBlock, WordTextExtraction
from lexinform.models.reviews import ConsultationRow, consultation_paragraph_decision
from lexinform.sections import PAGE_BREAK
from lexinform.word_projection import legacy_word_text

_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_LITERAL = re.compile(r"\s*([0-9]+)[.)]?\s*\Z")


def _value(node: ET.Element, name: str) -> str:
    child = node.find(_NS + name)
    return child.get(_NS + "val", "") if child is not None else ""


def _numbering_levels(root: ET.Element) -> dict[tuple[str, int], tuple[int, str]]:
    levels: dict[tuple[str, int], tuple[int, str]] = {}
    abstracts = {
        node.get(_NS + "abstractNumId", ""): node for node in root.findall(_NS + "abstractNum")
    }
    for instance in root.findall(_NS + "num"):
        identity = instance.get(_NS + "numId", "")
        abstract = abstracts.get(_value(instance, "abstractNumId"))
        if not identity or abstract is None or abstract.find(_NS + "numStyleLink") is not None:
            continue
        for index in (0, 1):
            level = abstract.find(f"{_NS}lvl[@{_NS}ilvl='{index}']")
            override = instance.find(f"{_NS}lvlOverride[@{_NS}ilvl='{index}']")
            if override is not None:
                replacement = override.find(_NS + "lvl")
                if replacement is not None:
                    level = replacement
            if level is None or _value(level, "lvlRestart") not in ("", "1"):
                continue
            style = _value(level, "numFmt")
            if style not in ("decimal", "lowerLetter", "upperLetter"):
                continue
            pattern = _value(level, "lvlText")
            if pattern not in (f"%{index + 1}", f"%{index + 1}.", f"%{index + 1})"):
                continue
            start = _value(override, "startOverride") if override is not None else ""
            start = start or _value(level, "start")
            if start.isascii() and start.isdecimal() and len(start) <= 6:
                levels[(identity, index)] = (int(start), style)
    return levels


def _walk(
    node: ET.Element, path: tuple[int, ...] = ()
) -> Iterator[tuple[ET.Element, tuple[int, ...]]]:
    for index, child in enumerate(node):
        address = (*path, index)
        if child.tag in (_NS + "del", _NS + "moveFrom"):
            continue
        yield child, address
        yield from _walk(child, address)


def _paragraph_numbers(
    body: ET.Element, levels: dict[tuple[str, int], tuple[int, str]]
) -> dict[tuple[int, ...], str]:
    labels: dict[tuple[int, ...], str] = {}
    counters: dict[tuple[str, int], int] = {}
    parents: dict[str, str] = {}
    for node, address in _walk(body):
        if node.tag != _NS + "p":
            continue
        props = node.find(f"{_NS}pPr/{_NS}numPr")
        if props is None:
            continue
        identity = _value(props, "numId")
        level = _value(props, "ilvl")
        if level not in ("", "0", "1"):
            continue
        index = 1 if level == "1" else 0
        key = (identity, index)
        definition = levels.get(key)
        if definition is None:
            if index == 0:
                parents.pop(identity, None)
                counters.pop((identity, 1), None)
            continue
        start, style = definition
        number = counters.get(key, start)
        counters[key] = number + 1
        if style == "decimal":
            label = str(number)
        elif 1 <= number <= 26:
            label = chr(ord("a" if style == "lowerLetter" else "A") + number - 1)
        else:
            continue
        if index == 0:
            parents[identity] = label
            counters.pop((identity, 1), None)
            labels[address] = label
        elif identity in parents:
            labels[address] = f"{parents[identity]}/{label}"
    return labels


def bind_consultation_rows(
    text: str, extraction: WordTextExtraction, rows: tuple[tuple[WordTextBlock, str | None], ...]
) -> tuple[ConsultationRow, ...]:
    blocks = {block.block_id: block for block in extraction.blocks}
    labels = {row.block_id: label for row, label in rows}
    cells: dict[tuple[int, ...], list[str]] = {}
    for block in extraction.blocks:
        if block.kind == "table_cell":
            cells.setdefault(block.path[:-1], []).append(block.text)
    bound: list[ConsultationRow] = []
    cursor = 0
    for identity in extraction.legacy_blocks:
        block = blocks[identity]
        if block.kind == "table_cell":
            continue
        paragraph = block.kind == "paragraph"
        if paragraph and consultation_paragraph_decision(block.text) is None:
            continue
        content = legacy_word_text((block.text,))
        if not content.strip():
            continue
        start = text.find(content, cursor)
        end = start + len(content)
        if (
            start < 0
            or (
                start
                and text[start - 1] not in ("\n", PAGE_BREAK)
                and not content.startswith(PAGE_BREAK)
            )
            or (
                end < len(text)
                and text[end] not in ("\n", PAGE_BREAK)
                and not content.endswith(PAGE_BREAK)
            )
        ):
            if paragraph:
                continue
            return ()
        bound.append(
            ConsultationRow(
                locator=identity,
                label=labels.get(identity),
                start=start,
                end=end,
                kind="paragraph" if paragraph else "row",
                response_offset=(
                    None
                    if paragraph
                    else _response_offset(block.text, content, cells.get(block.path, ()))
                ),
            )
        )
        cursor = end
    return tuple(bound)


def _response_offset(raw: str, text: str, cells: Sequence[str]) -> int | None:
    if len(cells) < 2 or raw != "\t".join(cells):
        return None
    response = legacy_word_text((cells[-1],))
    offset = len(text) - len(response)
    if response.strip() and offset > 0 and text.endswith("\t" + response):
        return offset
    return None


def consultation_word_rows(
    extraction: WordTextExtraction,
    root: ET.Element,
    numbering: ET.Element | None,
) -> tuple[tuple[WordTextBlock, str | None], ...]:
    """Return exact Word rows with independently located source labels."""
    body = root.find(_NS + "body")
    if body is None:
        return ()
    body_path = (list(root).index(body),)
    numbers = (
        _paragraph_numbers(body, _numbering_levels(numbering)) if numbering is not None else {}
    )
    blocks = {block.block_id: block for block in extraction.blocks}
    first_cells: dict[tuple[int, ...], tuple[ET.Element, tuple[int, ...]]] = {}
    rows: list[tuple[WordTextBlock, str | None]] = []
    parent_row: tuple[int, ...] = ()
    for node, address in _walk(body):
        if node.tag == _NS + "tr":
            parent_row = address
        elif node.tag == _NS + "tc" and address[: len(parent_row)] == parent_row:
            first_cells.setdefault(parent_row, (node, address))
    for identity in extraction.legacy_blocks:
        block = blocks[identity]
        if block.kind != "table_row":
            continue
        first = first_cells.get(block.path[1:]) if block.path[:1] == body_path else None
        label: str | None = None
        literal = _LITERAL.fullmatch(block.text.split("\t", 1)[0])
        if literal:
            label = literal[1]
        elif first is not None:
            cell, address = first
            paragraph = next(
                (path for node, path in _walk(cell, address) if node.tag == _NS + "p"), None
            )
            if paragraph is not None:
                label = numbers.get(paragraph)
        rows.append((block, label))
    return tuple(rows)
