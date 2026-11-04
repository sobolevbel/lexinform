import gc
import hashlib
import io
import weakref
import zipfile
from dataclasses import replace
from typing import Never

import pytest
from tools.replay_archive_dispositions import physical, synthetic_inputs, verify_members, zipped

from lexinform.adapters.document_text import DocumentTextExtractor
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.archive_disposition_observations import (
    ArchiveDispositionAccumulator,
    ArchiveRootObservation,
)
from lexinform.archive_policy_capture import ArchivePolicyCapture, ArchivePolicyDecision
from lexinform.models import (
    ArchiveMemberDisposition,
    ArtifactIdentity,
    CandidateDisposition,
    CandidateDispositionTrace,
    TextDocument,
)
from lexinform.models.candidate_trace import archive_member_rows_sha256
from lexinform.models.physical_inventory import PhysicalMetadata
from lexinform.physical_observations import PhysicalObservationCollector
from lexinform.physical_walk_capture import PhysicalWalkCapture
from lexinform.services.document_history import DocumentSourceRead, record_source_read
from lexinform.services.documents import TextLoader
from tests.fakes import FakeSejmGateway
from tests.unit.test_archive_member_dispositions import legacy
from tests.unit.test_candidate_disposition_trace import NOW
from tests.unit.test_physical_callers import URL, loader_for
from tests.unit.test_physical_walk_capture import extractor


def test_native_duplicate_members_retain_distinct_original_occurrences_and_cached_root() -> None:
    inputs = synthetic_inputs()
    url = "https://source.test/first"
    other = "https://source.test/other"
    gateway = FakeSejmGateway(
        files={url: inputs["synthetic_duplicate_names"], other: inputs["synthetic_nested_unknown"]}
    )
    loader = TextLoader(
        {"source.test": gateway.download},
        extractor(),
        max_bytes=100_000,
        physical_factory=PhysicalObservationCollector,
    )
    document = TextDocument(url=url, kind="rcl")
    _, first = loader.select_document(document)
    loader.select_document(TextDocument(url=other, kind="rcl"))
    _, cached = loader.select_document(document)
    trace = first.candidate_trace
    assert trace is not None, "native archive singleton keeps prospective member metadata"
    members = trace.archive_members
    assert members is not None and members.retention != "unknown", (
        "the bounded archive helper guarantees this section for the controlled fixture"
    )
    assert members.observed_rows == 4
    assert members.rows[0].ordinals == (0,)
    assert members.rows[1].ordinals == (1,)
    assert members.rows[0].leaf_sha256 != members.rows[1].leaf_sha256
    assert cached.candidate_trace == trace
    assert gateway.calls == ["download:" + url, "download:" + other]


def test_duplicate_payload_bytes_still_have_distinct_ordinal_occurrences() -> None:
    duplicate = synthetic_inputs()["synthetic_duplicate_names"]
    with zipfile.ZipFile(io.BytesIO(duplicate)) as archive:
        body = archive.read(archive.infolist()[0])
    data = zipped((("same.docx", body), ("same.docx", body)))
    reader, gateway = loader_for(data)
    _, loaded = reader.select_document(TextDocument(url=URL, kind="rcl"))
    assert loaded.candidate_trace is not None, "the actual native archive is traced"
    members = loaded.candidate_trace.archive_members
    assert members is not None and members.observed_rows == 4, (
        "the bounded archive helper guarantees this section for the controlled fixture"
    )
    first, second = members.rows[:2]
    assert first.ordinals == (0,) and second.ordinals == (1,)
    assert first.physical_part_index != second.physical_part_index
    assert first.leaf_sha256 == second.leaf_sha256
    assert gateway.calls == ["download:" + URL]


