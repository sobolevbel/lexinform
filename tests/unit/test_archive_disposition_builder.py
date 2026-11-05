from collections.abc import Iterable, Iterator
from typing import cast

import pytest

from lexinform.archive_dispositions import enrich_archive_trace
from lexinform.models import ArchiveMemberDisposition
from lexinform.models.candidate_trace import archive_member_rows_sha256
from tests.unit.test_archive_member_dispositions import legacy, row


def rows(count: int, *, large: bool = False) -> tuple[ArchiveMemberDisposition, ...]:
    return tuple(
        row(ordinal=index).model_copy(
            update={
                "leaf_sha256": "b" * 64 if large else None,
                "text_sha256": "c" * 64 if large else None,
            }
        )
        for index in range(count)
    )


def test_complete_stream_keeps_old_outer_fields_and_singleton_contract_unchanged() -> None:
    original = legacy()
    before = original.model_dump_json()
    actual = (row(stage="walk"), row())
    enriched = enrich_archive_trace(original, actual, walk="finished", unvisited="none_known")
    members = enriched.archive_members
    assert members is not None, "known actual rows fit the new archive section"
    assert members.retention == "complete" and members.rows == actual
    assert members.observed_rows == 2 and members.omitted_rows == 0
    assert members.rows_sha256 == archive_member_rows_sha256(actual)
    assert enriched.model_dump(exclude={"archive_members"}) == original.model_dump(
        exclude={"archive_members"}
    )
    assert original.model_dump_json() == before
    assert enriched.trace_id != original.trace_id and enriched.is_routine_singleton


@pytest.mark.parametrize("large", [False, True])
def test_one_pass_stream_hashes_every_row_and_retains_only_an_honest_bounded_prefix(
    large: bool,
) -> None:
    actual = rows(64, large=large)
    seen: list[int] = []

    def stream() -> Iterator[ArchiveMemberDisposition]:
        for index, item in enumerate(actual):
            seen.append(index)
            yield item

    enriched = enrich_archive_trace(legacy(), stream(), walk="finished", unvisited="known")
    members = enriched.archive_members
    assert members is not None, "the bounded prefix or summary fits the ordinary trace"
    assert seen == list(range(64))
    assert members.retention == "prefix"
    assert members.rows == actual[: len(members.rows)] and 0 < len(members.rows) <= 8
    assert members.observed_rows == 64 and members.omitted_rows == 64 - len(members.rows)
    assert members.rows_sha256 == archive_member_rows_sha256(actual)
    assert members.rows_sha256 != archive_member_rows_sha256(members.rows)
    assert len(members.model_dump_json().encode()) <= 1024
    assert len(enriched.model_dump_json(exclude_defaults=True).encode()) <= 4096


def test_whole_outer_omission_accepts_only_a_summary_with_full_stream_hash() -> None:
    original = legacy(url="https://api.test/" + "x" * 5000)
    assert original.coverage == "omitted_over_limit", (
        "the ordinary factory already omits this request"
    )
    actual = rows(20)
    enriched = enrich_archive_trace(original, actual, walk="interrupted", unvisited="known")
    members = enriched.archive_members
    assert members is not None and members.retention == "omitted", (
        "the bounded archive helper guarantees this section for the controlled fixture"
    )
    assert not members.rows and members.observed_rows == members.omitted_rows == 20
    assert members.rows_sha256 == archive_member_rows_sha256(actual)
    assert enriched.requested_document is None, (
        "the checked public fixture path guarantees this typed result"
    )
    assert enriched.model_dump(exclude={"archive_members"}) == original.model_dump(
        exclude={"archive_members"}
    )


def test_unavailable_facts_are_unknown_not_an_empty_observed_universe() -> None:
    enriched = enrich_archive_trace(legacy(), None, walk="finished", unvisited="none_known")
    members = enriched.archive_members
    assert members is not None and members.retention == "unknown", (
        "the bounded archive helper guarantees this section for the controlled fixture"
    )
    assert members.walk == members.unvisited == "unknown"
    assert (
        members.observed_rows is None
        and members.rows_sha256 is None
        and members.omitted_rows is None
    ), "the bounded archive helper guarantees this section for the controlled fixture"
    assert not members.rows


def test_empty_known_stream_has_exact_zero_count_and_empty_array_hash() -> None:
    enriched = enrich_archive_trace(legacy(), (), walk="finished", unvisited="none_known")
    members = enriched.archive_members
    assert members is not None and members.retention == "complete", (
        "the bounded archive helper guarantees this section for the controlled fixture"
    )
    assert members.observed_rows == members.omitted_rows == 0
    assert members.rows_sha256 == archive_member_rows_sha256(())


