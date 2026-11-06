"""Summarize physical extraction without persisting block text or unbounded archive members."""

import hashlib
import inspect
import sys
from collections import Counter
from functools import lru_cache
from importlib import metadata
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from lexinform.document_candidates import select_document_parts
from lexinform.models.doc_notes import (
    MAX_DOC_NOTE_SUMMARY_BYTES,
    MAX_DOC_RETAINED_LINKS,
    DocNotedParts,
    DocNoteSummary,
)
from lexinform.models.documents import ExtractedDocument, ExtractedParts, WordDetailedExtraction
from lexinform.models.extractions import (
    MAX_EXTRACTION_METADATA_BYTES,
    MAX_EXTRACTION_PART_SUMMARIES,
    MAX_ODT_NOTE_PREFIX_BYTES,
    ExtractedPartSummary,
    ExtractionComponent,
    ExtractionDependency,
    ExtractionOptions,
    ExtractionResult,
    OdtNoteBlockBound,
    OdtNotePrefix,
    OdtNoteStatus,
    WordInlineMarkers,
    _odt_prefix_bytes,
    extraction_metadata_sha256,
)
from lexinform.models.odt_notes import (
    OdtNoteCall,
    OdtNotedParts,
    OdtNoteEntry,
    OdtNoteOrigins,
    OdtPartNoteOrigins,
    odt_note_association,
)
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
        views[index] = linked.status.model_copy(
            update={"observed_textbox_nodes": None, "observed_inline": None}
        )
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
                summary.model_dump(
                    mode="json",
                    exclude={"word_view_status", "odt_note_status", "odt_note_prefix", "doc_notes"},
                )
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
        captured_parts = OdtNotedParts(legacy=physical, odt_notes=odt_origins)
        status_result = _with_odt_note_status(baseline, captured_parts)
    except Exception:
        return baseline
    try:
        return _with_odt_note_prefix(status_result, captured_parts)
    except Exception:
        return status_result


def summarize_word_observations(
    baseline: ExtractionResult, linked: tuple[WordPartViewStatus, ...]
) -> ExtractionResult:
    with_textboxes = _with_word_view_observations(
        baseline,
        "observed_textbox_nodes",
        {
            item.part_index: item.status.observed_textbox_nodes
            for item in linked
            if item.status.observed_textbox_nodes is not None
        },
    )
    inline = {
        item.part_index: item.status.observed_inline
        for item in linked
        if item.status.observed_inline is not None
    }
    removal = {index: markers for index, markers in inline.items() if markers.removal_text_nodes}
    with_inline = _with_word_view_observations(
        with_textboxes,
        "observed_inline",
        {
            index: markers.model_copy(update={"removal_text_nodes": None})
            if index in removal
            else markers
            for index, markers in inline.items()
        },
    )
    return _with_word_view_observations(
        with_inline,
        "observed_inline",
        {
            part.physical_index: removal[part.physical_index]
            for part in with_inline.selected_parts
            if part.physical_index in removal
            and part.word_view_status is not None
            and part.word_view_status.observed_inline is not None
        },
    )


def _with_word_view_observations(
    baseline: ExtractionResult,
    field: Literal["observed_textbox_nodes", "observed_inline"],
    observations: dict[int, int | WordInlineMarkers],
) -> ExtractionResult:
    if not observations:
        return baseline
    parts = tuple(
        part.model_copy(
            update={
                "word_view_status": part.word_view_status.model_copy(
                    update={field: observations[part.physical_index]}
                )
            }
        )
        if part.word_view_status is not None and part.physical_index in observations
        else part
        for part in baseline.selected_parts
    )
    candidate = baseline.model_copy(update={"selected_parts": parts}).model_dump_json()
    if len(candidate.encode()) > MAX_EXTRACTION_METADATA_BYTES:
        return baseline
    return ExtractionResult.model_validate_json(candidate)


def _with_odt_note_status(
    baseline: ExtractionResult,
    linked: OdtNotedParts,
) -> ExtractionResult:
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