def test_optional_policy_fault_preserves_selected_text_and_one_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = synthetic_inputs()["synthetic_duplicate_names"]
    ordinary = extractor().extract_document_with_provenance(data).document

    def fail(self: ArchivePolicyCapture, decision: ArchivePolicyDecision) -> None:
        raise RuntimeError("optional policy")

    monkeypatch.setattr(ArchivePolicyCapture, "record", fail)
    reader, gateway = loader_for(data)
    _, loaded = reader.select_document(TextDocument(url=URL, kind="rcl"))
    assert loaded.text == ordinary.text
    assert loaded.selection.parts == tuple(
        part.model_copy(update={"url": URL}) for part in ordinary.selection.parts
    )
    trace = loaded.candidate_trace
    assert trace is not None and trace.archive_members is not None, (
        "the bounded archive helper guarantees this section for the controlled fixture"
    )
    assert trace.archive_members.retention == "unknown"
    assert trace.archive_members.rows_sha256 is None, (
        "the bounded archive helper guarantees this section for the controlled fixture"
    )
    assert gateway.calls == ["download:" + URL]


def test_failed_outer_read_retains_only_actual_walk_and_cannot_export_policy_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = synthetic_inputs()
    failed = inputs["synthetic_duplicate_names"]
    other = "https://source.test/chosen"
    gateway = FakeSejmGateway(files={URL: failed, other: inputs["synthetic_nested_unknown"]})
    original_pages = DocumentTextExtractor.pages
    error = ValueError("existing pages failure after policy")

    def pages(self: DocumentTextExtractor, data: bytes) -> int:
        if data == failed:
            raise error
        return original_pages(self, data)

    monkeypatch.setattr(DocumentTextExtractor, "pages", pages)
    reader = TextLoader(
        {"source.test": gateway.download},
        extractor(),
        max_bytes=100_000,
        physical_factory=PhysicalObservationCollector,
    )
    document = TextDocument(
        url=URL, kind="rcl", alternatives=(TextDocument(url=other, kind="rcl"),)
    )
    selected, loaded = reader.select_document(document)
    assert selected.url == other
    trace = loaded.candidate_trace
    assert trace is not None and trace.dispositions[0].outcome == "read_failure", (
        "the public native loader retains this fixture's prospective archive trace"
    )
    assert trace.dispositions[0].observed_sha256 is None, (
        "the public native loader retains this fixture's prospective archive trace"
    )
    members = trace.archive_members
    assert members is not None and members.walk == "interrupted", (
        "the bounded archive helper guarantees this section for the controlled fixture"
    )
    assert members.observed_rows == 8
    assert all(item.stage == "walk" for item in members.rows if item.index == 0)
    assert gateway.calls == ["download:" + URL, "download:" + other]
    singleton, single_gateway = loader_for(failed)
    with pytest.raises(ValueError) as caught:
        singleton.select_document(TextDocument(url=URL, kind="rcl"))
    assert caught.value is error and single_gateway.calls == ["download:" + URL], (
        "the checked public fixture path guarantees this typed result"
    )


def test_losing_roots_and_both_phases_are_counted_without_reusing_an_old_phase_hash() -> None:
    inputs = synthetic_inputs()
    other = "https://source.test/other"
    gateway = FakeSejmGateway(
        files={URL: inputs["synthetic_crc"], other: inputs["synthetic_duplicate_names"]}
    )
    reader = TextLoader(
        {"source.test": gateway.download},
        extractor(),
        max_bytes=100_000,
        physical_factory=PhysicalObservationCollector,
    )
    document = TextDocument(
        url=URL,
        kind="rcl",
        compare_versions=True,
        alternatives=(TextDocument(url=other, kind="rcl"),),
    )
    _, loaded = reader.select_document(document)
    trace = loaded.candidate_trace
    assert trace is not None and trace.archive_members is not None, (
        "the bounded archive helper guarantees this section for the controlled fixture"
    )
    assert trace.archive_members.observed_rows == 10
    assert {item.phase for item in trace.dispositions} == {"version_comparison", "fallback"}
    assert any(
        item.phase == "version_comparison" and item.index == 0
        for item in trace.archive_members.rows
    )
    _, fallback = reader.select_document(document.model_copy(update={"compare_versions": False}))
    assert (
        fallback.candidate_trace is not None
        and fallback.candidate_trace.archive_members is not None
    ), "the bounded archive helper guarantees this section for the controlled fixture"
    assert fallback.candidate_trace.archive_members.rows_sha256 != trace.archive_members.rows_sha256
    assert gateway.calls == ["download:" + URL, "download:" + other]


