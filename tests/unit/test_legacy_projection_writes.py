import hashlib
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast

import pytest
from pydantic import JsonValue

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    AmendmentsRecord,
    AnalysisRecord,
    BillStatus,
    DocumentReview,
    DocumentReviewRecord,
    DocumentSelection,
    JointRecord,
    LegacyResultImport,
    LegacyResultOccurrence,
    LegacyResultValue,
    LocatedText,
    ProcessDetail,
    Publication,
    PublicationKind,
    PublicationStatus,
    ReadyAnalysis,
    ReviewContext,
    StatusChange,
    SupplementRecord,
)
from lexinform.models.legacy_results import canonical_legacy_json, legacy_json
from lexinform.models.reviews import ReviewPurpose
from tests.fakes import make_amendments, make_analysis, make_comparison, make_digest

NOW = datetime(2026, 10, 28, 10, tzinfo=UTC)


@dataclass(frozen=True)
class Location:
    column: str
    table: str = "bills"
    member: str | int | None = None


LOCATIONS = (
    Location("analysis_json"),
    Location("ready_analysis_json", member="record"),
    Location("joint_json"),
    Location("consultation_review_json"),
    Location("commission_review_json"),
    Location("act_application_review_json"),
    Location("amendments_json", table="status_changes"),
    Location("supplements_json", table="status_changes", member=0),
    Location("supplements_json", table="status_changes", member=1),
)


def raw_column(path: Path, location: Location) -> str | None:
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            f"SELECT {location.column} FROM {location.table} WHERE term=10 AND number='3039'"
        ).fetchone()
    assert row is not None, "the test seeds one owner row for each projection location"
    raw = row[0]
    assert raw is None or isinstance(raw, str), "the selected physical column stores JSON text"
    return raw


def record_at(value: JsonValue, location: Location) -> dict[str, JsonValue]:
    if isinstance(location.member, str):
        assert isinstance(value, dict), "the ready analysis fixture is an object"
        value = value[location.member]
    elif isinstance(location.member, int):
        assert isinstance(value, list), "the supplements fixture is an array"
        value = value[location.member]
    assert isinstance(value, dict), "the fixture location holds one saved result record"
    return value


def share_analysis_bodies(path: Path, values: tuple[LegacyResultValue, ...]) -> None:
    with sqlite3.connect(path) as connection:
        for value in values:
            envelope = legacy_json(value.value_json)
            assert isinstance(envelope, dict), "LegacyResultValue validates a saved result object"
            if "analysis" not in envelope:
                continue
            body = canonical_legacy_json(envelope.pop("analysis"))
            body_sha = hashlib.sha256(body.encode()).hexdigest()
            connection.execute(
                "INSERT OR IGNORE INTO legacy_analysis_bodies VALUES (?, ?)", (body_sha, body)
            )
            connection.execute(
                "UPDATE legacy_result_values SET envelope_json=?, analysis_body_sha256=?"
                " WHERE value_id=?",
                (canonical_legacy_json(envelope), body_sha, value.value_id),
            )


def save_record(
    repo: SqliteBillRepository, change_id: int, location: Location, encoded: str
) -> None:
    if location.column == "analysis_json":
        repo.save_analysis(10, "3039", AnalysisRecord.model_validate_json(encoded))
    elif location.column == "ready_analysis_json":
        repo.save_ready_analysis(
            10,
            "3039",
            ReadyAnalysis(
                record=AnalysisRecord.model_validate_json(encoded),
                located=LocatedText(),
                text="Replacement source text",
            ),
        )
    elif location.column == "joint_json":
        repo.save_joint_comparison(10, "3039", JointRecord.model_validate_json(encoded))
    elif location.column.endswith("review_json"):
        repo.save_document_review(10, "3039", DocumentReviewRecord.model_validate_json(encoded))
    elif location.column == "amendments_json":
        repo.save_status_change_amendments(change_id, AmendmentsRecord.model_validate_json(encoded))
    else:
        change = repo.get_status_change(change_id)
        assert change is not None and isinstance(location.member, int), (
            "the fixture identifies a supplement"
        )
        change.supplements[location.member] = SupplementRecord.model_validate_json(encoded)
        repo.save_status_change_supplements(change_id, change.supplements)


