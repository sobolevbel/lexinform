import tracemalloc
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from lexinform import physical_root_assembly as assembly
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models.artifacts import ArtifactIdentity, ArtifactMetadata
from lexinform.models.physical_inventory import (
    HeaderDisposition,
    PhysicalContainerExecution,
    PhysicalGraph,
    PhysicalHeaders,
    PhysicalMetadata,
    PhysicalOmission,
    PhysicalOmittedCount,
    PhysicalRootOrder,
    ReadRequest,
    physical_sha256,
)
from lexinform.physical_header_capture import PhysicalHeaderBuilder
from lexinform.physical_root_assembly import (
    ObservedPhysicalContainer,
    ObservedPhysicalOrder,
    PhysicalAssemblyGap,
    PhysicalAssemblyPacket,
    PhysicalRootAssembler,
)
from tests.unit.test_physical_inventory import exact_cap_header, header_row, recipe

ROOT = ArtifactIdentity(sha256="a" * 64, byte_size=100000)
CHILD = ArtifactIdentity(sha256="b" * 64, byte_size=1000)
ORDER = ObservedPhysicalOrder((), (), (), (), (0, "c" * 64))


def leaf(
    index: int = 0, parent: int | None = None, ordinal: int | None = None
) -> ObservedPhysicalContainer:
    return ObservedPhysicalContainer(
        index,
        parent,
        ordinal,
        ROOT if index == 0 else CHILD,
        None,
        (),
        "non_zip",
        None,
        None,
        (),
        (),
        (),
    )


def headers(
    body: ArtifactIdentity, count: int = 1, width: int = 1, member_size: int = 1000
) -> PhysicalMetadata:
    builder = PhysicalHeaderBuilder(
        PhysicalHeaders(
            body_sha=body.sha256,
            central_directory_sha256="d" * 64,
            central_directory_span=(0, 20),
            archive_comment=(0, None),
            rows=(),
        )
    )
    for index in range(count):
        builder.append(header_row(str(index) + "x" * width, member_size))
    return builder.finish(complete=True)


def nested(
    *, duplicate_lookup: bool = False
) -> tuple[PhysicalRootAssembler, ObservedPhysicalOrder]:
    assembler = PhysicalRootAssembler(ROOT)
    assembler.add_container(replace(leaf(1, 0, 1 if duplicate_lookup else 0), physical_index=0))
    count = 2 if duplicate_lookup else 1
    actual = count - 1
    status: HeaderDisposition = "member_complete"
    statuses = (status,) * count
    parent = replace(
        leaf(),
        header=headers(ROOT, count),
        known_members=((actual, CHILD),),
        route="archive",
        statuses=statuses,
        requests=(("member", "ordinal", 0, 0, "complete", 1000, CHILD.sha256, None),),
        opens=((0, "name", "same.docx", actual, "opened", None),),
    )
    assembler.add_container(parent)
    return assembler, ObservedPhysicalOrder(
        ((0, 0),), ((0, 0),), ((1, "e" * 64, "f" * 64),), (), ORDER.legacy_rejections
    )


def accept_packet(repo: SqliteBillRepository, packet: PhysicalAssemblyPacket) -> None:
    now = datetime(2026, 10, 30, tzinfo=UTC)
    for body in packet.artifacts:
        repo.save_artifact_metadata(
            ArtifactMetadata(
                sha256=body.sha256,
                byte_size=body.byte_size,
                recorded_at=now,
            )
        )
    repo.save_physical_inventory(packet.metadata, (packet.capture,), recorded_at=now)
    saved = repo.get_physical_capture(packet.capture.capture_id)
    assert saved is not None, "the shared structural reader accepts its retained rooted closure"
    before = repo.dump()
    repo.restore(before)
    assert repo.dump() == before