def test_deep_associations_are_unknown_instead_of_truncated_to_four_ordinals() -> None:
    reader, _ = loader_for(synthetic_inputs()["synthetic_depth"])
    _, loaded = reader.select_document(TextDocument(url=URL, kind="rcl"))
    trace = loaded.candidate_trace
    assert trace is not None and trace.archive_members is not None, (
        "the bounded archive helper guarantees this section for the controlled fixture"
    )
    assert trace.archive_members.retention == "unknown"
    assert trace.archive_members.rows_sha256 is None and not trace.archive_members.rows, (
        "the bounded archive helper guarantees this section for the controlled fixture"
    )


def test_native_prospective_trace_persists_once_and_roundtrips_through_existing_public_repository(
    repo: SqliteBillRepository,
) -> None:
    reader, _ = loader_for(synthetic_inputs()["synthetic_duplicate_names"])
    document = TextDocument(url=URL, kind="rcl")
    selected, loaded = reader.select_document(document)
    capture = reader.capture_source_read(document, selected, loaded)
    assert isinstance(capture, DocumentSourceRead), (
        "the existing source admission accepts this archive"
    )
    trace = capture.candidate_trace
    assert trace is not None and trace.archive_members is not None, (
        "the bounded archive helper guarantees this section for the controlled fixture"
    )
    record_source_read(
        repo, capture, term=10, number="archive", purpose="analysis", recorded_at=NOW
    )
    first = repo.dump()
    record_source_read(
        repo, capture, term=10, number="archive", purpose="analysis", recorded_at=NOW
    )
    assert repo.dump() == first
    assert repo.get_candidate_disposition_trace(trace.trace_id) == trace
    repo.restore(first)
    assert repo.dump() == first and repo.get_candidate_disposition_trace(trace.trace_id) == trace


def test_member_byte_limit_reports_actual_walk_cap_without_invented_policy_rows() -> None:
    data = synthetic_inputs()["synthetic_duplicate_names"]
    ordinary = extractor(limit=1).extract_document_with_provenance(data).document
    gateway = FakeSejmGateway(files={URL: data})
    reader = TextLoader(
        {"source.test": gateway.download},
        extractor(limit=1),
        max_bytes=100_000,
        physical_factory=PhysicalObservationCollector,
    )
    _, loaded = reader.select_document(TextDocument(url=URL, kind="rcl"))
    assert loaded.text is None and not ordinary.text, (
        "the checked public fixture path guarantees this typed result"
    )
    assert loaded.selection.state == ordinary.selection.state
    assert loaded.selection.rejected == ordinary.selection.rejected
    trace = loaded.candidate_trace
    assert trace is not None and trace.archive_members is not None, (
        "the bounded archive helper guarantees this section for the controlled fixture"
    )
    assert trace.archive_members.observed_rows == 2
    assert all(item.stage == "walk" for item in trace.archive_members.rows)
    assert {item.reason for item in trace.archive_members.rows} == {
        "unpacking_limit_source_pinned_inference"
    }
    assert gateway.calls == ["download:" + URL]