@dataclass(frozen=True)
class Copies:
    inline: SqliteBillRepository
    projected: SqliteBillRepository
    inline_path: Path
    projected_path: Path
    change_id: int
    values: tuple[LegacyResultValue, ...]

    @property
    def repositories(self) -> tuple[SqliteBillRepository, SqliteBillRepository]:
        return self.inline, self.projected

    def assert_parity(self) -> None:
        assert self.projected.get(10, "3039") == self.inline.get(10, "3039")
        assert self.projected.list_status_changes(10, "3039") == self.inline.list_status_changes(
            10, "3039"
        )
        assert self.projected.get_publication(
            10, "3039", PublicationKind.NEW_BILL, "channel"
        ) == self.inline.get_publication(10, "3039", PublicationKind.NEW_BILL, "channel")
        for repo in self.repositories:
            assert not repo.list_computation_results(10, "3039")
            assert not repo.list_result_applications(10, "3039")
            assert (
                tuple(repo.get_legacy_result(value.value_id) for value in self.values)
                == self.values
            )


def seed_writers(repo: SqliteBillRepository, process: ProcessDetail) -> int:
    repo.upsert_summary(process, now=NOW)
    analysis = AnalysisRecord(
        analysis=make_analysis(),
        model="saved-model",
        prompt_version="saved-prompt",
        input_chars=1200,
        truncated=False,
        text_source="pdf",
        created_at=NOW,
        source_url="https://source.test/bill.pdf",
        input_tokens=200,
        output_tokens=30,
    )
    repo.save_analysis(10, "3039", analysis)
    repo.save_ready_analysis(
        10, "3039", ReadyAnalysis(record=analysis, located=LocatedText(), text="Frozen source text")
    )
    repo.save_joint_comparison(
        10,
        "3039",
        JointRecord(
            comparison=make_comparison(),
            compared_with=["3038"],
            model="saved-model",
            prompt_version="saved-prompt",
            created_at=NOW,
            input_tokens=100,
        ),
    )
    purposes: tuple[ReviewPurpose, ...] = (
        "consultation_review",
        "commission_review",
        "act_application",
    )
    for purpose in purposes:
        repo.save_document_review(
            10,
            "3039",
            DocumentReviewRecord(
                review=DocumentReview(
                    purpose=purpose,
                    complete=False,
                    summary="Saved review answer",
                    consultation_findings=(),
                    effective_rules=(),
                    unresolved=("Known limitation",),
                ),
                context=ReviewContext(
                    number="3039", title=process.title, purpose=purpose, documents=()
                ),
                model="saved-model",
                prompt_version="saved-prompt",
                created_at=NOW,
                input_tokens=90,
            ),
        )
    change_id = repo.add_status_change(
        StatusChange(
            term=10,
            number="3039",
            old_fingerprint="old",
            new_fingerprint="new",
            new_stages=[],
            amendments=AmendmentsRecord(
                amendments=make_amendments(),
                model="saved-model",
                prompt_version="saved-prompt",
                source_url="https://source.test/amendments.pdf",
                source_kind="senate_amendments",
                created_at=NOW,
                input_tokens=80,
            ),
            supplements=[
                SupplementRecord(
                    number=number,
                    title="Stanowisko Rządu",
                    source_kind="print",
                    source_url=f"https://source.test/{number}.pdf",
                    digest=make_digest(),
                    model="saved-model",
                    prompt_version="saved-prompt",
                    created_at=NOW,
                    input_tokens=70,
                )
                for number in ("3039-1", "3039-2")
            ],
            detected_at=NOW,
        )
    )
    assert change_id is not None, "the fixture inserts the first status change"
    repo.create_publication(
        Publication(
            term=10,
            number="3039",
            kind=PublicationKind.NEW_BILL,
            status=PublicationStatus.SENT,
            channel_id="channel",
            message_id=7,
            created_at=NOW,
        )
    )
    return change_id