def test_one_complete_root_preserves_exact_graph_unit_order_identities() -> None:
    assembler = PhysicalRootAssembler(ROOT)
    assembler.add_container(leaf())
    packet = assembler.finish(recipe(), ORDER, complete=True)
    assert isinstance(packet, PhysicalAssemblyPacket)
    graph = PhysicalMetadata.of(PhysicalGraph(body_sha=ROOT.sha256, header=None, edges=()))
    unit = PhysicalMetadata.of(
        PhysicalContainerExecution(
            graph=graph.metadata_id,
            route="non_zip",
            propagated_error=None,
            format_error=None,
            statuses=(),
            requests=(),
            opens=(),
        ),
        body_sha256=ROOT.sha256,
    )
    order = PhysicalMetadata.of(
        PhysicalRootOrder(
            contexts=((None, None, None, unit.metadata_id),),
            requests=(),
            opens=(),
            physical=(),
            zip_failures=(),
            legacy_rejections=ORDER.legacy_rejections,
        ),
        body_sha256=ROOT.sha256,
    )
    assert packet.capture.inventory_id == graph.metadata_id
    assert packet.capture.execution_id == order.metadata_id
    assert packet.artifacts == (ROOT,) and packet.capture.extraction_id is None
    assert {item.metadata_id for item in packet.metadata} == {
        graph.metadata_id,
        unit.metadata_id,
        order.metadata_id,
        recipe().metadata_id,
    }


def test_duplicate_name_actual_open_is_not_replaced_by_lookup_or_enumerated_ordinal() -> None:
    assembler, order = nested(duplicate_lookup=True)
    packet = assembler.finish(recipe(), order, complete=True)
    assert isinstance(packet, PhysicalAssemblyPacket)
    graph = next(
        item.metadata for item in packet.metadata if item.metadata_id == packet.capture.inventory_id
    )
    assert isinstance(graph, PhysicalGraph)
    assert graph.edges[0] == (None, None)
    assert graph.edges[1][0] == CHILD.sha256 and graph.edges[1][1] is not None
    root = next(
        item.metadata for item in packet.metadata if item.metadata_id == packet.capture.execution_id
    )
    assert isinstance(root, PhysicalRootOrder) and root.contexts[1][1] == 1
    unit_id = root.contexts[0][3]
    unit = next(item.metadata for item in packet.metadata if item.metadata_id == unit_id)
    assert isinstance(unit, PhysicalContainerExecution)
    assert unit.requests[0][2:4] == (0, 0) and unit.opens[0][3] == 1
    assert packet.artifacts == (ROOT, CHILD)


def test_interrupted_root_retains_known_child_facts_without_a_completed_hash() -> None:
    assembler, order = nested()
    original = KeyboardInterrupt()
    packet: PhysicalAssemblyPacket | PhysicalAssemblyGap | None = None
    with pytest.raises(KeyboardInterrupt) as caught:
        try:
            raise original
        finally:
            packet = assembler.finish(recipe(), order, complete=False)
    assert caught.value is original and isinstance(packet, PhysicalAssemblyPacket)
    root = next(item for item in packet.metadata if item.metadata_id == packet.capture.execution_id)
    assert root.coverage == "partial" and root.omission is not None
    assert root.omission.full_metadata_sha256 is None and root.omission.unknown_fields == 1
    graph = next(
        item.metadata for item in packet.metadata if item.metadata_id == packet.capture.inventory_id
    )
    assert isinstance(graph, PhysicalGraph) and graph.edges[0][0] == CHILD.sha256


def test_interruption_before_root_facts_reports_a_gap_without_inventing_a_route() -> None:
    result = PhysicalRootAssembler(ROOT).finish(recipe(), ORDER, complete=False)
    assert isinstance(result, PhysicalAssemblyGap)
    assert result.reason == "missing_root_observation" and result.artifact == ROOT
    assert result.full_metadata_sha256 is None


