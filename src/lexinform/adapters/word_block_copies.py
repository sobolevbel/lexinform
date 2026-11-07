"""Capture plain XML prefixes during the unchanged native Word traversal."""

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from lexinform.adapters.word_field_projection import legacy_word_layout
from lexinform.models.block_input import MAX_BLOCK_INPUT_ROWS, BlockCopyRun, BlockCopyWitness
from lexinform.models.documents import WordDetailedExtraction
from lexinform.models.extractions import extraction_metadata_sha256
from lexinform.word_projection import _TIDY_PAGE_BREAK

_CURRENT: ContextVar[PlainWordCopies | None] = ContextVar("plain_word_copies", default=None)


class _PlainPrefix:
    def __init__(self) -> None:
        self.chars = 0
        self.digest = hashlib.sha256()

    def append(self, text: str) -> None:
        self.chars += len(text)
        for start in range(0, len(text), 4096):
            self.digest.update(text[start : start + 4096].encode())


def _span_sha(text: str, start: int, end: int) -> str:
    digest = hashlib.sha256()
    for position in range(start, end, 4096):
        digest.update(text[position : min(position + 4096, end)].encode())
    return digest.hexdigest()


class PlainWordCopies:
    def __init__(self) -> None:
        self._prefixes: dict[tuple[int, ...], _PlainPrefix] = {}
        self._failed = False
        self._stopped: set[tuple[int, ...]] = set()

    @contextmanager
    def scope(self) -> Iterator[None]:
        token = _CURRENT.set(self)
        try:
            yield
        finally:
            _CURRENT.reset(token)

    def fragment(self, paragraph: tuple[int, ...], text: str, *, plain: bool) -> None:
        if paragraph not in self._prefixes:
            if len(self._prefixes) == MAX_BLOCK_INPUT_ROWS:
                return
            self._prefixes[paragraph] = _PlainPrefix()
        if paragraph in self._stopped:
            return
        if not plain:
            self._stopped.add(paragraph)
        else:
            self._prefixes[paragraph].append(text)

    def fail(self) -> None:
        self._failed = True
        self._prefixes.clear()

    def finish(self, legacy: WordDetailedExtraction) -> BlockCopyWitness | None:
        if self._failed:
            return None
        body = legacy.body
        layout = legacy_word_layout(body)
        if body.body_state != "read" or layout is None:
            return None
        raw, offsets = layout
        text = body.legacy_text
        removed = tuple(
            index
            for match in _TIDY_PAGE_BREAK.finditer(raw)
            for index in range(*match.span())
            if raw[index] == "\n"
        )
        blocks = {block.path: block for block in body.blocks}
        manifest = body.manifest_sha256
        notes_manifest = legacy.notes.manifest_sha256
        parent = {
            "manifest_sha256": extraction_metadata_sha256((manifest, notes_manifest)),
            "body_manifest_sha256": manifest,
            "notes_manifest_sha256": notes_manifest,
            "xml_sha256": body.xml_sha256,
            "part_sha256": body.input_sha256,
            "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "text_chars": len(text),
        }
        rows: list[BlockCopyRun] = []
        for path, prefix in self._prefixes.items():
            chars, copied_sha = prefix.chars, prefix.digest.hexdigest()
            block, base = blocks.get(path), offsets.get(path)
            if not chars or block is None or base is None or chars > len(block.text):
                continue
            start = base - sum(index < base for index in removed)
            if (
                _span_sha(block.text, 0, chars) != copied_sha
                or _span_sha(text, start, start + chars) != copied_sha
            ):
                continue
            rows.append(
                BlockCopyRun(
                    source_start=start,
                    source_end=start + chars,
                    block_id=block.block_id,
                    block_start=0,
                    block_end=chars,
                    block_chars=len(block.text),
                    block_text_sha256=block.text_sha256,
                    text_sha256=copied_sha,
                )
            )
            try:
                BlockCopyWitness.model_validate({**parent, "runs": tuple(rows)})
            except ValueError:
                rows.pop()
                break
        return BlockCopyWitness.model_validate({**parent, "runs": tuple(rows)}) if rows else None


def current_plain_word_copies() -> PlainWordCopies | None:
    return _CURRENT.get()
