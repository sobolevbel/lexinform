"""Exact source positions for copied text and explicit gaps for generated text."""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class SourcePiece:
    text: str
    source_start: int | None = None


@dataclass(frozen=True)
class TextSegment:
    start: int
    end: int
    source_start: int | None = None


@dataclass(frozen=True)
class TextProjection:
    text: str
    source_sha256: str
    source_length: int
    segments: tuple[TextSegment, ...]

    def __post_init__(self) -> None:
        end = 0
        for segment in self.segments:
            if segment.start != end or segment.end <= segment.start:
                raise ValueError("Projection segments must partition the emitted text")
            if segment.source_start is not None and (
                segment.source_start < 0
                or segment.source_start + segment.end - segment.start > self.source_length
            ):
                raise ValueError("Projection segment escapes its source")
            end = segment.end
        if end != len(self.text):
            raise ValueError("Projection segments must cover the emitted text")

    def slice(self, start: int, end: int) -> TextProjection:
        if not 0 <= start <= end <= len(self.text):
            raise ValueError("Projection slice escapes its emitted text")
        segments: list[TextSegment] = []
        for segment in self.segments:
            begin, stop = max(start, segment.start), min(end, segment.end)
            if begin >= stop:
                continue
            origin = (
                None
                if segment.source_start is None
                else segment.source_start + begin - segment.start
            )
            segments.append(TextSegment(begin - start, stop - start, origin))
        return TextProjection(
            self.text[start:end], self.source_sha256, self.source_length, tuple(segments)
        )


def project_pieces(
    source: str, pieces: Sequence[SourcePiece], *, separator: str = "", strip: bool = False
) -> TextProjection:
    fragments: list[str] = []
    segments: list[TextSegment] = []
    offset = 0

    def append(text: str, origin: int | None) -> None:
        nonlocal offset
        if not text:
            return
        if origin is not None and (origin < 0 or source[origin : origin + len(text)] != text):
            raise ValueError("Copied text differs from its declared source position")
        fragments.append(text)
        if segments and (
            (origin is None and segments[-1].source_start is None)
            or (
                origin is not None
                and segments[-1].source_start is not None
                and segments[-1].source_start + segments[-1].end - segments[-1].start == origin
            )
        ):
            last = segments[-1]
            segments[-1] = TextSegment(last.start, offset + len(text), last.source_start)
        else:
            segments.append(TextSegment(offset, offset + len(text), origin))
        offset += len(text)

    for index, piece in enumerate(pieces):
        if index:
            previous = pieces[index - 1]
            origin = None
            if (
                previous.source_start is not None
                and piece.source_start is not None
                and previous.source_start + len(previous.text) + len(separator)
                == piece.source_start
                and source[piece.source_start - len(separator) : piece.source_start] == separator
            ):
                origin = piece.source_start - len(separator)
            append(separator, origin)
        append(piece.text, piece.source_start)
    text = "".join(fragments)
    result = TextProjection(
        text, hashlib.sha256(source.encode()).hexdigest(), len(source), tuple(segments)
    )
    if strip:
        start = len(text) - len(text.lstrip())
        return result.slice(start, max(start, len(text.rstrip())))
    return result