@pytest.mark.parametrize("complete", [False, True])
def test_oversized_graph_retains_an_honest_rooted_prefix_without_fake_unknown_edges(
    complete: bool, repo: SqliteBillRepository
) -> None:
    assembler = PhysicalRootAssembler(ROOT)
    header = headers(ROOT, 4000)
    assembler.add_container(
        replace(
            leaf(),
            header=header,
            statuses=("readability_rejected",) * 4000,
            route="archive",
            complete=complete,
        )
    )
    result = assembler.finish(recipe(), ORDER, complete=complete)
    assert isinstance(result, PhysicalAssemblyPacket)
    graph = next(
        item for item in result.metadata if item.metadata_id == result.capture.inventory_id
    )
    assert isinstance(graph.metadata, PhysicalGraph) and graph.metadata.edges == ()
    assert graph.omission is not None and graph.omission.omitted[0].count == 4000
    assert graph.omission.full_metadata_sha256 is None
    accept_packet(repo, result)


def test_repeated_contexts_use_bounded_prefix_closure_despite_content_deduplication(
    repo: SqliteBillRepository,
) -> None:
    assembler = PhysicalRootAssembler(ROOT)
    for index in range(1, 601):
        assembler.add_container(leaf(index, 0, index - 1))
    assembler.add_container(
        replace(
            leaf(),
            header=headers(ROOT, 600),
            known_members=tuple((i, CHILD) for i in range(600)),
            route="archive",
            statuses=("member_complete",) * 600,
        )
    )
    result = assembler.finish(recipe(), ORDER, complete=False)
    assert isinstance(result, PhysicalAssemblyPacket)
    root = next(item for item in result.metadata if item.metadata_id == result.capture.execution_id)
    assert isinstance(root.metadata, PhysicalRootOrder) and root.metadata.contexts == ()
    assert root.omission is not None and root.omission.full_metadata_sha256 is None
    assert root.omitted("contexts") == 601
    assert result.artifacts == (ROOT,)
    accept_packet(repo, result)


def test_distinct_root_union_uses_an_honest_prefix_with_header_and_recipe_storage(
    repo: SqliteBillRepository,
) -> None:
    assembler = PhysicalRootAssembler(ROOT)
    for index in range(1, 41):
        body = ArtifactIdentity(sha256=f"{index:064x}", byte_size=100000)
        assembler.add_container(
            replace(
                leaf(index, 0, index - 1),
                body=body,
                header=headers(body, width=20000),
                route="word",
                statuses=("uninspected_format_part",),
            )
        )
    assembler.add_container(
        replace(
            leaf(),
            header=headers(ROOT, 40, member_size=100000),
            known_members=tuple(
                (index - 1, ArtifactIdentity(sha256=f"{index:064x}", byte_size=100000))
                for index in range(1, 41)
            ),
            route="archive",
            statuses=("member_complete",) * 40,
        )
    )
    result = assembler.finish(recipe(), ORDER, complete=False)
    assert isinstance(result, PhysicalAssemblyPacket)
    assert result.artifacts == (ROOT,)
    assert (
        sum(item.budget_bytes for item in result.metadata) + result.capture.budget_bytes <= 524288
    )
    assert all(item.kind not in ("unit",) for item in result.metadata)
    accept_packet(repo, result)


def test_malformed_known_body_cannot_be_hidden_by_a_later_successful_finish() -> None:
    assembler = PhysicalRootAssembler(ROOT)
    assembler.add_container(leaf(1, 0, 0))
    wrong = CHILD.model_copy(update={"byte_size": 999})
    with pytest.raises(ValueError, match="conflicting byte sizes"):
        assembler.add_container(
            replace(
                leaf(),
                header=headers(ROOT),
                known_members=((0, wrong),),
                route="archive",
                statuses=("member_complete",),
            )
        )
    result = assembler.finish(recipe(), ORDER, complete=False)
    assert isinstance(result, PhysicalAssemblyGap) and result.reason == "invalid_observed_facts"


def test_completed_read_requires_actual_known_member_size_and_hash() -> None:
    assembler = PhysicalRootAssembler(ROOT)
    with pytest.raises(ValueError, match="actual member body"):
        assembler.add_container(
            replace(
                leaf(),
                header=headers(ROOT),
                known_members=((0, CHILD),),
                route="archive",
                statuses=("member_complete",),
                requests=(("member", "ordinal", 0, 0, "complete", 999, CHILD.sha256, None),),
            )
        )