@pytest.mark.parametrize("fault", ["slot", "leaf", "text", "missing", "duplicate"])
def test_actual_typed_slot_and_full_policy_universe_must_corroborate_every_decision(
    fault: str,
) -> None:
    data = synthetic_inputs()["synthetic_duplicate_names"]
    artifact = ArtifactIdentity(sha256=hashlib.sha256(data).hexdigest(), byte_size=len(data))
    physical = PhysicalWalkCapture(artifact)
    policy = ArchivePolicyCapture()
    with physical.scope(), policy.scope():
        extractor().extract_document_with_provenance(data)
    decisions = policy.decisions
    assert decisions is not None and physical.observation is not None, (
        "the native invocation seals both facts"
    )
    if fault == "slot":
        decisions = (replace(decisions[0], index=99), *decisions[1:])
    elif fault == "leaf":
        decisions = (replace(decisions[0], leaf_sha256="a" * 64), *decisions[1:])
    elif fault == "text":
        decisions = (replace(decisions[0], text_sha256="a" * 64), *decisions[1:])
    elif fault == "missing":
        decisions = decisions[:-1]
    else:
        decisions = (*decisions, decisions[0])
    original = legacy().model_copy(
        update={
            "dispositions": (
                CandidateDisposition(
                    phase="fallback",
                    index=0,
                    outcome="selected",
                    reason="first_acceptable_candidate",
                    observed_sha256=artifact.sha256,
                ),
            )
        }
    )
    root = ArchiveRootObservation(artifact, physical.observation, decisions, True)
    accumulator = ArchiveDispositionAccumulator()
    accumulator.observe("fallback", 0, root)
    enriched = accumulator.enrich(original)
    members = enriched.archive_members
    assert members is not None and members.retention == "unknown", (
        "the bounded archive helper guarantees this section for the controlled fixture"
    )
    assert members.observed_rows is None and members.rows_sha256 is None and not members.rows, (
        "the bounded archive helper guarantees this section for the controlled fixture"
    )


def native_root() -> ArchiveRootObservation:
    data = synthetic_inputs()["synthetic_duplicate_names"]
    artifact = ArtifactIdentity(sha256=hashlib.sha256(data).hexdigest(), byte_size=len(data))
    physical, policy = PhysicalWalkCapture(artifact), ArchivePolicyCapture()
    with physical.scope(), policy.scope():
        extractor().extract_document_with_provenance(data)
    return ArchiveRootObservation(artifact, physical.observation, policy.decisions, True)


def test_accumulator_releases_the_completed_packet_before_final_trace_assembly() -> None:
    root = native_root()
    assert root.physical is not None and root.physical.assembly is not None, (
        "native capture seals the actual packet"
    )
    packet = weakref.ref(root.physical.assembly)
    digest = root.artifact.sha256
    accumulator = ArchiveDispositionAccumulator()
    accumulator.observe("fallback", 0, root)
    del root
    gc.collect()
    assert packet() is None, (
        "the accumulator keeps only primitive rows after the invocation returns"
    )
    original = legacy().model_copy(
        update={
            "dispositions": (
                CandidateDisposition(
                    phase="fallback",
                    index=0,
                    outcome="selected",
                    reason="first_acceptable_candidate",
                    observed_sha256=digest,
                ),
            )
        }
    )
    result = accumulator.enrich(original)
    assert result.archive_members is not None and result.archive_members.observed_rows == 4, (
        "the full primitive row stream survives without its packet"
    )


def test_global_attempt_budget_discards_all_partial_facts_and_skips_later_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = native_root()
    accumulator = ArchiveDispositionAccumulator()
    for index in range(600):
        accumulator.observe("fallback", index, root)

    def cancel(self: PhysicalMetadata, **kwargs: object) -> str:
        raise KeyboardInterrupt("unknown coverage must not gather further metadata")

    monkeypatch.setattr(PhysicalMetadata, "model_dump_json", cancel)
    accumulator.observe("fallback", 601, root)
    result = accumulator.enrich(legacy())
    members = result.archive_members
    assert members is not None and members.retention == "unknown", (
        "the global metadata cap produces the explicit unknown envelope"
    )
    assert members.observed_rows is None and members.rows_sha256 is None and not members.rows


