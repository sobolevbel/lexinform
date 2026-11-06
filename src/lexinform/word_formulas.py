"""Read bounded Office Math source structure without choosing a rendered Word view."""

import hashlib
import io
import zipfile
from dataclasses import dataclass
from typing import Literal
from xml.etree import ElementTree as ET

from lexinform.word_structure import WORDML_NAMESPACES

_MATH = (
    "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "http://purl.oclc.org/ooxml/officeDocument/math",
)
_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}AlternateContent"
_MEMBER = "word/document.xml"
_MAX_XML = 16_777_216
_MAX_FORMULAS = 64
_MAX_FORMULA_NODES = 256
_MAX_FORMULA_DEPTH = 16
_MAX_BODY_NODES = 250_000
_MAX_BODY_DEPTH = 64

Kind = Literal[
    "oMath",
    "f",
    "num",
    "den",
    "d",
    "e",
    "sSub",
    "sSup",
    "sSubSup",
    "sub",
    "sup",
    "r",
    "t",
    "eqArr",
    "dPr",
    "begChr",
    "endChr",
    "sepChr",
    "type",
    "grow",
    "shp",
    "opaque_properties",
]
_UNKNOWN = Literal[
    "unsupported_structure", "formula_limit", "alternate_content", "outside_paragraph"
]
_KINDS: dict[str, Kind] = {
    "oMath": "oMath",
    "f": "f",
    "num": "num",
    "den": "den",
    "d": "d",
    "e": "e",
    "sSub": "sSub",
    "sSup": "sSup",
    "sSubSup": "sSubSup",
    "sub": "sub",
    "sup": "sup",
    "r": "r",
    "t": "t",
    "eqArr": "eqArr",
    "dPr": "dPr",
    "begChr": "begChr",
    "endChr": "endChr",
    "sepChr": "sepChr",
    "type": "type",
    "grow": "grow",
    "shp": "shp",
}
_OPAQUE = frozenset({"fPr", "sSubPr", "sSupPr", "sSubSupPr", "eqArrPr", "rPr", "ctrlPr"})
_SLOTS = {
    "f": ("num", "den"),
    "sSub": ("e", "sub"),
    "sSup": ("e", "sup"),
    "sSubSup": ("e", "sub", "sup"),
}


@dataclass(frozen=True)
class MathNode:
    kind: Kind
    attributes: tuple[tuple[str, str], ...] = ()
    text: str | None = None
    children: tuple[MathNode, ...] = ()
    opaque_tag: str | None = None
    property_sha256: str | None = None


@dataclass(frozen=True)
class FormulaObservation:
    path: tuple[int, ...]
    paragraph_path: tuple[int, ...] | None
    xml_sha256: str | None
    structure: MathNode | None
    unknown: _UNKNOWN | None


@dataclass(frozen=True)
class WordFormulaScan:
    document_xml_sha256: str
    coverage: Literal["complete_body_walk", "prefix"]
    formulas: tuple[FormulaObservation, ...]


class _UnsupportedError(ValueError):
    pass


class _FormulaLimitError(ValueError):
    pass


def read_word_formulas(data: bytes) -> WordFormulaScan:
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        matches = [item for item in package.infolist() if item.filename == _MEMBER]
        if len(matches) != 1 or matches[0].file_size > _MAX_XML:
            raise ValueError("Word formula inventory requires one bounded main XML member")
        with package.open(matches[0]) as stream:
            xml = stream.read(_MAX_XML + 1)
    if len(xml) > _MAX_XML or b"\0" in xml or b"<!DOCTYPE" in xml.upper():
        raise ValueError("Word formula inventory requires bounded XML without a DTD")
    root = ET.fromstring(xml)
    word = next(
        (
            f"{{{namespace}}}"
            for namespace in WORDML_NAMESPACES
            if root.tag == f"{{{namespace}}}document"
        ),
        None,
    )
    if word is None:
        raise ValueError("Expected a supported WordML document root")
    bodies = [(index, node) for index, node in enumerate(root) if node.tag == word + "body"]
    if len(bodies) != 1:
        raise ValueError("Expected one WordML document body")
    index, body = bodies[0]
    formulas, complete = _body_formulas(body, (index,), word)
    return WordFormulaScan(
        hashlib.sha256(xml).hexdigest(), "complete_body_walk" if complete else "prefix", formulas
    )