def test_order_requires_each_actual_local_event_exactly_once() -> None:
    assembler, order = nested()
    with pytest.raises(ValueError, match="every local event exactly once"):
        assembler.finish(recipe(), replace(order, requests=()), complete=True)


def test_depth_stop_keeps_observed_header_without_inventing_an_unopened_payload() -> None:
    assembler = PhysicalRootAssembler(ROOT)
    for index in reversed(range(5)):
        body = ROOT if index == 0 else ArtifactIdentity(sha256=f"{index:064x}", byte_size=1000)
        child = ArtifactIdentity(sha256=f"{index + 1:064x}", byte_size=1000)
        stopped = index == 4
        assembler.add_container(
            ObservedPhysicalContainer(
                index,
                index - 1 if index else None,
                0 if index else None,
                body,
                headers(body),
                () if stopped else ((0, child),),
                "archive",
                None,
                None,
                ("not_visited_depth_limit",) if stopped else ("member_complete",),
                ()
                if stopped
                else (("member", "ordinal", 0, 0, "complete", 1000, child.sha256, None),),
                (),
            )
        )
    order = replace(ORDER, requests=tuple((index, 0) for index in range(4)))
    packet = assembler.finish(recipe(), order, complete=True)
    assert isinstance(packet, PhysicalAssemblyPacket)
    stopped_graphs = [
        item.metadata
        for item in packet.metadata
        if isinstance(item.metadata, PhysicalGraph) and item.body_sha256 == f"{4:064x}"
    ]
    assert len(stopped_graphs) == 1 and stopped_graphs[0].edges == ((None, None),)
    assert len(packet.artifacts) == 5


def test_root_order_overflow_keeps_known_full_hash_and_restorable_prefix(
    repo: SqliteBillRepository,
) -> None:
    count = 200
    assembler = PhysicalRootAssembler(ROOT)
    for index in range(1, count + 1):
        assembler.add_container(replace(leaf(index, 0, index - 1), physical_index=index - 1))
    header = headers(ROOT, count)
    status: HeaderDisposition = "member_complete"
    requests: tuple[ReadRequest, ...] = tuple(
        ("member", "ordinal", index, index, "complete", 1000, CHILD.sha256, None)
        for index in range(count)
    )
    parent = replace(
        leaf(),
        header=header,
        known_members=tuple((i, CHILD) for i in range(count)),
        route="archive",
        statuses=(status,) * count,
        requests=requests,
    )
    assembler.add_container(parent)
    order = ObservedPhysicalOrder(
        tuple((0, i) for i in range(count)),
        (),
        tuple((i + 1, "e" * 64, "f" * 64) for i in range(count)),
        (),
        ORDER.legacy_rejections,
    )
    packet = assembler.finish(recipe(), order, complete=True)
    assert isinstance(packet, PhysicalAssemblyPacket)
    child_graph = PhysicalMetadata.of(PhysicalGraph(body_sha=CHILD.sha256, header=None, edges=()))
    child_unit = PhysicalMetadata.of(
        PhysicalContainerExecution(
            graph=child_graph.metadata_id,
            route="non_zip",
            propagated_error=None,
            format_error=None,
            statuses=(),
            requests=(),
            opens=(),
        ),
        body_sha256=CHILD.sha256,
    )
    root_graph = PhysicalMetadata.of(
        PhysicalGraph(
            body_sha=ROOT.sha256,
            header=header.metadata_id,
            edges=((CHILD.sha256, child_graph.metadata_id),) * count,
        )
    )
    root_unit = PhysicalMetadata.of(
        PhysicalContainerExecution.model_validate(
            {
                "graph": root_graph.metadata_id,
                "route": "archive",
                "propagated_error": None,
                "format_error": None,
                "statuses": (status,) * count,
                "requests": requests,
                "opens": (),
            }
        ),
        body_sha256=ROOT.sha256,
        header_id=header.metadata_id,
    )
    full = PhysicalRootOrder(
        contexts=(
            (None, None, None, root_unit.metadata_id),
            *tuple((0, i, i, child_unit.metadata_id) for i in range(count)),
        ),
        requests=order.requests,
        opens=(),
        physical=order.physical,
        zip_failures=(),
        legacy_rejections=order.legacy_rejections,
    )
    root = next(item for item in packet.metadata if item.metadata_id == packet.capture.execution_id)
    assert root.omission is not None and root.omission.reason == "over_limit"
    assert root.omission.full_metadata_sha256 == physical_sha256(full.model_dump(mode="json"))
    assert root.omitted("contexts") == count + 1 and root.omitted("physical") == count
    graph = next(
        item for item in packet.metadata if item.metadata_id == packet.capture.inventory_id
    )
    assert graph.omission is not None
    assert graph.omission.full_metadata_sha256 == root_graph.metadata_id
    assert isinstance(graph.metadata, PhysicalGraph) and graph.metadata.edges == ()
    accept_packet(repo, packet)


