"""Retain an exact optional Word witness for one observed amendment member."""

import hashlib
from io import BytesIO
from urllib.parse import urlparse
from zipfile import ZipFile

from lexinform.adapters.document_text import DocxTextExtractor
from lexinform.models.extractions import (
    DocumentExtraction,
    ExtractionResult,
    ObservedMemberBlockCopy,
)
from lexinform.physical_walk_capture import suspend_physical_walk_capture
from lexinform.rcl_amendments import has_rcl_self_amendment_text, rcl_self_amendment_print_number

_MAX_MEMBER_BYTES = 4 * 1024 * 1024
_MAX_XML_BYTES = 2 * 1024 * 1024
_WORD_XML = {"word/document.xml", "word/footnotes.xml", "word/endnotes.xml"}


def enrich_rcl_observed_word_source(
    rcl_host: str, reader: DocxTextExtractor, url: str, data: bytes, observed: DocumentExtraction
) -> DocumentExtraction:
    location = urlparse(url)
    if location.hostname != rcl_host or not location.path.casefold().endswith(".zip"):
        return observed
    selection, result = observed.document.selection, observed.result
    if (
        selection.state != "unsupported"
        or observed.document.archive_unpack_blocked
        or result.rejected_count
        or result.omitted_selected_parts
        or result.physical_part_count != len(selection.observed_parts)
        or result.observed_member_block_copy is not None
    ):
        return observed
    selected = {part.physical_index for part in result.selected_parts}
    candidates = [
        (index, part)
        for index, part in enumerate(selection.observed_parts)
        if index not in selected
        and len(part.members) == 1
        and part.members[0].casefold().endswith((".docx", ".docm"))
        and not part.losses
        and rcl_self_amendment_print_number(part.text) is not None
        and has_rcl_self_amendment_text(part.text)
    ]
    if len(candidates) != 1 or hashlib.sha256(data).hexdigest() != result.artifact_sha256:
        return observed
    index, part = candidates[0]
    with ZipFile(BytesIO(data)) as archive:
        members = [item for item in archive.infolist() if item.filename == part.members[0]]
        if len(members) != 1 or members[0].file_size > _MAX_MEMBER_BYTES:
            return observed
        member = archive.read(members[0])
    if hashlib.sha256(member).hexdigest() != part.sha256:
        return observed
    with ZipFile(BytesIO(member)) as word:
        entries = [item for item in word.infolist() if item.filename in _WORD_XML]
        if (
            len(entries) != len({item.filename for item in entries})
            or sum(item.file_size for item in entries) > _MAX_XML_BYTES
        ):
            return observed
    with suspend_physical_walk_capture():
        witness = reader.extract_with_view_status(member).block_copy_witness
    if witness is None or (witness.part_sha256, witness.text_sha256, witness.text_chars) != (
        part.sha256,
        part.text_sha256,
        len(part.text),
    ):
        return observed
    captured = ObservedMemberBlockCopy(physical_index=index, witness=witness)
    enriched = ExtractionResult.model_validate(
        {**result.model_dump(), "observed_member_block_copy": captured}
    )
    return observed.model_copy(update={"result": enriched})