@pytest.fixture(params=[False, True], ids=["current-schema", "editorial-upcast"])
def copies(
    tmp_path: Path, process_3039: ProcessDetail, request: pytest.FixtureRequest
) -> Iterator[Copies]:
    inline_path, projected_path = tmp_path / "inline.db", tmp_path / "projected.db"
    inline, projected = SqliteBillRepository(inline_path), SqliteBillRepository(projected_path)
    inline.migrate()
    projected.migrate()
    change_id = seed_writers(inline, process_3039)
    assert seed_writers(projected, process_3039) == change_id
    values: dict[str, LegacyResultValue] = {}
    occurrences: list[LegacyResultOccurrence] = []
    for location in LOCATIONS:
        raw = raw_column(inline_path, location)
        assert raw is not None, "seed_writers populated every result column"
        value = legacy_json(raw)
        record = record_at(value, location)
        if request.param is True and "analysis" in record:
            answer = record["analysis"]
            assert isinstance(answer, dict), "the analysis fixture has a structured answer"
            for field in (
                "mnemonic_candidate",
                "mnemonic_fallback",
                "mnemonic_basis",
                "mnemonic_language",
            ):
                del answer[field]
            with sqlite3.connect(inline_path) as connection:
                connection.execute(
                    f"UPDATE {location.table} SET {location.column}=?",
                    (canonical_legacy_json(value),),
                )
        archived = LegacyResultValue(value_json=canonical_legacy_json(record))
        values[archived.value_id] = archived
        occurrences.append(
            LegacyResultOccurrence(
                value_id=archived.value_id,
                source_table=location.table,
                source_key_json='[10,"3039"]' if location.table == "bills" else f"[{change_id}]",
                source_column=location.column,
                source_path="" if location.member is None else f"/{location.member}",
                container_term=10,
                container_number="3039",
            )
        )
    imported = LegacyResultImport(
        imported_at=NOW, values=tuple(values.values()), occurrences=tuple(occurrences)
    )
    for repo in (inline, projected):
        repo.import_legacy_results(imported)
    if request.param is True:
        share_analysis_bodies(projected_path, imported.values)
    with sqlite3.connect(projected_path) as connection:
        for location, occurrence in zip(LOCATIONS, occurrences, strict=True):
            raw = connection.execute(f"SELECT {location.column} FROM {location.table}").fetchone()[
                0
            ]
            value = legacy_json(raw)
            marker: dict[str, JsonValue] = {"legacy_result_ref": occurrence.value_id}
            if isinstance(location.member, str):
                assert isinstance(value, dict), "the ready fixture is a JSON object"
                value[location.member] = marker
            elif isinstance(location.member, int):
                assert isinstance(value, list), "the supplements fixture is a JSON array"
                value[location.member] = marker
            else:
                value = marker
            connection.execute(
                f"UPDATE {location.table} SET {location.column}=?", (canonical_legacy_json(value),)
            )
    result = Copies(inline, projected, inline_path, projected_path, change_id, imported.values)
    result.assert_parity()
    yield result
    inline.close()
    projected.close()


@pytest.mark.parametrize("location", LOCATIONS[:2], ids=lambda location: location.column)
def test_analysis_and_ready_writers_keep_bounded_quiet_selection_overrides(
    copies: Copies, location: Location
) -> None:
    for day in range(100):
        for repo in copies.repositories:
            bill = repo.get(10, "3039")
            assert bill is not None and bill.analysis is not None, (
                "the fixture has a saved analysis"
            )
            record = bill.analysis.model_copy(
                update={
                    "source_checked_at": NOW + timedelta(days=day),
                    "source_url": f"https://source.test/current-{day}.pdf",
                    "selection": DocumentSelection(policy_version="selection-v2"),
                    "input_tokens": 0,
                    "output_tokens": 0,
                }
            )
            if location.column == "analysis_json":
                repo.save_analysis(10, "3039", record)
            else:
                repo.save_ready_analysis(
                    10,
                    "3039",
                    ReadyAnalysis(record=record, located=LocatedText(), text="Frozen source text"),
                )
        copies.assert_parity()
    raw = raw_column(copies.projected_path, location)
    assert raw is not None, "the writer retains the analysis or ready result"
    stored = record_at(legacy_json(raw), location)
    assert stored["legacy_result_ref"] in {value.value_id for value in copies.values}
    assert "analysis" not in stored
    assert len(canonical_legacy_json(stored)) < 1400
    overrides = stored["projection_overrides"]
    assert isinstance(overrides, dict), "quiet metadata differs from the immutable original"
    assert overrides["input_tokens"] == 0
    assert overrides["source_checked_at"] == (NOW + timedelta(days=99)).isoformat().replace(
        "+00:00", "Z"
    )
    inline_raw = raw_column(copies.inline_path, location)
    assert inline_raw is not None and "legacy_result_ref" not in record_at(
        legacy_json(inline_raw), location
    )