def test_actual_header_universe_is_required_for_an_already_known_member_ordinal() -> None:
    builder = PhysicalHeaderBuilder(
        PhysicalHeaders(
            body_sha=ROOT.sha256,
            central_directory_sha256="d" * 64,
            central_directory_span=(0, 20),
            archive_comment=(0, None),
            rows=(),
        )
    )
    builder.append(header_row("first.docx", 1000))
    partial = builder.finish(complete=False)
    assembler = PhysicalRootAssembler(ROOT)
    with pytest.raises(ValueError, match="actual header universe"):
        assembler.add_container(
            replace(
                leaf(),
                header=partial,
                known_members=((2, CHILD),),
                route="archive",
                statuses=("uninspected_format_part",),
            )
        )
    result = assembler.finish(recipe(), ORDER, complete=False)
    assert isinstance(result, PhysicalAssemblyGap)
    assert result.reason == "header_universe_unavailable"
    assert result.full_metadata_sha256 is None


def test_valid_historical_exact_cap_header_uses_a_coherent_future_timestamp_prefix(
    repo: SqliteBillRepository,
) -> None:
    small = exact_cap_header(1)
    original = exact_cap_header(1 + 32768 - small.budget_bytes)
    assembler = PhysicalRootAssembler(ROOT)
    assembler.add_container(
        replace(
            leaf(),
            header=original,
            route="archive",
            statuses=("readability_rejected",),
        )
    )
    packet = assembler.finish(recipe(), ORDER, complete=True)
    assert isinstance(packet, PhysicalAssemblyPacket)
    retained = next(item for item in packet.metadata if item.kind == "headers")
    assert retained.metadata_id != original.metadata_id and retained.omission is not None
    assert retained.omission.full_metadata_sha256 == original.metadata_id
    assert retained.omitted("rows") == 1
    graph = next(
        item for item in packet.metadata if item.metadata_id == packet.capture.inventory_id
    )
    assert graph.header_id == retained.metadata_id
    accept_packet(repo, packet)


def counted_header(body: ArtifactIdentity, count: int) -> PhysicalMetadata:
    return PhysicalMetadata.of(
        PhysicalHeaders(
            body_sha=body.sha256,
            central_directory_sha256="d" * 64,
            central_directory_span=(0, 20),
            archive_comment=(0, None),
            rows=(),
        ),
        omission=PhysicalOmission(
            reason="missing_metadata",
            full_metadata_sha256=None,
            omitted=(PhysicalOmittedCount(field="rows", count=count),),
        ),
    )


