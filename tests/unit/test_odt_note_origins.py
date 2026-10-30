import json
import tracemalloc
from typing import Literal

import pytest
from pydantic import ValidationError

from lexinform.models import (
    DocumentPart,
    ExtractedParts,
    OdtEmittedSpan,
    OdtNoteCall,
    OdtNotedParts,
    OdtNoteEntry,
    OdtNoteOrigins,
    OdtPartExtraction,
    OdtPartNoteOrigins,
    OdtTextBlock,
    OdtTextExtraction,
)
from lexinform.models.odt_notes import odt_note_association

SHA = "a" * 64
XML_SHA = "b" * 64


def entry(order: int = 0, index: int = 0, identity: str | None = "n1") -> OdtNoteEntry:
    return OdtNoteEntry(
        order=order,
        path=(0, index),
        paragraph_path=(0,),
        block_path=(0,),
        emitted=None,
        identity=identity,
        note_class="footnote",
        citation_path=(0, index, 0),
        body_path=(0, index, 1),
        parent_note_path=None,
    )


def call(
    order: int,
    index: int,
    notes: tuple[OdtNoteEntry, ...],
    *,
    identity: str | None = "n1",
    kind: Literal["embedded", "reference"] = "reference",
    complete: bool = True,
) -> OdtNoteCall:
    state, target = odt_note_association(
        kind, (0, index), identity, notes, inventory_complete=complete
    )
    return OdtNoteCall(
        order=order,
        path=(0, index),
        paragraph_path=(0,),
        block_path=(0,),
        emitted=None,
        identity=identity,
        kind=kind,
        association=state,
        target_path=target,
    )


def body(text: str = "Before1noteAfter") -> OdtTextExtraction:
    return OdtTextExtraction(
        input_sha256=SHA,
        xml_sha256=XML_SHA,
        python_version="3.14.7",
        body_state="read",
        blocks=(OdtTextBlock(kind="paragraph", path=(0,), text=text),),
        legacy_blocks=("odt-xml:content.xml:body:0",),
    )


def origins(
    notes: tuple[OdtNoteEntry, ...] = (), calls: tuple[OdtNoteCall, ...] = ()
) -> OdtNoteOrigins:
    return OdtNoteOrigins(
        input_sha256=SHA,
        xml_sha256=XML_SHA,
        body_manifest_sha256=body().manifest_sha256,
        walk_state="finished",
        note_count=len(notes),
        call_count=len(calls),
        entries=notes,
        calls=calls,
        coverage="complete",
    )


def physical(extraction: OdtTextExtraction | None = None) -> ExtractedParts:
    source = extraction or body()
    return ExtractedParts(
        input_sha256="c" * 64,
        parts=(DocumentPart(text=source.legacy_text, sha256=SHA),),
        odt_blocks=(OdtPartExtraction(part_index=0, extraction=source),),
    )


def bind(value: OdtNoteOrigins, source: ExtractedParts | None = None) -> OdtNotedParts:
    return OdtNotedParts(
        legacy=source or physical(), odt_notes=(OdtPartNoteOrigins(part_index=0, extraction=value),)
    )


def test_embedded_and_separate_reference_use_paths_and_exact_unique_ids() -> None:
    notes = (entry(),)
    value = origins(notes, (call(1, 0, notes, kind="embedded"), call(2, 1, notes)))
    linked = bind(value)
    assert linked.odt_notes[0].extraction.calls[1].target_path == (0, 0)
    assert OdtNotedParts.model_validate_json(linked.model_dump_json()) == linked
    assert value.full_manifest_sha256 == value.retained_manifest_sha256
    assert len(value.model_dump_json().encode()) <= 32768


def test_duplicate_missing_and_empty_ids_do_not_guess_from_display_text() -> None:
    notes = (entry(0, 0), entry(1, 1))
    calls = (
        call(2, 2, notes),
        call(3, 3, notes, identity="different"),
        call(4, 4, notes, identity=""),
    )
    value = origins(notes, calls)
    assert [item.association for item in value.calls] == ["ambiguous", "missing", "unverified"]
    forged = value.calls[0].model_copy(
        update={"association": "resolved", "target_path": notes[0].path}
    )
    with pytest.raises(ValidationError):
        value.model_copy(update={"calls": (forged, *calls[1:])}).model_validate(
            value.model_copy(update={"calls": (forged, *calls[1:])})
        )