def test_analysis_replacement_clears_ready_without_resurrecting_its_old_ref(copies: Copies) -> None:
    for repo in copies.repositories:
        bill = repo.get(10, "3039")
        assert bill is not None and bill.analysis is not None, "the fixture has an analysis"
        record = bill.analysis.model_copy(update={"source_checked_at": NOW})
        repo.save_analysis(10, "3039", record)
        repo.save_ready_analysis(10, "3039", ReadyAnalysis(record=record, located=LocatedText()))
    copies.assert_parity()
    raw = raw_column(copies.projected_path, LOCATIONS[1])
    assert raw is not None and "legacy_result_ref" not in raw


@pytest.mark.parametrize("status", [BillStatus.BATCH_PENDING, BillStatus.REANALYSIS_READY])
def test_equal_analysis_preserves_pending_generation_ready_and_editorial(
    copies: Copies, status: BillStatus
) -> None:
    for repo in copies.repositories:
        repo.set_status(10, "3039", status)
        before = repo.get(10, "3039")
        assert before is not None and before.analysis is not None, "the fixture has an analysis"
        repo.save_analysis(10, "3039", before.analysis)
        assert repo.get(10, "3039") == before
    copies.assert_parity()
    raw = raw_column(copies.projected_path, LOCATIONS[0])
    assert raw is not None and "legacy_result_ref" in raw


def test_secondary_writers_preserve_all_existing_answer_refs(copies: Copies) -> None:
    for repo in copies.repositories:
        bill = repo.get(10, "3039")
        assert bill is not None and bill.joint is not None, "the fixture has a joint comparison"
        repo.save_joint_comparison(10, "3039", bill.joint.model_copy(update={"input_tokens": 0}))
        for review in (
            bill.consultation_review,
            bill.commission_review,
            bill.act_application_review,
        ):
            assert review is not None, "the fixture has each review purpose"
            repo.save_document_review(10, "3039", review.model_copy(update={"input_tokens": 0}))
        change = repo.get_status_change(copies.change_id)
        assert change is not None and change.amendments is not None, "the fixture has amendments"
        repo.save_status_change_amendments(
            copies.change_id,
            change.amendments.model_copy(
                update={"source_url": "https://source.test/relocated.pdf", "input_tokens": 0}
            ),
        )
        repo.save_status_change_supplements(
            copies.change_id,
            [record.model_copy(update={"input_tokens": 0}) for record in change.supplements],
        )
    copies.assert_parity()
    for location in LOCATIONS[2:]:
        raw = raw_column(copies.projected_path, location)
        assert raw is not None, "the secondary writer retains its result"
        stored = record_at(legacy_json(raw), location)
        assert "legacy_result_ref" in stored
        assert stored["projection_overrides"] == {
            "input_tokens": 0,
            **(
                {"source_url": "https://source.test/relocated.pdf"}
                if location.column == "amendments_json"
                else {}
            ),
        }


