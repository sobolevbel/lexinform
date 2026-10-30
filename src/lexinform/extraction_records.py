"""Summarize physical extraction without persisting block text or unbounded archive members."""

import hashlib
import inspect
import sys
from collections import Counter
from functools import lru_cache
from importlib import metadata
from pathlib import Path
from typing import Literal

from lexinform.document_candidates import select_document_parts
from lexinform.models.documents import ExtractedDocument, ExtractedParts, WordDetailedExtraction
from lexinform.models.extractions import (
    MAX_EXTRACTION_METADATA_BYTES,
    MAX_EXTRACTION_PART_SUMMARIES,
    ExtractedPartSummary,
    ExtractionComponent,
    ExtractionDependency,
    ExtractionOptions,
    ExtractionResult,
    OdtNoteStatus,
    extraction_metadata_sha256,
)
from lexinform.models.odt_notes import OdtNotedParts, OdtPartNoteOrigins
from lexinform.models.word_view_capture import WordPartViewStatus, WordViewExtraction


@lru_cache(maxsize=32)
def component_code(source_path: str) -> str | None:
    try:
        return hashlib.sha256(Path(source_path).read_bytes()).hexdigest()
    except OSError:
        return None


def extraction_component(
    role: Literal["package", "pdf", "word", "doc", "odt"], implementation: object
) -> ExtractionComponent:
    identity = type(implementation)
    qualified = f"{identity.__module__}.{identity.__qualname__}"
    try:
        code = component_code(inspect.getfile(identity))
    except TypeError:
        code = None
    return ExtractionComponent(
        role=role,
        qualified_name_sha256=hashlib.sha256(qualified.encode()).hexdigest(),
        source_file_sha256=code,
    )


@lru_cache(maxsize=1)
def extraction_dependencies() -> tuple[ExtractionDependency, ...]:
    result = [ExtractionDependency(name="python", version=sys.version.split()[0])]
    names: tuple[Literal["pypdf", "olefile", "inflate64"], ...] = ("pypdf", "olefile", "inflate64")
    for name in names:
        try:
            value = metadata.version(name)
        except metadata.PackageNotFoundError:
            value = None
        result.append(
            ExtractionDependency(name=name, version=value if value and len(value) <= 64 else None)
        )
    return tuple(result)


