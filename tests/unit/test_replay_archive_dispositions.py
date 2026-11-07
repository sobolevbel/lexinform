import io
import json
import zipfile

import pytest
from tools.corpus_artifacts import digest
from tools.replay_archive_dispositions import (
    archive_leaf,
    observe,
    physical,
    synthetic_inputs,
    verify_members,
)

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.models import CandidateDisposition, CandidateDispositionTrace, TextDocument
from lexinform.models.candidate_trace import (
    ArchiveMemberDisposition,
    ArchiveMemberDispositions,
    PackedArchiveMemberDispositions,
    archive_member_rows_sha256,
)
from lexinform.models.physical_inventory import PhysicalContainerExecution, PhysicalRootOrder
from lexinform.physical_observations import PhysicalObservationCollector
from lexinform.services.documents import TextLoader


def test_raw_archive_address_uses_occurrence_ordinals_for_duplicate_names() -> None:
    data = synthetic_inputs()["synthetic_duplicate_names"]
    assert archive_leaf(data, (0,)) != archive_leaf(data, (1,))
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.infolist()[0].filename == archive.infolist()[1].filename


def test_independent_archive_address_rejects_an_unobserved_ordinal() -> None:
    with pytest.raises(ValueError, match="ordinal"):
        archive_leaf(synthetic_inputs()["synthetic_duplicate_names"], (99,))


def test_public_native_path_records_source_capture_and_ordered_cache_reuse_without_bodies() -> None:
    url = "https://replay.test/controlled"
    report = observe(
        {url: synthetic_inputs()["synthetic_duplicate_names"]},
        [("controlled", TextDocument(url=url, kind="rcl"))],
    )
    core = report["core"]
    assert core["metrics"]["hosts"]["replay.test"]["completed"] == 1
    assert core["metrics"]["cache_hits"] > 0
    assert core["io_events"][0]["event"] == "download"
    assert any(row["event"] == "cache" for row in core["io_events"])
    assert core["outcomes"]["controlled/0"]["source_capture"]["kind"] == "DocumentSourceRead"
    assert all(row["status"] == "recorded" for row in report["archive_members"].values())
    assert all(row["checked"] > 0 for row in report["archive_members"].values())
    assert any(
        row.stage == "policy"
        for item in report["archive_members"].values()
        for row in (
            PackedArchiveMemberDispositions
            if item["section"].get("schema_version") == 2
            else ArchiveMemberDispositions
        )
        .model_validate(item["section"])
        .rows
    )
    assert "Cudzoziemiec" not in json.dumps(report) and url not in json.dumps(report)


@pytest.mark.parametrize("case", ["valid_leaf", "bad_walk_slot", "nonleaf_policy"])
def test_future_member_checks_reject_laundered_slot_and_nonleaf_policy(case: str) -> None:
    data = synthetic_inputs()[
        "synthetic_nested_unknown" if case == "nonleaf_policy" else "synthetic_duplicate_names"
    ]
    root = digest(data)
    url = "https://replay.test/association"

    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        assert url == "https://replay.test/association"
        return data

    extractor = DocumentTextExtractor(PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor())
    loader = TextLoader(
        {"replay.test": download},
        extractor,
        max_bytes=32_000_000,
        physical_factory=PhysicalObservationCollector,
    )
    loader.read(url)
    _, packets = physical(loader)
    packet = packets[root][0]
    metadata = {item.metadata_id: item.metadata for item in packet.metadata}
    order = metadata[packet.capture.execution_id]
    assert isinstance(order, PhysicalRootOrder), "native public capture supplies root order"
    execution = metadata[order.contexts[0][3]]
    assert isinstance(execution, PhysicalContainerExecution), "root context points to its execution"
    row = ArchiveMemberDisposition(
        stage="walk" if case == "bad_walk_slot" else "policy",
        phase="fallback",
        index=0,
        root_sha256=root,
        ordinals=(0,),
        physical_part_index=99 if case == "bad_walk_slot" else 0 if case == "valid_leaf" else None,
        outcome="observed" if case == "bad_walk_slot" else "selected",
        reason=execution.statuses[0] if case == "bad_walk_slot" else "selected_main",
    )
    section = ArchiveMemberDispositions(
        walk="finished",
        retention="complete",
        observed_rows=1,
        rows_sha256=archive_member_rows_sha256((row,)),
        omitted_rows=0,
        unvisited="none_known",
        rows=(row,),
    )
    trace = CandidateDispositionTrace.from_decisions(
        TextDocument(url=url, kind="rcl"),
        (
            CandidateDisposition(
                phase="fallback",
                index=0,
                outcome="selected",
                reason="first_acceptable_candidate",
                observed_sha256=root,
            ),
        ),
        result="selected",
        reason="first_acceptable_candidate",
        selection_state="unverified",
    ).model_copy(update={"archive_members": section})
    if case == "valid_leaf":
        assert verify_members(trace, packets, {root: data})["checked"] == 1
    else:
        with pytest.raises(ValueError, match="actual extracted leaf"):
            verify_members(trace, packets, {root: data})