@pytest.mark.parametrize("error", [ValueError("metadata"), KeyboardInterrupt("metadata")])
def test_accumulator_failure_preserves_ordinary_trace_or_propagates_cancellation(
    error: BaseException, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = native_root()
    original = legacy()

    def fail(self: ArchiveMemberDisposition, **kwargs: object) -> str:
        raise error

    monkeypatch.setattr(ArchiveMemberDisposition, "model_dump_json", fail)
    accumulator = ArchiveDispositionAccumulator()
    if isinstance(error, Exception):
        accumulator.observe("fallback", 0, root)
        assert accumulator.enrich(original) is original
    else:
        with pytest.raises(type(error)) as caught:
            accumulator.observe("fallback", 0, root)
        assert caught.value is error


def test_capture_time_failure_marker_stays_walk_only_when_outer_parent_is_omitted() -> None:
    root = replace(native_root(), completed=False)
    accumulator = ArchiveDispositionAccumulator()
    accumulator.observe("fallback", 0, root)
    result = accumulator.enrich(legacy(url="https://api.test/" + "x" * 5000))
    members = result.archive_members
    assert members is not None and members.retention == "omitted", (
        "the existing outer factory omits its request before archive enrichment"
    )
    assert members.walk == "interrupted" and members.observed_rows == 2
    assert not members.rows and result.requested_document is None


def test_large_archive_prefix_retains_selected_main_and_real_exclusion_with_original_identity() -> (
    None
):
    main_text = (
        "USTAWA\nz dnia 1 stycznia 2026 r.\no cudzoziemcach\nArt. 1. Cudzoziemiec otrzymuje 200 zł.\n"
        + "Cudzoziemiec składa wniosek. " * 20
    )

    def document(text: str) -> bytes:
        xml = (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'
            + text
            + "</w:t></w:r></w:p></w:body></w:document>"
        )
        return zipped((("word/document.xml", xml.encode()),))

    data = zipped(
        (
            *(
                (f"excluded_{index}.docx", document("TABELA ZGODNOŚCI\n" + "Treść tabeli. " * 20))
                for index in range(8)
            ),
            ("zz_project.docx", document(main_text)),
        )
    )
    reader, gateway = loader_for(data)
    _, loaded = reader.select_document(TextDocument(url=URL, kind="rcl"))
    trace = loaded.candidate_trace
    assert trace is not None and trace.archive_members is not None, (
        "the public native loader records this archive's actual policy decisions"
    )
    members = trace.archive_members
    assert members.retention == "prefix" and members.observed_rows == 18
    assert [item.stage for item in members.rows] == ["policy", "policy"]
    assert members.rows[0].reason == "selected_main" and members.rows[0].ordinals == (8,)
    assert members.rows[1].reason == "non_bill_body"
    assert members.rows[1].physical_part_index is not None
    assert len(members.model_dump_json().encode()) <= 1024
    _, packets = physical(reader)
    verified = verify_members(trace, packets, {hashlib.sha256(data).hexdigest(): data})
    assert verified["checked"] == 2 and verified["unknown"] == 0
    assert gateway.calls == ["download:" + URL]


def test_full_canonical_priority_orders_selected_roles_and_stable_original_slots_before_walk() -> (
    None
):
    def document(text: str) -> bytes:
        xml = (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'
            + text
            + "</w:t></w:r></w:p></w:body></w:document>"
        )
        return zipped((("word/document.xml", xml.encode()),))

    bodies = (
        document("UZASADNIENIE\n" + "Treść uzasadnienia. " * 20),
        document(
            "USTAWA\nz dnia 1 stycznia 2026 r.\no cudzoziemcach\nArt. 1. Termin wynosi 30 dni.\n"
            + "Treść ustawy. " * 20
        ),
        document("Załącznik do ustawy\n" + "Pierwsza treść. " * 20),
        document("Załącznik do ustawy\n" + "Druga treść. " * 20),
    )
    data = zipped(
        tuple(
            zip(
                ("uzasadnienie.docx", "project.docx", "annex_b.docx", "annex_a.docx"),
                bodies,
                strict=True,
            )
        )
    )
    artifact = ArtifactIdentity(sha256=hashlib.sha256(data).hexdigest(), byte_size=len(data))
    capture, policy = PhysicalWalkCapture(artifact), ArchivePolicyCapture()
    with capture.scope(), policy.scope():
        extractor().extract_document_with_provenance(data)
    decisions = policy.decisions
    assert decisions is not None and len(decisions) == 4, (
        "the native fixture records every selected original slot"
    )
    raw_ordinals = {hashlib.sha256(body).hexdigest(): index for index, body in enumerate(bodies)}
    assert len(raw_ordinals) == 4, (
        "this independent raw reference uses four deliberately distinct payloads"
    )
    policy_rows = tuple(
        ArchiveMemberDisposition(
            stage="policy",
            phase="fallback",
            index=0,
            root_sha256=artifact.sha256,
            ordinals=(raw_ordinals[item.leaf_sha256 or ""],),
            physical_part_index=item.index,
            outcome=item.outcome,
            reason=item.reason,
            leaf_sha256=item.leaf_sha256,
            text_sha256=item.text_sha256,
        )
        for item in decisions
    )
    main = tuple(item for item in policy_rows if item.reason == "selected_main")
    annexes = tuple(
        sorted(
            (item for item in policy_rows if item.reason == "selected_annex"),
            key=lambda item: item.physical_part_index or 0,
        )
    )
    support = tuple(item for item in policy_rows if item.reason == "selected_support")
    assert len(main) == len(support) == 1 and len(annexes) == 2
    by_ordinal = {item.ordinals: item for item in policy_rows}
    walk = tuple(
        by_ordinal[(index,)].model_copy(
            update={"stage": "walk", "outcome": "observed", "reason": "member_complete"}
        )
        for index in range(4)
    )
    expected = (*main, *annexes, *support, *walk)
    accumulator = ArchiveDispositionAccumulator()
    accumulator.observe(
        "fallback", 0, ArchiveRootObservation(artifact, capture.observation, decisions[::-1], True)
    )
    original = legacy().model_copy(
        update={
            "dispositions": (
                CandidateDisposition(
                    phase="fallback",
                    index=0,
                    outcome="selected",
                    reason="first_acceptable_candidate",
                    observed_sha256=artifact.sha256,
                ),
            )
        }
    )
    result = accumulator.enrich(original)
    members = result.archive_members
    assert members is not None, "the bounded native helper retains this corroborated policy prefix"
    assert members.rows == expected[: len(members.rows)]
    assert members.observed_rows == 8 and members.rows_sha256 == archive_member_rows_sha256(
        expected
    )


@pytest.mark.parametrize("boundary", ["constructor", "enrich"])
def test_outer_metadata_fault_keeps_ordinary_selection_trace_and_downloads(
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    inputs = synthetic_inputs()
    other = "https://source.test/other"
    files = {URL: inputs["synthetic_duplicate_names"], other: inputs["synthetic_nested_unknown"]}
    document = TextDocument(
        url=URL,
        kind="rcl",
        compare_versions=True,
        alternatives=(TextDocument(url=other, kind="rcl"),),
    )
    baseline_gateway = FakeSejmGateway(files=files)
    baseline_reader = TextLoader(
        {"source.test": baseline_gateway.download},
        extractor(),
        max_bytes=100_000,
        physical_factory=PhysicalObservationCollector,
    )
    expected_document, expected = baseline_reader.select_document(document)
    original = expected.candidate_trace
    assert original is not None, "the explicit comparison request retains its outer decisions"
    ordinary = CandidateDispositionTrace.model_validate(
        {**original.model_dump(), "archive_members": None}
    )
    assert not ordinary.is_routine_singleton, "the two-candidate fixture keeps its ordinary trace"

    def fail_constructor() -> Never:
        raise RuntimeError("optional accumulator construction")

    def fail_enrich(
        self: ArchiveDispositionAccumulator,
        trace: CandidateDispositionTrace,
    ) -> Never:
        raise RuntimeError("optional accumulator enrichment")

    if boundary == "constructor":
        monkeypatch.setattr(
            "lexinform.services.documents.ArchiveDispositionAccumulator", fail_constructor
        )
    else:
        monkeypatch.setattr(ArchiveDispositionAccumulator, "enrich", fail_enrich)
    gateway = FakeSejmGateway(files=files)
    reader = TextLoader(
        {"source.test": gateway.download},
        extractor(),
        max_bytes=100_000,
        physical_factory=PhysicalObservationCollector,
    )
    selected, actual = reader.select_document(document)
    assert selected == expected_document
    assert actual == replace(expected, candidate_trace=ordinary)
    assert gateway.calls == baseline_gateway.calls