def summarize_extraction(
    physical: ExtractedParts,
    selected: ExtractedDocument,
    *,
    components: tuple[ExtractionComponent, ...],
    options: ExtractionOptions,
    word_views: tuple[WordPartViewStatus, ...] = (),
    odt_origins: tuple[OdtPartNoteOrigins, ...] = (),
) -> ExtractionResult:
    pdf = {part.part_index: part.extraction for part in physical.pdf_pages}
    word = {part.part_index: part.extraction for part in physical.word_blocks}
    odt = {part.part_index: part.extraction for part in physical.odt_blocks}
    unlocated = {part.part_index: part.extraction for part in physical.unlocated_text}
    notes = {part.part_index: part.extraction for part in physical.word_notes}
    views = {}
    word_manifests: dict[int, tuple[str, str | None]] = {}
    for offered in word_views:
        linked = WordPartViewStatus.model_validate(offered)
        index = linked.part_index
        if (
            index in views
            or index not in word
            or index not in notes
            or index >= len(physical.parts)
        ):
            raise ValueError("Word view status requires a unique actual physical legacy part")
        body, note = word[index], notes[index]
        body_manifest, notes_manifest = body.manifest_sha256, note.manifest_sha256
        word_manifests[index] = (body_manifest, notes_manifest)
        if (
            physical.parts[index].sha256,
            body.input_sha256,
            body_manifest,
            notes_manifest,
        ) != (
            linked.input_sha256,
            linked.input_sha256,
            linked.body_manifest_sha256,
            linked.notes_manifest_sha256,
        ):
            raise ValueError("Word view status differs from its actual input/body/note identities")
        WordViewExtraction(
            legacy=WordDetailedExtraction(body=body, notes=note), status=linked.status
        )
        views[index] = linked.status
    identities: list[str] = []
    selected_summaries: list[ExtractedPartSummary] = []
    selected_keys = Counter((part.sha256, part.members) for part in selected.selection.parts)
    for index, part in enumerate(physical.parts):
        fields: dict[str, object] = {
            "physical_index": index,
            "sha256": part.sha256,
            "member_path_sha256": extraction_metadata_sha256(part.members),
            "text_sha256": hashlib.sha256(part.text.encode()).hexdigest(),
            "text_chars": len(part.text),
            "format": "unknown",
            "coordinates": "unknown",
            "text_view": "unknown",
            "error_count": 0,
            "limitations": ("coordinates_unknown",),
            "loss_count": len(part.losses),
            "losses_sha256": extraction_metadata_sha256(part.losses),
        }
        if index in pdf:
            page = pdf[index]
            fields.update(
                format="pdf",
                coordinates=page.version,
                text_view=page.legacy_projection,
                manifest_sha256=page.manifest_sha256,
                block_count=len(page.pages),
                error_count=sum(page.error_type is not None for page in page.pages)
                + (page.error_type is not None),
                limitations=("pdf_text_layer_only",),
            )
        elif index in word:
            body = word[index]
            manifests = word_manifests.get(index)
            if manifests is None:
                manifests = (
                    body.manifest_sha256,
                    notes[index].manifest_sha256 if index in notes else None,
                )
            fields.update(
                format="word",
                coordinates=body.version,
                text_view=body.text_view,
                manifest_sha256=extraction_metadata_sha256(manifests),
                block_count=len(body.blocks),
                error_count=(body.body_state != "read")
                + (len(notes[index].losses) if index in notes else 0),
                limitations=(),
                word_view_status=views.get(index),
            )
        elif index in odt:
            open_document = odt[index]
            fields.update(
                format="odt",
                coordinates=open_document.version,
                text_view=open_document.text_view,
                manifest_sha256=open_document.manifest_sha256,
                block_count=len(open_document.blocks),
                error_count=int(open_document.body_state != "read"),
                limitations=(
                    "odt_notes_unmapped",
                    "tracked_changes_unresolved",
                    "table_repetitions_unexpanded",
                ),
            )
        elif index in unlocated:
            legacy = unlocated[index]
            fields.update(
                format=legacy.route,
                text_view="unlocated",
                manifest_sha256=legacy.manifest_sha256,
                limitations=(
                    "coordinates_unknown",
                    "notes_unlocated",
                    "tracked_changes_unknown",
                    "fields_unlocated",
                ),
            )
        summary = ExtractedPartSummary.model_validate(fields)
        identities.append(
            extraction_metadata_sha256(
                summary.model_dump(mode="json", exclude={"word_view_status", "odt_note_status"})
            )
        )
        key = (part.sha256, part.members)
        if selected_keys[key]:
            selected_summaries.append(summary)
            selected_keys[key] -= 1
    result = ExtractionResult.model_construct(
        artifact_sha256=physical.input_sha256,
        package_policy_source_sha256=component_code(inspect.getfile(select_document_parts)),
        components=components,
        dependencies=extraction_dependencies(),
        options=options,
        physical_manifest_sha256=extraction_metadata_sha256(identities),
        output_text_sha256=hashlib.sha256(selected.text.encode()).hexdigest(),
        output_chars=len(selected.text),
        physical_part_count=len(physical.parts),
        selected_part_count=len(selected_summaries),
        rejected_count=len(physical.rejected),
        rejected_sha256=extraction_metadata_sha256(physical.rejected),
        selected_parts=tuple(selected_summaries[:MAX_EXTRACTION_PART_SUMMARIES]),
        omitted_selected_parts=max(0, len(selected_summaries) - MAX_EXTRACTION_PART_SUMMARIES),
    )
    if views:
        for index in range(len(result.selected_parts) - 1, -1, -1):
            if len(result.model_dump_json().encode()) <= MAX_EXTRACTION_METADATA_BYTES:
                break
            summary = result.selected_parts[index]
            if summary.word_view_status is not None:
                retained = list(result.selected_parts)
                retained[index] = summary.model_copy(update={"word_view_status": None})
                result = result.model_copy(update={"selected_parts": tuple(retained)})
    while (
        result.selected_parts
        and len(result.model_dump_json().encode()) > MAX_EXTRACTION_METADATA_BYTES
    ):
        result = result.model_copy(
            update={
                "selected_parts": result.selected_parts[:-1],
                "omitted_selected_parts": result.omitted_selected_parts + 1,
            }
        )
    baseline = ExtractionResult.model_validate_json(result.model_dump_json())
    if not odt_origins:
        return baseline
    try:
        return _with_odt_note_status(baseline, physical, odt_origins)
    except Exception:
        return baseline


def _with_odt_note_status(
    baseline: ExtractionResult,
    physical: ExtractedParts,
    offered: tuple[OdtPartNoteOrigins, ...],
) -> ExtractionResult:
    linked = OdtNotedParts(legacy=physical, odt_notes=offered)
    origins = {part.part_index: part.extraction for part in linked.odt_notes}
    retained = list(baseline.selected_parts)
    for index, summary in enumerate(retained):
        value = origins.get(summary.physical_index)
        if value is None:
            continue
        status = OdtNoteStatus(
            known_walk=value.walk_state,
            input_sha256=value.input_sha256,
            body_manifest_sha256=value.body_manifest_sha256,
            retained_manifest_sha256=value.retained_manifest_sha256,
            observed_notes=value.note_count,
            observed_calls=value.call_count,
            retained_notes=len(value.entries),
            retained_calls=len(value.calls),
            coverage=value.coverage,
        )
        retained[index] = ExtractedPartSummary.model_validate(
            summary.model_copy(update={"odt_note_status": status})
        )
    candidate = baseline.model_copy(update={"selected_parts": tuple(retained)})
    for index in range(len(retained) - 1, -1, -1):
        if len(candidate.model_dump_json().encode()) <= MAX_EXTRACTION_METADATA_BYTES:
            break
        retained[index] = retained[index].model_copy(update={"odt_note_status": None})
        candidate = baseline.model_copy(update={"selected_parts": tuple(retained)})
    return ExtractionResult.model_validate(candidate)