def test_nested_entries_bind_nearest_physical_note_without_id_uniqueness_assumption() -> None:
    outer = entry()
    inner = OdtNoteEntry(
        order=2,
        path=(0, 0, 1, 0, 0),
        paragraph_path=(0, 0, 1, 0),
        block_path=(0,),
        emitted=None,
        identity="n1",
        note_class="endnote",
        citation_path=None,
        body_path=None,
        parent_note_path=outer.path,
    )
    first = call(1, 0, (outer,), kind="embedded")
    second = OdtNoteCall(
        order=3,
        path=inner.path,
        paragraph_path=inner.paragraph_path,
        block_path=(0,),
        emitted=None,
        identity="n1",
        kind="embedded",
        association="embedded",
        target_path=inner.path,
    )
    value = origins((outer, inner), (first, second))
    assert bind(value).odt_notes[0].extraction.entries[1].parent_note_path == outer.path
    with pytest.raises(ValidationError):
        OdtNoteOrigins.model_validate(
            value.model_copy(
                update={"entries": (outer, inner.model_copy(update={"parent_note_path": None}))}
            )
        )


def test_incomplete_inventory_cannot_prove_unique_or_missing_references() -> None:
    notes = (entry(),)
    references = (
        call(1, 1, notes, complete=False),
        call(2, 2, notes, identity="absent", complete=False),
    )
    value = origins().model_copy(
        update={
            "entries": notes,
            "calls": references,
            "note_count": 100,
            "call_count": 2,
            "coverage": "partial",
        }
    )
    parsed = OdtNoteOrigins.model_validate(value)
    assert parsed.full_manifest_sha256 is None
    assert [item.association for item in parsed.calls] == ["unverified", "unverified"]
    with pytest.raises(ValidationError):
        OdtNoteOrigins.model_validate(
            parsed.model_copy(
                update={
                    "calls": (
                        references[0].model_copy(
                            update={"association": "resolved", "target_path": notes[0].path}
                        ),
                        references[1],
                    )
                }
            )
        )


def test_interrupted_empty_and_unavailable_are_explicit_without_fake_completeness() -> None:
    interrupted = OdtNoteOrigins.model_validate(
        origins().model_copy(update={"walk_state": "interrupted", "coverage": "partial"})
    )
    assert interrupted.full_manifest_sha256 is None
    unavailable = OdtNoteOrigins.model_validate(
        origins().model_copy(update={"walk_state": "unavailable", "coverage": "unknown"})
    )
    assert unavailable.full_manifest_sha256 is None
    with pytest.raises(ValidationError):
        OdtNoteOrigins.model_validate(interrupted.model_copy(update={"coverage": "complete"}))


@pytest.mark.parametrize("field", ["input_sha256", "xml_sha256", "body_manifest_sha256"])
def test_carrier_rejects_foreign_input_xml_or_body_manifest(field: str) -> None:
    value = origins().model_copy(update={field: "e" * 64})
    with pytest.raises(ValidationError):
        bind(value)


def test_observed_ranges_require_explicit_evidence_and_actual_block_bounds() -> None:
    note = entry().model_copy(
        update={"emitted": OdtEmittedSpan(start=6, end=11, evidence="observed-legacy-appends")}
    )
    assert bind(origins((note,))).odt_notes[0].extraction.entries[0].emitted == note.emitted
    bad = note.model_copy(
        update={"emitted": OdtEmittedSpan(start=0, end=999, evidence="observed-legacy-appends")}
    )
    with pytest.raises(ValidationError):
        bind(origins((bad,)))
    with pytest.raises(ValidationError):
        OdtEmittedSpan.model_validate({"start": 0, "end": 1})


