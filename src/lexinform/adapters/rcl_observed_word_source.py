"""Capture bounded Word block evidence for exact RCL archive members."""

import hashlib
from io import BytesIO
from urllib.parse import urlparse
from zipfile import ZipFile

from lexinform.adapters.document_text import DocxTextExtractor
from lexinform.input_receipts import archive_member_matches
from lexinform.models import DocumentPart
from lexinform.models.block_input import BlockCopyWitness
from lexinform.models.extractions import (
    MAX_SELECTED_BILL_BLOCK_SAMPLE_BYTES,
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
) -> tuple[DocumentExtraction, ObservedMemberBlockCopy | None]:
    location = urlparse(url)
    if location.hostname != rcl_host or not location.path.casefold().endswith(".zip"):
        return observed, None
    selection, result = observed.document.selection, observed.result
    if (
        observed.document.archive_unpack_blocked
        or result.rejected_count
        or result.omitted_selected_parts
        or result.physical_part_count != len(selection.observed_parts)
        or result.observed_member_block_copy is not None
    ):
        return observed, None
    if selection.state in ("selected", "unverified"):
        return observed, _selected_bill_sample(reader, data, observed)
    if selection.state != "unsupported":
        return observed, None
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
        return observed, None
    index, part = candidates[0]
    witness = _read_member_witness(reader, data, part)
    if witness is None:
        return observed, None
    captured = ObservedMemberBlockCopy(physical_index=index, witness=witness)
    enriched = ExtractionResult.model_validate(
        {**result.model_dump(), "observed_member_block_copy": captured}
    )
    return observed.model_copy(update={"result": enriched}), None


def _selected_bill_sample(
    reader: DocxTextExtractor, data: bytes, observed: DocumentExtraction
) -> ObservedMemberBlockCopy | None:
    bills = [part for part in observed.document.selection.parts if part.role == "bill"]
    if len(bills) != 1:
        return None
    (part,) = bills
    if (
        len(part.members) != 1
        or not part.members[0].casefold().endswith((".docx", ".docm"))
        or part.losses
    ):
        return None
    result = observed.result
    summaries = [
        item
        for item in result.selected_parts
        if item.block_copy_witness is None
        and archive_member_matches(part, item, chars=len(part.text))
    ]
    if len(summaries) != 1 or hashlib.sha256(data).hexdigest() != result.artifact_sha256:
        return None
    (summary,) = summaries
    witness = _read_member_witness(reader, data, part)
    if witness is None or witness.manifest_sha256 != summary.manifest_sha256:
        return None
    for run in sorted(witness.runs, key=lambda item: item.source_start - item.source_end):
        sample = ObservedMemberBlockCopy(
            physical_index=summary.physical_index,
            witness=witness.model_copy(update={"runs": (run,)}),
        )
        if len(sample.model_dump_json().encode()) <= MAX_SELECTED_BILL_BLOCK_SAMPLE_BYTES:
            return sample
    return None


def _read_member_witness(
    reader: DocxTextExtractor, data: bytes, part: DocumentPart
) -> BlockCopyWitness | None:
    with ZipFile(BytesIO(data)) as archive:
        members = [item for item in archive.infolist() if item.filename == part.members[0]]
        if len(members) != 1 or members[0].file_size > _MAX_MEMBER_BYTES:
            return None
        member = archive.read(members[0])
    if hashlib.sha256(member).hexdigest() != part.sha256:
        return None
    with ZipFile(BytesIO(member)) as word:
        entries = [item for item in word.infolist() if item.filename in _WORD_XML]
        if (
            len(entries) != len({item.filename for item in entries})
            or sum(item.file_size for item in entries) > _MAX_XML_BYTES
        ):
            return None
    with suspend_physical_walk_capture():
        witness = reader.extract_with_view_status(member).block_copy_witness
    if witness is None or (witness.part_sha256, witness.text_sha256, witness.text_chars) != (
        part.sha256,
        part.text_sha256,
        len(part.text),
    ):
        return None
    return witness