@pytest.mark.parametrize("location", LOCATIONS, ids=lambda location: str(location))
@pytest.mark.parametrize("replacement", ["answer", "modern"])
def test_changed_answers_and_modern_refs_take_inline_writer_path(
    copies: Copies, location: Location, replacement: str
) -> None:
    raw = raw_column(copies.inline_path, location)
    assert raw is not None, "the fixture seeds every result location"
    record = record_at(legacy_json(raw), location)
    if replacement == "modern":
        record["computation_id"] = "result-v1:" + "a" * 64
        record["request_sha256"] = "b" * 64
    else:
        answer_field = next(
            field
            for field in ("analysis", "comparison", "review", "amendments", "digest")
            if field in record
        )
        answer = record[answer_field]
        assert isinstance(answer, dict), "the fixture result has a structured answer"
        answer["summary"] = "A newly validated answer"
        if answer_field == "analysis":
            answer["mnemonic_candidate"] = "Новый срок ВНЖ"
            answer["mnemonic_basis"] = "The new text changes the residence deadline."
    encoded = canonical_legacy_json(record)
    for repo in copies.repositories:
        save_record(repo, copies.change_id, location, encoded)
    copies.assert_parity()
    saved = raw_column(copies.projected_path, location)
    assert saved is not None, "the replacement writer saves an inline result"
    current = record_at(legacy_json(saved), location)
    assert "legacy_result_ref" not in current
    assert current.get("computation_id") == record.get("computation_id")


@pytest.mark.parametrize("location", LOCATIONS, ids=lambda location: str(location))
def test_writers_reject_corrupt_previous_refs_without_overwriting_them(
    copies: Copies, location: Location
) -> None:
    inline = raw_column(copies.inline_path, location)
    physical = raw_column(copies.projected_path, location)
    assert inline is not None and physical is not None, "the fixture seeds both result copies"
    encoded = canonical_legacy_json(record_at(legacy_json(inline), location))
    value = legacy_json(physical)
    marker = record_at(value, location)
    marker["legacy_result_ref"] = "legacy-value-v1:" + "f" * 64
    damaged = canonical_legacy_json(value)
    with sqlite3.connect(copies.projected_path) as connection:
        connection.execute(f"UPDATE {location.table} SET {location.column}=?", (damaged,))

    with pytest.raises(ValueError, match="missing"):
        if location.column == "supplements_json":
            change = copies.inline.get_status_change(copies.change_id)
            assert change is not None, "the uncompacted status change supplies the writer input"
            copies.projected.save_status_change_supplements(copies.change_id, change.supplements)
        else:
            save_record(copies.projected, copies.change_id, location, encoded)

    if location.column == "supplements_json":
        with pytest.raises(ValueError, match="missing"):
            copies.projected.save_status_change_supplements(copies.change_id, [])

    assert raw_column(copies.projected_path, location) == damaged
    assert (
        tuple(copies.projected.get_legacy_result(value.value_id) for value in copies.values)
        == copies.values
    )


def test_supplement_append_reorder_and_clear_keep_existing_semantics(copies: Copies) -> None:
    for repo in copies.repositories:
        change = repo.get_status_change(copies.change_id)
        assert change is not None, "the fixture seeds a status change"
        repo.save_status_change_supplements(
            copies.change_id,
            [
                *change.supplements,
                change.supplements[0].model_copy(update={"number": "3039-3"}),
            ],
        )
    copies.assert_parity()
    raw = raw_column(copies.projected_path, LOCATIONS[-1])
    assert raw is not None, "the status change has three supplements"
    records = legacy_json(raw)
    assert isinstance(records, list), "supplements are stored as an array"
    assert all("legacy_result_ref" in record for record in records[:2] if isinstance(record, dict))
    assert isinstance(records[2], dict) and "legacy_result_ref" not in records[2]
    for repo in copies.repositories:
        change = repo.get_status_change(copies.change_id)
        assert change is not None, "the saved status change remains available"
        repo.save_status_change_supplements(copies.change_id, list(reversed(change.supplements)))
    copies.assert_parity()
    for repo in copies.repositories:
        repo.save_status_change_supplements(copies.change_id, [])
    copies.assert_parity()
    assert raw_column(copies.projected_path, LOCATIONS[-1]) is None


