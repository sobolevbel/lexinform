"""Typed render ownership and an offline preview for a fixed two-member update."""

import hashlib
from dataclasses import dataclass
from datetime import date
from typing import Literal

from lexinform.telegram_text import MESSAGE_LIMIT, utf16_length

type UpdateOwner = tuple[int, str, int | None, int | None]
type FragmentKind = Literal["header", "member", "member_action", "road", "action", "tags"]


@dataclass(frozen=True)
class UpdateFragment:
    kind: FragmentKind
    owners: tuple[UpdateOwner, ...]
    text: str
    separator: Literal["", "\n\n"] = "\n\n"


@dataclass(frozen=True)
class UpdateFragmentRange:
    kind: FragmentKind
    owners: tuple[UpdateOwner, ...]
    start: int
    end: int


@dataclass(frozen=True)
class GroupedUpdateRender:
    text: str
    members: tuple[UpdateOwner, UpdateOwner]
    ranges: tuple[UpdateFragmentRange, ...]
    today: date
    language: str
    attention: bool

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()


def update_text(fragments: list[UpdateFragment]) -> str:
    chunks: list[str] = []
    for fragment in fragments:
        if fragment.text:
            if chunks:
                chunks.append(fragment.separator)
            chunks.append(fragment.text)
    return "".join(chunks)


def grouped_update_render(
    fragments: list[UpdateFragment],
    members: tuple[UpdateOwner, UpdateOwner],
    *,
    today: date,
    language: str,
    attention: bool,
) -> GroupedUpdateRender | None:
    text = update_text(fragments)
    if len(fragments) > 12 or not text or utf16_length(text) > MESSAGE_LIMIT:
        return None
    ranges: list[UpdateFragmentRange] = []
    end = 0
    for fragment in fragments:
        if fragment.text:
            start = end + (len(fragment.separator.encode()) if ranges else 0)
            end = start + len(fragment.text.encode())
            ranges.append(UpdateFragmentRange(fragment.kind, fragment.owners, start, end))
    result = GroupedUpdateRender(text, members, tuple(ranges), today, language, attention)
    return result if _render_chunks(result) is not None else None


def preview_grouped_update(
    baseline: GroupedUpdateRender,
    candidate: GroupedUpdateRender,
    *,
    owner: UpdateOwner,
    expected_sha256: str,
) -> str | None:
    """Preview only a single member entry change against the exact previous render hash."""
    if (
        baseline.sha256 != expected_sha256
        or owner not in baseline.members
        or (baseline.members, baseline.today, baseline.language, baseline.attention)
        != (candidate.members, candidate.today, candidate.language, candidate.attention)
        or len(baseline.ranges) != len(candidate.ranges)
    ):
        return None
    original = _render_chunks(baseline)
    replacement = _render_chunks(candidate)
    if original is None or replacement is None:
        return None
    edit: tuple[int, int, bytes] | None = None
    for left, right, original_chunk, new_chunk in zip(
        baseline.ranges, candidate.ranges, original, replacement, strict=True
    ):
        if (left.kind, left.owners) != (right.kind, right.owners):
            return None
        original_separator, original_text = original_chunk
        new_separator, new_text = new_chunk
        if original_separator != new_separator:
            return None
        if original_text != new_text:
            if (left.kind, left.owners) != ("member", (owner,)) or edit is not None:
                return None
            edit = left.start, left.end, new_text
    if edit is None:
        return None
    start, end, entry = edit
    source = baseline.text.encode()
    return (source[:start] + entry + source[end:]).decode()


def _render_chunks(render: GroupedUpdateRender) -> tuple[tuple[bytes, bytes], ...] | None:
    if (
        len(render.ranges) > 12
        or len(set(render.members)) != 2
        or len({owner[:2] for owner in render.members}) != 2
        or utf16_length(render.text) > MESSAGE_LIMIT
        or not render.ranges
        or render.ranges[0].kind != "header"
    ):
        return None
    data = render.text.encode()
    chunks = []
    member_entries: list[UpdateOwner] = []
    cursor = 0
    for index, span in enumerate(render.ranges):
        if (
            not cursor <= span.start < span.end <= len(data)
            or len(set(span.owners)) != len(span.owners)
            or any(owner not in render.members for owner in span.owners)
        ):
            return None
        separator = data[cursor : span.start]
        if separator != (b"" if cursor == 0 or span.kind == "member_action" else b"\n\n"):
            return None
        if span.kind == "header" and (index or span.owners):
            return None
        if span.kind != "header" and not span.owners:
            return None
        if span.kind == "member_action" and (
            index == 0
            or (render.ranges[index - 1].kind, render.ranges[index - 1].owners)
            != ("member", span.owners)
        ):
            return None
        if span.kind == "member":
            if len(span.owners) != 1:
                return None
            member_entries.extend(span.owners)
        fragment = data[span.start : span.end]
        try:
            fragment.decode()
        except UnicodeDecodeError:
            return None
        chunks.append((separator, fragment))
        cursor = span.end
    if (
        cursor != len(data)
        or set(member_entries) != set(render.members)
        or len(member_entries) != 2
    ):
        return None
    return tuple(chunks)