@pytest.mark.parametrize("actual", [rows(20), None])
def test_archive_summary_survives_a_nearly_full_outer_trace(
    actual: tuple[ArchiveMemberDisposition, ...] | None,
) -> None:
    original = legacy(url="https://api.test/" + "x" * 3500)
    assert original.coverage == "complete", (
        "the controlled ordinary trace still fits its own budget"
    )
    before = original.model_dump_json()
    enriched = enrich_archive_trace(original, actual, walk="finished", unvisited="unknown")
    assert enriched.coverage == "omitted_over_limit"
    assert enriched.universe_sha256 == original.universe_sha256
    assert enriched.omitted_dispositions == len(original.dispositions)
    assert enriched.requested_document is None and not enriched.dispositions
    members = enriched.archive_members
    assert members is not None, "the archive summary fits once the outer request is omitted"
    assert not members.rows
    if actual is None:
        assert members.retention == "unknown" and members.observed_rows is None
    else:
        assert members.retention == "omitted"
        assert members.observed_rows == members.omitted_rows == len(actual)
        assert members.rows_sha256 == archive_member_rows_sha256(actual)
    assert original.model_dump_json() == before


@pytest.mark.parametrize("error", [ValueError("metadata"), RuntimeError("metadata")])
def test_optional_stream_failure_preserves_the_exact_ordinary_trace(error: Exception) -> None:
    original = legacy()
    before = original.model_dump_json()

    def stream() -> Iterator[ArchiveMemberDisposition]:
        yield row()
        raise error

    assert (
        enrich_archive_trace(original, stream(), walk="finished", unvisited="unknown") is original
    ), "the public native loader retains this fixture's prospective archive trace"
    assert original.model_dump_json() == before


@pytest.mark.parametrize("error", [KeyboardInterrupt(), SystemExit(7), GeneratorExit()])
def test_stream_cancellation_propagates_the_same_object_without_mutating_the_trace(
    error: BaseException,
) -> None:
    original = legacy()
    before = original.model_dump_json()

    def stream() -> Iterator[ArchiveMemberDisposition]:
        yield row()
        raise error

    with pytest.raises(type(error)) as caught:
        enrich_archive_trace(original, stream(), walk="unknown", unvisited="unknown")
    assert caught.value is error and original.model_dump_json() == before, (
        "the checked public fixture path guarantees this typed result"
    )


@pytest.mark.parametrize(
    "fault", ["row", "outer_index", "duplicate", "root_conflict", "cross_stage"]
)
def test_invalid_tail_after_the_retained_prefix_is_not_hash_certified(fault: str) -> None:
    original = legacy()
    actual = rows(20)
    if fault == "row":
        last = row(ordinal=20).model_copy(update={"reason": "member_failed"})
    elif fault == "outer_index":
        last = row(ordinal=20).model_copy(update={"index": 1})
    elif fault == "duplicate":
        last = actual[0]
    elif fault == "root_conflict":
        last = row(ordinal=20).model_copy(update={"root_sha256": "d" * 64})
    else:
        last = row(stage="walk").model_copy(update={"ordinals": (20,)})
    assert (
        enrich_archive_trace(original, (*actual, last), walk="finished", unvisited="unknown")
        is original
    ), "the public native loader retains this fixture's prospective archive trace"


def test_an_existing_prefix_is_not_rebound_or_overwritten_and_a_summary_is_not_a_row_stream() -> (
    None
):
    enriched = enrich_archive_trace(legacy(), rows(20), walk="finished", unvisited="known")
    members = enriched.archive_members
    assert members is not None and members.retention == "prefix", (
        "the bounded archive helper guarantees this section for the controlled fixture"
    )

    def forbidden() -> Iterator[ArchiveMemberDisposition]:
        yield from ()
        raise KeyboardInterrupt("already frozen work must not consume another stream")

    assert (
        enrich_archive_trace(enriched, forbidden(), walk="unknown", unvisited="unknown") is enriched
    ), "the public native loader retains this fixture's prospective archive trace"
    original = legacy()
    wrong = cast(Iterable[ArchiveMemberDisposition], cast(object, members))
    assert (
        enrich_archive_trace(original, wrong, walk="finished", unvisited="unknown") is original
    ), "the public native loader retains this fixture's prospective archive trace"


def test_validation_bookkeeping_overflow_is_unknown_without_a_partial_hash_or_tail_claim() -> None:
    actual = rows(1000, large=True)
    seen: list[int] = []

    def stream() -> Iterator[ArchiveMemberDisposition]:
        for index, item in enumerate(actual):
            seen.append(index)
            yield item

    enriched = enrich_archive_trace(legacy(), stream(), walk="finished", unvisited="none_known")
    members = enriched.archive_members
    assert members is not None and members.retention == "unknown", (
        "the bounded archive helper guarantees this section for the controlled fixture"
    )
    assert (
        members.observed_rows is None
        and members.rows_sha256 is None
        and members.omitted_rows is None
    ), "the bounded archive helper guarantees this section for the controlled fixture"
    assert not members.rows and members.walk == members.unvisited == "unknown"
    assert 8 < len(seen) < len(actual)