def _body_formulas(
    body: ET.Element, path: tuple[int, ...], word: str
) -> tuple[tuple[FormulaObservation, ...], bool]:
    todo: list[tuple[ET.Element, tuple[int, ...], tuple[int, ...] | None, bool]] = [
        (body, path, None, False)
    ]
    observations: list[FormulaObservation] = []
    visited = 0
    while todo:
        node, located, paragraph, alternate = todo.pop()
        visited += 1
        if visited > _MAX_BODY_NODES or len(located) > _MAX_BODY_DEPTH:
            return tuple(observations), False
        if node.tag == word + "p":
            paragraph = located
        alternate = alternate or node.tag == _MC
        math = next((f"{{{ns}}}" for ns in _MATH if node.tag == f"{{{ns}}}oMath"), None)
        if math is not None:
            if len(observations) == _MAX_FORMULAS:
                return tuple(observations), False
            observations.append(_observe(node, located, paragraph, alternate, math, word))
            continue
        todo.extend(
            (child, (*located, index), paragraph, alternate)
            for index, child in reversed(tuple(enumerate(node)))
        )
    return tuple(observations), True


def _observe(
    node: ET.Element,
    path: tuple[int, ...],
    paragraph: tuple[int, ...] | None,
    alternate: bool,
    math: str,
    word: str,
) -> FormulaObservation:
    try:
        checksum = _formula_hash(node)
    except _FormulaLimitError:
        return FormulaObservation(path, paragraph, None, None, "formula_limit")
    structure = None
    unknown: _UNKNOWN | None = None
    if alternate:
        unknown = "alternate_content"
    elif paragraph is None:
        unknown = "outside_paragraph"
    else:
        try:
            structure = _structure(node, math, word)
        except _UnsupportedError:
            unknown = "unsupported_structure"
        except _FormulaLimitError:
            unknown = "formula_limit"
    return FormulaObservation(path, paragraph, checksum, structure, unknown)


def _formula_hash(node: ET.Element) -> str:
    pending = [(node, 0)]
    count = 0
    while pending:
        element, depth = pending.pop()
        count += 1
        if count > _MAX_FORMULA_NODES or depth >= _MAX_FORMULA_DEPTH:
            raise _FormulaLimitError()
        pending.extend((child, depth + 1) for child in element)
    return hashlib.sha256(ET.tostring(node)).hexdigest()


def _structure(node: ET.Element, math: str, word: str, parent: str = "") -> MathNode:
    tag = node.tag.removeprefix(math) if node.tag.startswith(math) else ""
    if (node.tail or "").strip():
        raise _UnsupportedError()
    opaque_property = (tag in _OPAQUE and tag == f"{parent}Pr") or (
        parent == "dPr" and tag == "ctrlPr"
    )
    if opaque_property or (parent == "r" and node.tag == word + "rPr"):
        return MathNode(
            "opaque_properties",
            opaque_tag=node.tag,
            property_sha256=hashlib.sha256(ET.tostring(node)).hexdigest(),
        )
    kind = _KINDS.get(tag)
    if kind is None or (tag == "oMath" and parent) or ((node.text or "").strip() and tag != "t"):
        raise _UnsupportedError()
    attributes = tuple(sorted(node.attrib.items()))
    if len(attributes) > 8 or any(len(value) > 256 for _, value in attributes):
        raise _FormulaLimitError()
    content = (node.text or "") if tag == "t" else None
    if content is not None and len(content) > 512:
        raise _FormulaLimitError()
    if tag in ("t", "begChr", "endChr", "sepChr", "type", "grow", "shp") and len(node):
        raise _UnsupportedError()
    names = tuple(child.tag.removeprefix(math) for child in node if child.tag.startswith(math))
    operands = names[1:] if names and names[0] == f"{tag}Pr" else names
    if tag in _SLOTS and operands != _SLOTS[tag]:
        raise _UnsupportedError()
    if tag in ("d", "eqArr") and (not operands or any(name != "e" for name in operands)):
        raise _UnsupportedError()
    children = tuple(_structure(child, math, word, tag) for child in node)
    return MathNode(kind, attributes, content, children)
