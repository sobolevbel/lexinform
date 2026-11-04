import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from tools.source_history_doctor import audit

from lexinform.adapters.sqlite_repo import SCHEMA_VERSION, SqliteBillRepository
from lexinform.models import (
    CandidateDisposition,
    CandidateDispositionTrace,
    DocumentSelection,
    DocumentSelectionRecord,
    SourceObservation,
    TextDocument,
)
from lexinform.models.candidate_trace import (
    ArchiveMemberDisposition,
    ArchiveMemberDispositions,
    archive_member_rows_sha256,
)
from tests.unit.test_candidate_disposition_trace import NOW

ROOT = "a" * 64


def legacy(
    *, url: str = "https://api.test/archive.zip", failed: bool = False
) -> CandidateDispositionTrace:
    return CandidateDispositionTrace.from_decisions(
        TextDocument(url=url, kind="rcl"),
        (
            CandidateDisposition(
                phase="fallback",
                index=0,
                outcome="read_failure" if failed else "selected",
                reason="read_error" if failed else "first_acceptable_candidate",
                observed_sha256=None if failed else ROOT,
            ),
        ),
        result="fallback" if failed else "selected",
        reason="all_rejected" if failed else "first_acceptable_candidate",
        selection_state="unsupported" if failed else "unverified",
    )


def row(*, stage: str = "policy", ordinal: int = 0) -> ArchiveMemberDisposition:
    return ArchiveMemberDisposition.model_validate(
        {
            "stage": stage,
            "phase": "fallback",
            "index": 0,
            "root_sha256": ROOT,
            "ordinals": (ordinal,),
            "physical_part_index": ordinal,
            "outcome": "observed" if stage == "walk" else "selected",
            "reason": "member_complete" if stage == "walk" else "selected_main",
        }
    )


def section(rows: tuple[ArchiveMemberDisposition, ...] | None = None) -> ArchiveMemberDispositions:
    actual = rows if rows is not None else (row(stage="walk"), row())
    return ArchiveMemberDispositions(
        walk="finished",
        retention="complete",
        observed_rows=len(actual),
        omitted_rows=0,
        rows_sha256=archive_member_rows_sha256(actual),
        unvisited="none_known",
        rows=actual,
    )


def enriched(
    original: CandidateDispositionTrace | None = None,
    members: ArchiveMemberDispositions | None = None,
) -> CandidateDispositionTrace:
    return CandidateDispositionTrace.model_validate(
        {**(original or legacy()).model_dump(), "archive_members": members or section()}
    )


def test_literal_legacy_json_and_ids_and_default_factory_remain_byte_exact() -> None:
    original = legacy()
    expected = '{"coverage":"complete","requested_document":{"url":"https://api.test/archive.zip","kind":"rcl"},"universe_sha256":"e824a3f7a4c99a5fb6737bdd6127f03b3f8a72477932026ae423413ccb2eaeac","candidate_count":1,"omitted_dispositions":0,"dispositions":[{"phase":"fallback","index":0,"outcome":"selected","reason":"first_acceptable_candidate","observed_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}],"result":"selected","reason":"first_acceptable_candidate","selection_state":"unverified"}'
    assert original.model_dump_json(exclude_defaults=True) == expected
    assert (
        original.trace_id
        == "candidate-trace-v1:b72759fd0659329550200effcdebf2d870756f316cf98fd4a8ea43c677e8040e"
    )
    assert (
        hashlib.sha256(original.model_dump_json().encode()).hexdigest()
        == "a2202a3f165377bd548a896351b505153394f5321de9cb98856424aced7207cc"
    )
    assert original.archive_members is None and original.is_routine_singleton
    assert CandidateDispositionTrace.model_validate_json(expected) == original
    assert enriched().is_routine_singleton