def test_huge_known_header_universe_is_preflighted_without_expansion_or_large_copies(
    repo: SqliteBillRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def guarded_range(size: int) -> range:
        if size > 100000:
            raise AssertionError("omitted header universe must not be expanded")
        return range(size)

    monkeypatch.setattr(assembly, "range", guarded_range, raising=False)
    count = 2**63 - 1
    assembler = PhysicalRootAssembler(ROOT)
    observed = replace(
        leaf(),
        header=counted_header(ROOT, count),
        known_members=((0, CHILD),),
        route="archive",
        statuses=(),
        complete=False,
    )
    tracemalloc.start()
    try:
        assembler.add_container(leaf(1, 0, 0))
        assembler.add_container(observed)
        packet = assembler.finish(recipe(), ORDER, complete=False)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 4_000_000
    assert isinstance(packet, PhysicalAssemblyPacket)
    graph = next(
        item for item in packet.metadata if item.metadata_id == packet.capture.inventory_id
    )
    assert graph.omitted("edges") == count
    assert graph.omission is not None and graph.omission.full_metadata_sha256 is None
    assert isinstance(graph.metadata, PhysicalGraph) and graph.metadata.edges == ()
    accept_packet(repo, packet)


def test_large_supplied_unit_name_is_streamed_without_copying_the_whole_payload(
    repo: SqliteBillRepository,
) -> None:
    name = "a" * 2_000_000
    assembler = PhysicalRootAssembler(ROOT)
    observed = replace(
        leaf(),
        header=headers(ROOT),
        route="word",
        statuses=("format_failed",),
        requests=(("xml", "name", name, None, "failed", None, None, "KeyError"),),
    )
    order = replace(ORDER, requests=((0, 0),))
    tracemalloc.start()
    try:
        assembler.add_container(observed)
        packet = assembler.finish(recipe(), order, complete=True)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 4_000_000
    assert isinstance(packet, PhysicalAssemblyPacket)
    assert all(name not in record.model_dump_json() for record in packet.metadata)
    accept_packet(repo, packet)


def test_duplicate_address_after_eviction_never_becomes_an_extra_context_count() -> None:
    assembler = PhysicalRootAssembler(ROOT)
    oversized = replace(
        leaf(1, 0, 0),
        header=counted_header(CHILD, 2**63 - 1),
        route="archive",
        complete=False,
    )
    assembler.add_container(oversized)
    with pytest.raises(ValueError, match="already verified"):
        assembler.add_container(oversized)
    result = assembler.finish(recipe(), ORDER, complete=False)
    assert isinstance(result, PhysicalAssemblyGap)
    assert result.distinct_addresses == 1 and result.reason == "invalid_observed_facts"


def test_conflicting_body_size_after_eviction_is_not_silently_omitted() -> None:
    assembler = PhysicalRootAssembler(ROOT)
    assembler.add_container(
        replace(
            leaf(1, 0, 0),
            header=counted_header(CHILD, 2**63 - 1),
            route="archive",
            complete=False,
        )
    )
    with pytest.raises(ValueError, match="conflicting byte sizes"):
        assembler.add_container(
            replace(leaf(2, 0, 1), body=CHILD.model_copy(update={"byte_size": 999}))
        )
    result = assembler.finish(recipe(), ORDER, complete=False)
    assert isinstance(result, PhysicalAssemblyGap) and result.distinct_addresses == 1


def test_missing_parent_after_eviction_is_an_explicit_input_gap() -> None:
    assembler = PhysicalRootAssembler(ROOT)
    assembler.add_container(
        replace(
            leaf(2, 1, 0),
            header=counted_header(CHILD, 2**63 - 1),
            route="archive",
            complete=False,
        )
    )
    assembler.add_container(leaf())
    result = assembler.finish(recipe(), ORDER, complete=False)
    assert isinstance(result, PhysicalAssemblyGap)
    assert result.reason == "parent_observation_unavailable" and result.distinct_addresses == 2


def test_duplicate_global_order_is_rejected_even_after_metadata_eviction() -> None:
    assembler = PhysicalRootAssembler(ROOT)
    requests: tuple[ReadRequest, ...] = (
        ("xml", "name", "missing.xml", None, "failed", None, None, "KeyError"),
    ) * 1000
    assembler.add_container(
        replace(
            leaf(),
            header=headers(ROOT),
            route="word",
            statuses=("format_failed",),
            requests=requests,
        )
    )
    with pytest.raises(ValueError, match="repeats"):
        assembler.finish(recipe(), replace(ORDER, requests=((0, 0),) * 1000), complete=True)


def test_complete_known_child_must_match_retained_header_size_without_read_log() -> None:
    assembler = PhysicalRootAssembler(ROOT)
    assembler.add_container(leaf(1, 0, 0))
    with pytest.raises(ValueError, match="retained declared size"):
        assembler.add_container(
            replace(
                leaf(),
                header=headers(ROOT, member_size=999),
                known_members=((0, CHILD),),
                route="archive",
                statuses=("member_complete",),
            )
        )
    result = assembler.finish(recipe(), ORDER, complete=False)
    assert isinstance(result, PhysicalAssemblyGap) and result.reason == "invalid_observed_facts"


def test_returned_length_mismatch_retains_size_without_inventing_complete_child_hash(
    repo: SqliteBillRepository,
) -> None:
    assembler = PhysicalRootAssembler(ROOT)
    assembler.add_container(
        replace(
            leaf(),
            header=headers(ROOT, member_size=999),
            route="archive",
            statuses=("member_returned_length_differs",),
            requests=(("member", "ordinal", 0, 0, "returned_length_differs", 1000, None, None),),
        )
    )
    packet = assembler.finish(recipe(), replace(ORDER, requests=((0, 0),)), complete=True)
    assert isinstance(packet, PhysicalAssemblyPacket) and packet.artifacts == (ROOT,)
    graph = next(
        item.metadata for item in packet.metadata if item.metadata_id == packet.capture.inventory_id
    )
    assert isinstance(graph, PhysicalGraph) and graph.edges == ((None, None),)
    unit = next(
        item.metadata
        for item in packet.metadata
        if isinstance(item.metadata, PhysicalContainerExecution)
    )
    assert unit.requests[0][5:7] == (1000, None)
    accept_packet(repo, packet)


def test_omitted_header_size_stays_unknown_instead_of_being_guessed(
    repo: SqliteBillRepository,
) -> None:
    full = headers(ROOT)
    assert isinstance(full.metadata, PhysicalHeaders)
    partial = PhysicalMetadata.of(
        full.metadata.model_copy(update={"rows": ()}),
        omission=PhysicalOmission(
            reason="over_limit",
            full_metadata_sha256=full.metadata_id,
            omitted=(PhysicalOmittedCount(field="rows", count=1),),
        ),
    )
    assembler = PhysicalRootAssembler(ROOT)
    assembler.add_container(leaf(1, 0, 0))
    assembler.add_container(
        replace(
            leaf(),
            header=partial,
            known_members=((0, CHILD),),
            route="archive",
            statuses=("member_complete",),
        )
    )
    packet = assembler.finish(recipe(), ORDER, complete=True)
    assert isinstance(packet, PhysicalAssemblyPacket)
    retained = next(item for item in packet.metadata if item.kind == "headers")
    assert isinstance(retained.metadata, PhysicalHeaders), (
        "the headers kind retains its closed payload"
    )
    assert retained == partial and retained.metadata.rows == ()
    accept_packet(repo, packet)


def test_integrity_work_limit_reports_unverified_input_without_inventing_context_counts() -> None:
    requests: tuple[ReadRequest, ...] = (
        ("xml", "name", "missing.xml", None, "failed", None, None, "KeyError"),
    ) * 65537
    assembler = PhysicalRootAssembler(ROOT)
    with pytest.raises(ValueError, match="bounded integrity work"):
        assembler.add_container(
            replace(
                leaf(),
                header=headers(ROOT),
                route="word",
                statuses=("format_failed",),
                requests=requests,
            )
        )
    result = assembler.finish(recipe(), ORDER, complete=False)
    assert isinstance(result, PhysicalAssemblyGap)
    assert result.reason == "integrity_proof_capacity" and result.distinct_addresses == 0