@pytest.mark.parametrize("change", ["id", "escaped_id", "path", "index", "events", "typed"])
def test_metadata_limits_reject_before_unbounded_materialization(change: str) -> None:
    raw = origins((entry(),)).model_dump()
    if change == "id":
        raw["entries"][0]["identity"] = "x" * 1_000_000
    elif change == "escaped_id":
        raw["entries"][0]["identity"] = "😀" * 30
    elif change == "path":
        raw["entries"][0]["path"] = [0] * 100_000
    elif change == "index":
        raw["entries"][0]["path"] = (0, 1 << 100_000)
    elif change == "events":
        raw["entries"] = [raw["entries"][0]] * 100_000
    else:
        raw["entries"] = (entry().model_copy(update={"order": -1}),)
    tracemalloc.start()
    try:
        with pytest.raises(ValidationError):
            OdtNoteOrigins.model_validate(raw)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 300_000


def test_aggregate_byte_limit_and_original_event_prefix_are_enforced() -> None:
    notes = tuple(
        entry(i, i, "x" * 250).model_copy(update={"note_class": "y" * 250}) for i in range(64)
    )
    oversized = origins().model_dump()
    oversized.update(entries=notes, note_count=64)
    with pytest.raises(ValidationError):
        OdtNoteOrigins.model_validate(oversized)
    with pytest.raises(ValidationError):
        origins((entry(2),))
    partial = OdtNoteOrigins.model_validate(
        origins().model_copy(
            update={"note_count": 2**63 - 1, "call_count": 2**63 - 1, "coverage": "partial"}
        )
    )
    assert not partial.entries and not partial.calls and partial.full_manifest_sha256 is None


def test_inert_companion_preserves_literal_old_models_and_custom_unknown() -> None:
    original = physical()
    before = original.model_dump_json()
    digest = original.odt_blocks[0].extraction.manifest_sha256
    bind(origins(), original)
    assert original.model_dump_json() == before
    assert original.odt_blocks[0].extraction.manifest_sha256 == digest
    assert (
        json.loads(before)["odt_blocks"][0]["extraction"]["inline_notes"]
        == "legacy-inline-unmapped"
    )
    custom = ExtractedParts(input_sha256=SHA, parts=(DocumentPart(text="custom", sha256=SHA),))
    assert OdtNotedParts(legacy=custom, odt_notes=()).legacy == custom
    with pytest.raises(ValidationError):
        bind(origins(), custom)


def test_reference_node_cannot_impersonate_embedded_definition() -> None:
    notes = (entry(),)
    with pytest.raises(ValidationError):
        origins(notes, (call(1, 0, notes),))


def test_typed_nested_span_and_physical_index_are_revalidated() -> None:
    invalid_span = OdtEmittedSpan(start=0, end=1, evidence="observed-legacy-appends").model_copy(
        update={"end": -1}
    )
    with pytest.raises(ValidationError):
        origins((entry().model_copy(update={"emitted": invalid_span}),))
    valid = OdtPartNoteOrigins(part_index=0, extraction=origins())
    with pytest.raises(ValidationError):
        OdtNotedParts(legacy=physical(), odt_notes=(valid.model_copy(update={"part_index": -1}),))


def test_identityless_embedded_note_uses_only_its_actual_xml_node() -> None:
    notes = (entry(identity=None),)
    value = origins(
        notes, (call(1, 0, notes, identity=None, kind="embedded"), call(2, 1, notes, identity=None))
    )
    assert [record.association for record in value.calls] == ["embedded", "unverified"]
    assert value.calls[0].target_path == notes[0].path and value.calls[1].target_path is None


def test_typed_carrier_cannot_leak_an_untyped_legacy_body_slot() -> None:
    legacy = physical()
    broken = legacy.model_copy(update={"odt_blocks": (legacy.odt_blocks[0].model_dump(),)})
    with pytest.raises(ValidationError):
        OdtNotedParts(legacy=broken, odt_notes=())


def test_cross_lane_events_follow_actual_xml_walk_order() -> None:
    notes = (entry(0, 5),)
    with pytest.raises(ValidationError):
        origins(notes, (call(1, 1, notes),))
    with pytest.raises(ValidationError):
        origins(notes, (call(1, 5, notes),))
    reversed_pair = (entry(1, 5),)
    with pytest.raises(ValidationError):
        origins(reversed_pair, (call(0, 5, reversed_pair, kind="embedded"),))
    forward = (entry(1, 2),)
    valid = origins(forward, (call(0, 1, forward), call(2, 2, forward, kind="embedded")))
    assert valid.coverage == "complete"