@pytest.mark.parametrize("limit", [-2, -1, 0, 1, 2, 3, 4, 5, 20])
def test_projected_cost_ranking_matches_sql_null_zero_tie_and_limit_behavior(
    tmp_path: Path, process_3039: ProcessDetail, limit: int
) -> None:
    inline_path, projected_path = tmp_path / "cost-inline.db", tmp_path / "cost-projected.db"
    inline, projected = SqliteBillRepository(inline_path), SqliteBillRepository(projected_path)
    values: dict[str, LegacyResultValue] = {}
    occurrences: list[LegacyResultOccurrence] = []
    replacements: list[tuple[str, int, str]] = []
    cases: tuple[
        tuple[
            int,
            str,
            int | None,
            int | None,
            Literal["base", "inline", "override", "removed", "absent"],
        ],
        ...,
    ] = (
        (10, "1", 900, 900, "base"),
        (9, "20", 800, 800, "base"),
        (10, "10", 800, 800, "base"),
        (10, "2", 800, 800, "inline"),
        (10, "3", 900, 0, "override"),
        (10, "4", None, 400, "override"),
        (10, "5", 900, None, "override"),
        (10, "6", 900, None, "removed"),
        (10, "7", None, None, "base"),
        (10, "8", 0, 0, "base"),
        (10, "9", None, None, "absent"),
    )
    for repo in (inline, projected):
        repo.migrate()
        repo.upsert_summary(process_3039, now=NOW)
    for term, number, base_tokens, current_tokens, kind in cases:
        process = process_3039.model_copy(update={"term": term, "number": number})
        record = AnalysisRecord(
            analysis=make_analysis(),
            model="saved-model",
            prompt_version="saved-prompt",
            input_chars=1200,
            truncated=False,
            text_source="pdf",
            created_at=NOW,
            input_tokens=current_tokens,
        )
        for repo in (inline, projected):
            repo.upsert_summary(process, now=NOW)
            repo.save_analysis(term, number, record)
        if kind == "inline":
            continue
        base = cast(dict[str, JsonValue], record.model_dump(mode="json"))
        base["input_tokens"] = base_tokens
        if kind == "absent":
            del base["input_tokens"]
        value = LegacyResultValue(value_json=canonical_legacy_json(base))
        values[value.value_id] = value
        occurrences.append(
            LegacyResultOccurrence(
                value_id=value.value_id,
                source_table="bills",
                source_key_json=canonical_legacy_json([term, number]),
                source_column="analysis_json",
                source_path="",
                container_term=term,
                container_number=number,
            )
        )
        marker: dict[str, JsonValue] = {"legacy_result_ref": value.value_id}
        if kind == "override":
            marker["projection_overrides"] = {"input_tokens": current_tokens}
        elif kind == "removed":
            marker["projection_removed"] = ["input_tokens"]
        replacements.append((canonical_legacy_json(marker), term, number))
    imported = LegacyResultImport(
        imported_at=NOW, values=tuple(values.values()), occurrences=tuple(occurrences)
    )
    for repo in (inline, projected):
        repo.import_legacy_results(imported)
    share_analysis_bodies(projected_path, imported.values)
    with sqlite3.connect(projected_path) as connection:
        connection.executemany(
            "UPDATE bills SET analysis_json=? WHERE term=? AND number=?", replacements
        )
    with sqlite3.connect(inline_path) as connection:
        expected = connection.execute(
            "SELECT term, number FROM bills WHERE json_extract(analysis_json, '$.input_tokens') IS NOT NULL"
            " ORDER BY json_extract(analysis_json, '$.input_tokens') DESC, term, number LIMIT ?",
            (limit,),
        ).fetchall()
    ordered = [(10, "1"), (9, "20"), (10, "10"), (10, "2"), (10, "4"), (10, "3"), (10, "8")]
    assert expected == (ordered if limit < 0 else ordered[:limit])
    assert [
        (bill.term, bill.number) for bill in projected.most_expensive_analyses(limit=limit)
    ] == expected
    assert projected.most_expensive_analyses(limit=limit) == inline.most_expensive_analyses(
        limit=limit
    )
    inline.close()
    projected.close()
