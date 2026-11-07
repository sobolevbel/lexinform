"""Bound exact copied extraction spans to the same-pass native PDF and ODT blocks."""

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from lexinform.models.block_input import MAX_BLOCK_INPUT_ROWS, BlockCopyRun, BlockCopyWitness
from lexinform.models.documents import OdtTextExtraction, PdfTextExtraction
from lexinform.pdf_projection import pdf_copy_spans

_CURRENT: ContextVar[NativeBlockCopies | None] = ContextVar("native_block_copies", default=None)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _witness(parent: dict[str, object], offered: Iterator[BlockCopyRun]) -> BlockCopyWitness | None:
    rows: list[BlockCopyRun] = []
    for row in offered:
        if len(rows) == MAX_BLOCK_INPUT_ROWS:
            break
        rows.append(row)
        try:
            BlockCopyWitness.model_validate({**parent, "runs": tuple(rows)})
        except ValueError:
            rows.pop()
            break
    return BlockCopyWitness.model_validate({**parent, "runs": tuple(rows)}) if rows else None


def pdf_block_copies(body: PdfTextExtraction) -> BlockCopyWitness | None:
    if body.error_type is not None:
        return None
    text = body.legacy_text
    parent: dict[str, object] = {
        "profile": "native-block-copies-v1",
        "coordinates": body.version,
        "text_view": body.legacy_projection,
        "manifest_sha256": body.manifest_sha256,
        "notes_manifest_sha256": None,
        "part_sha256": body.input_sha256,
        "text_sha256": _sha(text),
        "text_chars": len(text),
    }

    def rows() -> Iterator[BlockCopyRun]:
        for start, end, page_index, left, right in pdf_copy_spans(
            tuple(page.text for page in body.pages)
        ):
            page = body.pages[page_index]
            if page.error_type is None:
                yield BlockCopyRun(
                    source_start=start,
                    source_end=end,
                    block_id=page.block_id,
                    block_start=left,
                    block_end=right,
                    block_chars=len(page.text),
                    block_text_sha256=page.text_sha256,
                    text_sha256=_sha(text[start:end]),
                )

    return _witness(parent, rows())


class NativeBlockCopies:
    def __init__(self) -> None:
        self.odt: dict[str, BlockCopyWitness] = {}

    @contextmanager
    def scope(self) -> Iterator[None]:
        token = _CURRENT.set(self)
        try:
            yield
        finally:
            _CURRENT.reset(token)

    def finish_odt(self, body: OdtTextExtraction, supported: set[tuple[int, ...]]) -> None:
        if body.body_state != "read":
            return
        text = body.legacy_text
        blocks = {block.block_id: block for block in body.blocks}
        parent: dict[str, object] = {
            "profile": "native-block-copies-v1",
            "coordinates": body.version,
            "text_view": body.text_view,
            "manifest_sha256": body.manifest_sha256,
            "notes_manifest_sha256": None,
            "xml_sha256": body.xml_sha256,
            "part_sha256": body.input_sha256,
            "text_sha256": _sha(text),
            "text_chars": len(text),
        }

        def rows() -> Iterator[BlockCopyRun]:
            start = 0
            for identity in body.legacy_blocks:
                block = blocks[identity]
                end = start + len(block.text)
                if block.path in supported and block.text and text[start:end] == block.text:
                    yield BlockCopyRun(
                        source_start=start,
                        source_end=end,
                        block_id=block.block_id,
                        block_start=0,
                        block_end=len(block.text),
                        block_chars=len(block.text),
                        block_text_sha256=block.text_sha256,
                        text_sha256=block.text_sha256,
                    )
                start = end + 1

        witness = _witness(parent, rows())
        if witness is not None:
            self.odt[body.input_sha256] = witness


def current_native_block_copies() -> NativeBlockCopies | None:
    return _CURRENT.get()
