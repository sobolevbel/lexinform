"""Bound the exact Word formula source data allowed into a frozen analysis request."""

import json
from typing import Annotated, Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lexinform.models.physical_inventory import Sha256
from lexinform.word_formulas import NARY_SOURCE_KINDS, Kind, MathNode

_MAX_BYTES = 4096
_INT = Annotated[int, Field(strict=True, ge=0, le=16_777_216)]
_HEADER = "\n\n=== STRUKTURA ŹRÓDŁOWA WZORÓW WORD (OMML) ===\n"
_KINDS = frozenset(get_args(Kind)) - NARY_SOURCE_KINDS
_SLOTS = {
    "f": ("num", "den"),
    "sSub": ("e", "sub"),
    "sSup": ("e", "sup"),
    "sSubSup": ("e", "sub", "sup"),
}


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def encode_formula_tree(node: MathNode) -> str:
    def encode(value: MathNode) -> object:
        return [
            value.kind,
            dict(value.attributes),
            value.text,
            [encode(child) for child in value.children],
            value.opaque_tag,
            value.property_sha256,
        ]

    return _canonical(encode(node))


def _validate_tree(tree: object) -> None:
    pending = [(tree, 0)]
    count = 0
    while pending:
        node, depth = pending.pop()
        count += 1
        if count > 256 or depth >= 16 or not isinstance(node, list) or len(node) != 6:
            raise ValueError("Invalid bounded Word formula tree")
        kind, attrs, token, children, opaque_tag, opaque_sha = node
        if (
            not isinstance(kind, str)
            or kind not in _KINDS
            or (kind == "oMath") != (depth == 0)
            or not isinstance(attrs, dict)
            or len(attrs) > 8
            or any(not isinstance(value, str) or len(value) > 256 for value in attrs.values())
            or not isinstance(children, list)
        ):
            raise ValueError("Invalid Word formula node")
        if kind == "t":
            if not isinstance(token, str) or len(token) > 512 or children:
                raise ValueError("Invalid Word formula token")
        elif token is not None:
            raise ValueError("Unexpected Word formula token")
        if kind == "opaque_properties":
            if (
                not isinstance(opaque_tag, str)
                or not isinstance(opaque_sha, str)
                or len(opaque_sha) != 64
                or any(char not in "0123456789abcdef" for char in opaque_sha)
                or attrs
                or children
            ):
                raise ValueError("Invalid Word formula property reference")
        elif opaque_tag is not None or opaque_sha is not None:
            raise ValueError("Unexpected Word formula property reference")
        operands = tuple(child[0] for child in children if isinstance(child, list) and child)
        if operands and operands[0] in ("opaque_properties", "dPr"):
            operands = operands[1:]
        if (
            (kind in _SLOTS and operands != _SLOTS[kind])
            or (kind in ("d", "eqArr") and (not operands or any(slot != "e" for slot in operands)))
            or (kind in ("begChr", "endChr", "sepChr", "type", "grow", "shp") and children)
        ):
            raise ValueError("Invalid Word formula operand order")
        pending.extend((child, depth + 1) for child in children)


class WordFormulaInputEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    paragraph_path: tuple[_INT, _INT]
    formula_path: tuple[_INT, ...] = Field(min_length=3, max_length=16)
    subtree_sha256: Sha256
    source_anchor: tuple[_INT, _INT]
    emitted_anchor: tuple[_INT, _INT]
    source_gap: _INT
    emitted_gap: _INT
    tree: str = Field(min_length=1, max_length=_MAX_BYTES)

    @field_validator("tree")
    @classmethod
    def canonical_tree(cls, value: str) -> str:
        if len(value.encode()) > _MAX_BYTES:
            raise ValueError("Word formula tree exceeds byte limit")
        try:
            decoded = json.loads(value)
        except (ValueError, RecursionError) as exc:
            raise ValueError("Invalid Word formula encoding") from exc
        _validate_tree(decoded)
        if _canonical(decoded) != value:
            raise ValueError("Expected canonical Word formula encoding")
        return value

    @model_validator(mode="after")
    def aligned_anchor(self) -> Self:
        src, src_end = self.source_anchor
        dst, dst_end = self.emitted_anchor
        if (
            self.formula_path[:2] != self.paragraph_path
            or not src < self.source_gap < src_end
            or not dst < self.emitted_gap < dst_end
            or src_end - src != dst_end - dst
            or self.source_gap - src != self.emitted_gap - dst
        ):
            raise ValueError("Word formula anchors do not agree")
        return self


class WordFormulaInputV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: Literal["rcl-singleton-word-omml-v1"] = "rcl-singleton-word-omml-v1"
    selection_id: str = Field(pattern=r"^selection-v1:[0-9a-f]{64}$", max_length=77)
    source_observation_id: str = Field(
        pattern=r"^source-observation-v1:[0-9a-f]{64}$", max_length=86
    )
    artifact_sha256: Sha256
    document_xml_sha256: Sha256
    formulas: tuple[WordFormulaInputEntry, ...] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def ordered_and_bounded(self) -> Self:
        for left, right in zip(self.formulas, self.formulas[1:], strict=False):
            if (
                left.paragraph_path >= right.paragraph_path
                or left.source_anchor[1] > right.source_anchor[0]
                or left.emitted_anchor[1] > right.emitted_anchor[0]
            ):
                raise ValueError("Word formula positions overlap or are out of order")
        if len(self.model_dump_json().encode()) > _MAX_BYTES:
            raise ValueError("Word formula input exceeds byte limit")
        return self

    def prompt_suffix(self) -> str:
        formulas = [[item.emitted_gap, json.loads(item.tree)] for item in self.formulas]
        return _HEADER + _canonical({"profile": self.profile, "formulas": formulas})