def _with_odt_note_prefix(baseline: ExtractionResult, linked: OdtNotedParts) -> ExtractionResult:
    sources = {item.part_index: item.extraction for item in linked.odt_notes}
    bodies = {item.part_index: item.extraction for item in linked.legacy.odt_blocks}
    retained = list(baseline.selected_parts)
    result = baseline
    used = 0
    for slot, summary in sorted(enumerate(retained), key=lambda item: item[1].physical_index):
        status = summary.odt_note_status
        source = sources.get(summary.physical_index)
        if status is None or source is None or not (source.entries or source.calls):
            continue
        needed = {record.block_path for record in (*source.entries, *source.calls)}
        bounds = {
            block.path: OdtNoteBlockBound(path=block.path, kind=block.kind, chars=len(block.text))
            for block in bodies[summary.physical_index].blocks
            if block.path in needed and block.kind in ("paragraph", "heading")
        }
        events = sorted((*source.entries, *source.calls), key=lambda record: record.order)
        for size in range(len(events), 0, -1):
            selected = events[:size]
            entries = tuple(record for record in selected if isinstance(record, OdtNoteEntry))
            calls = []
            for record in selected:
                if isinstance(record, OdtNoteCall):
                    association, target = odt_note_association(
                        record.kind,
                        record.path,
                        record.identity,
                        entries,
                        inventory_complete=source.walk_state == "finished"
                        and source.note_count == len(entries),
                    )
                    calls.append(
                        record.model_copy(
                            update={"association": association, "target_path": target}
                        )
                    )
            coverage = (
                "unknown"
                if source.walk_state == "unavailable"
                else "complete"
                if source.walk_state == "finished"
                and (len(entries), len(calls)) == (source.note_count, source.call_count)
                else "partial"
            )
            stored = OdtNoteOrigins.model_validate(
                source.model_copy(
                    update={"entries": entries, "calls": tuple(calls), "coverage": coverage}
                )
            )
            paths = {record.block_path for record in selected}
            witnesses = tuple(bounds[path] for path in sorted(paths))
            offered = {
                "physical_index": summary.physical_index,
                "origins": stored,
                "blocks": witnesses,
            }
            try:
                width = _odt_prefix_bytes(offered)
            except ValueError:
                continue
            if used + width > MAX_ODT_NOTE_PREFIX_BYTES:
                continue
            prefix = OdtNotePrefix(
                physical_index=summary.physical_index, origins=stored, blocks=witnesses
            )
            enriched = summary.model_copy(
                update={
                    "odt_note_status": status.model_copy(update={"contents": "prefix_retained"}),
                    "odt_note_prefix": prefix,
                }
            )
            candidate_parts = retained.copy()
            candidate_parts[slot] = enriched
            candidate = baseline.model_copy(update={"selected_parts": tuple(candidate_parts)})
            if len(candidate.model_dump_json().encode()) > MAX_EXTRACTION_METADATA_BYTES:
                continue
            result = ExtractionResult.model_validate(candidate)
            retained = candidate_parts
            used += width
            break
    return result


def summarize_doc_notes(baseline: ExtractionResult, linked: DocNotedParts) -> ExtractionResult:
    """Retain a bounded source-CP prefix without changing existing selected summaries."""
    linked = DocNotedParts.model_validate(linked)
    if (baseline.artifact_sha256, baseline.physical_part_count) != (
        linked.legacy.input_sha256,
        len(linked.legacy.parts),
    ):
        raise ValueError("DOC note summaries require their actual physical extraction")
    captures = {item.part_index: item.extraction for item in linked.doc_notes}
    retained = list(baseline.selected_parts)
    current = baseline
    for slot, summary in sorted(enumerate(retained), key=lambda item: item[1].physical_index):
        source = captures.get(summary.physical_index)
        if source is None:
            continue
        part = linked.legacy.parts[summary.physical_index]
        if (summary.sha256, summary.text_sha256, summary.member_path_sha256) != (
            part.sha256,
            part.text_sha256,
            extraction_metadata_sha256(part.members),
        ):
            raise ValueError("DOC note summary differs from its selected physical leaf")
        if (summary.format, summary.coordinates, summary.text_view) != (
            "doc",
            "unknown",
            "unlocated",
        ):
            raise ValueError("DOC note summaries require their actual unlocated DOC projection")
        for count in range(min(len(source.links), MAX_DOC_RETAINED_LINKS), -1, -1):
            try:
                notes = DocNoteSummary.from_capture(source, retained_links=count)
                enriched = ExtractedPartSummary.model_validate(
                    summary.model_copy(update={"doc_notes": notes})
                )
                offered = list(retained)
                offered[slot] = enriched
                size = sum(
                    len(item.doc_notes.model_dump_json().encode())
                    for item in offered
                    if item.doc_notes is not None
                )
                candidate = current.model_copy(update={"selected_parts": tuple(offered)})
                if (
                    size > MAX_DOC_NOTE_SUMMARY_BYTES
                    or len(candidate.model_dump_json().encode()) > MAX_EXTRACTION_METADATA_BYTES
                ):
                    continue
                current = ExtractionResult.model_validate(candidate)
            except ValidationError:
                continue
            retained = offered
            break
        else:
            break
    return _with_doc_note_projections(current, linked)


def _with_doc_note_projections(
    baseline: ExtractionResult, linked: DocNotedParts
) -> ExtractionResult:
    captures = {item.part_index: item.projections for item in linked.doc_notes if item.projections}
    if not captures:
        return baseline
    parts = list(baseline.selected_parts)
    current = baseline
    for slot, summary in sorted(enumerate(parts), key=lambda item: item[1].physical_index):
        source = captures.get(summary.physical_index)
        notes = summary.doc_notes
        if source is None or notes is None:
            continue
        retained_refs = {link.reference_cp for link in notes.links}
        projections = tuple(item for item in source if item.reference_cp in retained_refs)
        for count in range(len(projections), 0, -1):
            enriched = notes.model_copy(update={"projections": projections[:count]})
            if len(enriched.model_dump_json().encode()) > MAX_DOC_NOTE_SUMMARY_BYTES:
                continue
            offered = parts.copy()
            offered[slot] = summary.model_copy(update={"doc_notes": enriched})
            candidate = current.model_copy(update={"selected_parts": tuple(offered)})
            if (
                len(candidate.model_dump_json().encode()) > MAX_EXTRACTION_METADATA_BYTES
                or sum(
                    len(item.doc_notes.model_dump_json().encode())
                    for item in offered
                    if item.doc_notes is not None
                )
                > MAX_DOC_NOTE_SUMMARY_BYTES
            ):
                continue
            current = ExtractionResult.model_validate(candidate)
            parts = offered
            break
    return current