def test_walk_and_policy_share_occurrence_without_equating_physical_completion_to_selection() -> (
    None
):
    trace = enriched()
    members = trace.archive_members
    assert members is not None, "the controlled future trace carries an archive section"
    assert members.observed_rows == 2 and len(members.model_dump_json().encode()) <= 1024
    assert members.rows[0].outcome == "observed" and members.rows[1].outcome == "selected"
    assert all(item.association == "known" for item in members.rows)
    canonical = json.dumps(
        [item.model_dump(mode="json", exclude_defaults=True) for item in members.rows],
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    assert members.rows_sha256 == hashlib.sha256(canonical.encode()).hexdigest()
    assert (
        CandidateDispositionTrace.model_validate_json(trace.model_dump_json(exclude_defaults=True))
        == trace
    )


@pytest.mark.parametrize(
    "stage,outcome,reason",
    [
        ("walk", "selected", "member_complete"),
        ("policy", "selected", "member_complete"),
        ("walk", "observed", "selected_main"),
        ("policy", "observed", "selected_main"),
        ("policy", "selected", "rank_preference"),
    ],
)
def test_stage_does_not_launder_walk_status_as_policy(
    stage: str, outcome: str, reason: str
) -> None:
    with pytest.raises(ValidationError):
        ArchiveMemberDisposition.model_validate(
            {**row().model_dump(), "stage": stage, "outcome": outcome, "reason": reason}
        )


def test_missing_path_is_unknown_even_with_an_actual_physical_slot() -> None:
    item = ArchiveMemberDisposition.model_validate(
        {**row().model_dump(), "root_sha256": None, "ordinals": None}
    )
    assert item.physical_part_index == 0 and item.association == "unknown"
    assert enriched(members=section((item,))).archive_members is not None


@pytest.mark.parametrize(
    "updates",
    [
        {"root_sha256": None},
        {"ordinals": (), "physical_part_index": None},
        {"ordinals": None, "physical_part_index": None},
        {"ordinals": (0, 1, 2, 3, 4)},
        {"ordinals": (True,)},
        {"index": True},
        {"physical_part_index": -1},
        {"filename": "private.zip"},
        {"url": "https://private.test"},
    ],
)
def test_rows_have_bounded_actual_addresses_and_no_names_or_prose(updates: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ArchiveMemberDisposition.model_validate({**row().model_dump(), **updates})


@pytest.mark.parametrize("address", ["path", "slot"])
def test_duplicate_same_stage_addresses_are_rejected(address: str) -> None:
    first = row()
    second = first if address == "path" else first.model_copy(update={"ordinals": (1,)})
    with pytest.raises(ValidationError, match="duplicate"):
        section((first, second))


def test_observed_retention_is_separate_from_unvisited_descendants_and_walk_completion() -> None:
    full = section((row(),)).model_dump()
    interrupted = ArchiveMemberDispositions.model_validate(
        {**full, "walk": "interrupted", "unvisited": "known"}
    )
    assert interrupted.retention == "complete" and interrupted.observed_rows == 1
    prefix = ArchiveMemberDispositions.model_validate(
        {
            **full,
            "retention": "prefix",
            "observed_rows": 21,
            "omitted_rows": 20,
            "rows_sha256": "b" * 64,
        }
    )
    assert prefix.rows_sha256 != archive_member_rows_sha256(prefix.rows)
    omitted = ArchiveMemberDispositions.model_validate(
        {
            **full,
            "retention": "omitted",
            "observed_rows": 21,
            "omitted_rows": 21,
            "rows": (),
            "rows_sha256": "b" * 64,
        }
    )
    assert not omitted.rows and omitted.observed_rows == 21
    unknown = ArchiveMemberDispositions(
        walk="unknown",
        retention="unknown",
        observed_rows=None,
        omitted_rows=None,
        rows_sha256=None,
        unvisited="unknown",
    )
    assert not unknown.rows and unknown.observed_rows is None


@pytest.mark.parametrize(
    "fault",
    [
        "count",
        "hash",
        "omitted",
        "unknown_count",
        "unknown_walk",
        "prefix_count",
        "rows_limit",
        "byte_limit",
    ],
)
def test_sections_refuse_false_completeness_unknown_claims_and_size_overflow(fault: str) -> None:
    values = section((row(),)).model_dump()
    if fault == "count":
        values["observed_rows"] = 2
    elif fault == "hash":
        values["rows_sha256"] = "b" * 64
    elif fault == "omitted":
        values["omitted_rows"] = 1
    elif fault.startswith("unknown"):
        values.update(
            walk="unknown",
            retention="unknown",
            observed_rows=None,
            omitted_rows=None,
            rows_sha256=None,
            unvisited="unknown",
            rows=(),
        )
        values["observed_rows" if fault == "unknown_count" else "walk"] = (
            0 if fault == "unknown_count" else "finished"
        )
    elif fault == "prefix_count":
        values.update(retention="prefix", omitted_rows=1)
    else:
        items = tuple(
            row(ordinal=index).model_copy(update={"leaf_sha256": "b" * 64, "text_sha256": "c" * 64})
            for index in range(9 if fault == "rows_limit" else 3)
        )
        values.update(
            rows=items, observed_rows=len(items), rows_sha256=archive_member_rows_sha256(items)
        )
    with pytest.raises(ValidationError):
        ArchiveMemberDispositions.model_validate(values)


@pytest.mark.parametrize(
    "updates", [{"index": 1}, {"phase": "version_comparison"}, {"root_sha256": "b" * 64}]
)
def test_retained_rows_must_match_known_outer_phase_bounds_and_body(
    updates: dict[str, Any],
) -> None:
    changed = row().model_copy(update=updates)
    with pytest.raises(ValidationError):
        enriched(members=section((changed,)))


def test_old_failed_outer_body_ban_survives_addressed_member_failure_root() -> None:
    with pytest.raises(ValidationError):
        CandidateDisposition(
            phase="fallback",
            index=0,
            outcome="read_failure",
            reason="read_error",
            observed_sha256=ROOT,
        )
    member = row(stage="walk").model_copy(
        update={"reason": "member_failed", "physical_part_index": None}
    )
    future = enriched(legacy(failed=True), section((member,)))
    assert future.dispositions[0].observed_sha256 is None
    assert future.archive_members is not None and future.archive_members.rows[0].root_sha256 == ROOT


def test_whole_outer_omission_retains_only_an_inner_summary() -> None:
    outer = legacy(url="https://api.test/" + "x" * 5000)
    assert outer.coverage == "omitted_over_limit" and outer.requested_document is None
    members = ArchiveMemberDispositions(
        walk="interrupted",
        retention="omitted",
        observed_rows=10,
        omitted_rows=10,
        rows_sha256="b" * 64,
        unvisited="known",
    )
    future = enriched(outer, members)
    assert future.requested_document is None and future.archive_members == members
    with pytest.raises(ValidationError):
        enriched(outer, section((row(),)))


def test_archive_section_does_not_relax_whole_trace_budget() -> None:
    original = legacy(url="https://api.test/" + "x" * 3400)
    assert original.coverage == "complete"
    with pytest.raises(ValidationError, match="metadata limit"):
        enriched(original)


def test_public_repository_roundtrip_and_valid_doctor_parity(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        trace = enriched()
        selected = DocumentSelectionRecord.from_selection(DocumentSelection())
        observation = SourceObservation(
            term=10,
            number="archive-owner",
            purpose="analysis",
            document=TextDocument(url="https://api.test/archive.zip", kind="rcl"),
            selection_id=selected.selection_id,
            recorded_at=NOW,
        )
        repo.save_source_observation(selected, observation)
        repo.save_candidate_disposition_trace(observation.observation_id, trace)
        assert repo.get_candidate_disposition_trace(trace.trace_id) == trace
        assert repo.list_candidate_disposition_traces(observation.observation_id) == [trace]
        original = repo.dump()
        repo.restore(original)
        assert (
            repo.dump() == original
            and repo.get_candidate_disposition_trace(trace.trace_id) == trace
        )
        marker = f"PRAGMA user_version = {SCHEMA_VERSION};"
        corrupt = original.replace(
            marker,
            "UPDATE candidate_disposition_traces SET trace_json=json_set(trace_json,'$.archive_members.observed_rows',9);\n"
            + marker,
        )
        with pytest.raises(ValueError):
            repo.restore(corrupt)
        assert repo.dump() == original
    before = path.read_bytes()
    assert audit(path)["status"] == "recorded" and path.read_bytes() == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("root_sha256", "b" * 64),
        ("ordinals", (1,)),
        ("physical_part_index", 1),
        ("leaf_sha256", "b" * 64),
        ("text_sha256", "c" * 64),
    ],
)
def test_known_occurrence_facts_cannot_conflict_across_walk_and_policy(
    field: str, value: object
) -> None:
    walked = row(stage="walk").model_copy(update={"leaf_sha256": "d" * 64, "text_sha256": "e" * 64})
    selected = row().model_copy(
        update={"leaf_sha256": "d" * 64, "text_sha256": "e" * 64, field: value}
    )
    with pytest.raises(ValidationError):
        section((walked, selected))


def test_one_failed_outer_attempt_cannot_claim_distinct_known_roots() -> None:
    first = row(stage="walk")
    second = row(stage="walk", ordinal=1).model_copy(update={"root_sha256": "b" * 64})
    with pytest.raises(ValidationError, match="conflicting known archive roots"):
        enriched(legacy(failed=True), section((first, second)))


def test_failed_outer_read_does_not_accept_policy_completion() -> None:
    with pytest.raises(ValidationError, match="completed member policy"):
        enriched(legacy(failed=True), section((row(),)))


def test_unknown_association_facts_are_not_enriched_from_another_stage() -> None:
    unknown = ArchiveMemberDisposition.model_validate(
        {**row(stage="walk").model_dump(), "root_sha256": None, "ordinals": None}
    )
    known = row()
    members = section((unknown, known))
    assert members.rows[0].root_sha256 is None and members.rows[0].ordinals is None
    assert members.rows[0].association == "unknown" and members.rows[1].association == "known"


def test_root_parse_failure_without_an_occurrence_uses_an_unknown_summary() -> None:
    with pytest.raises(ValidationError, match="actual ordinal path or physical slot"):
        ArchiveMemberDisposition.model_validate(
            {
                "stage": "walk",
                "phase": "fallback",
                "index": 0,
                "root_sha256": ROOT,
                "outcome": "observed",
                "reason": "format_failed",
            }
        )
    unknown = ArchiveMemberDispositions(
        walk="unknown",
        retention="unknown",
        observed_rows=None,
        omitted_rows=None,
        rows_sha256=None,
        unvisited="unknown",
    )
    assert enriched(legacy(failed=True), unknown).archive_members == unknown


def test_public_get_and_list_reject_corrupt_future_archive_metadata(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    trace = enriched()
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        selected = DocumentSelectionRecord.from_selection(DocumentSelection())
        observation = SourceObservation(
            term=10,
            number="archive-owner",
            purpose="analysis",
            document=TextDocument(url="https://api.test/archive.zip", kind="rcl"),
            selection_id=selected.selection_id,
            recorded_at=NOW,
        )
        repo.save_source_observation(selected, observation)
        repo.save_candidate_disposition_trace(observation.observation_id, trace)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "UPDATE candidate_disposition_traces SET trace_json=json_set(trace_json,'$.archive_members.observed_rows',9)"
        )
    with closing(SqliteBillRepository(path)) as repo:
        with pytest.raises(ValueError):
            repo.get_candidate_disposition_trace(trace.trace_id)
        with pytest.raises(ValueError):
            repo.list_candidate_disposition_traces(observation.observation_id)


@pytest.mark.parametrize(
    "outcome,reason",
    [("official_conflict", "official_link_conflict"), ("unattempted", "selection_stopped")],
)
def test_outer_candidates_without_an_actual_attempt_cannot_claim_archive_rows(
    outcome: str, reason: str
) -> None:
    disposition = CandidateDisposition.model_validate(
        {"phase": "fallback", "index": 0, "outcome": outcome, "reason": reason}
    )
    original = CandidateDispositionTrace.from_decisions(
        TextDocument(url="https://api.test/archive.zip", kind="rcl"),
        (disposition,),
        result="fallback",
        reason="all_rejected",
        selection_state="unsupported",
    )
    with pytest.raises(ValidationError, match="actually attempted"):
        enriched(original, section((row(stage="walk"),)))
    with pytest.raises(ValidationError):
        CandidateDisposition.model_validate({**disposition.model_dump(), "observed_sha256": ROOT})
