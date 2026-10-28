import gzip
import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.legacy_results import (
    hydrate_legacy_value,
    legacy_projection,
    materialize_legacy_value,
    read_legacy_json,
)
from lexinform.models import (
    AnalysisRecord,
    LegacyResultImport,
    LegacyResultOccurrence,
    LegacyResultValue,
    ProcessDetail,
)
from lexinform.models.legacy_results import canonical_legacy_json, legacy_json
from tests.fakes import FakeLlm

NOW = datetime(2026, 10, 28, 10, tzinfo=UTC)


def saved_value(**fields: JsonValue) -> LegacyResultValue:
    value: dict[str, JsonValue] = {
        "model": "saved-model",
        "prompt_version": "saved-prompt",
        "created_at": "2026-01-01T12:00:00Z",
        "analysis": {"summary": "Exact saved answer", "unknown": False},
        "unknown_metadata": {"empty": [], "zero": 0},
    }
    value.update(fields)
    return LegacyResultValue(value_json=canonical_legacy_json(value))


def imported(value: LegacyResultValue) -> LegacyResultImport:
    return LegacyResultImport(
        imported_at=NOW,
        source_sha256="a" * 64,
        values=(value,),
        occurrences=(
            LegacyResultOccurrence(
                value_id=value.value_id,
                source_table="bills",
                source_key_json='[10,"3039"]',
                source_column="analysis_json",
                source_path="",
                container_term=10,
                container_number="3039",
            ),
        ),
    )


def decoded(value: LegacyResultValue) -> dict[str, JsonValue]:
    result = legacy_json(value.value_json)
    assert isinstance(result, dict), "LegacyResultValue validates a saved JSON object"
    return result


def read_value(value: LegacyResultValue, identifier: str) -> LegacyResultValue | None:
    return value if value.value_id == identifier else None


def test_exact_value_preserves_unknown_fields_nulls_and_placeholders() -> None:
    absent = saved_value()
    null = saved_value(input_tokens=None)
    placeholder = saved_value(analysis=None)

    assert absent.value_id != null.value_id
    assert decoded(absent)["unknown_metadata"] == {"empty": [], "zero": 0}
    assert decoded(placeholder)["analysis"] is None
    assert "provider" not in decoded(absent)


@pytest.mark.parametrize(
    "raw",
    [
        '{"model":"m","model":"other","prompt_version":"p","created_at":null,"analysis":{}}',
        '{"model":"m","prompt_version":"p","created_at":null,"analysis":{"n":NaN}}',
        '{"model":"m","prompt_version":"p","created_at":null,"analysis":{"n":1e999}}',
        "{}",
    ],
)
def test_invalid_exact_values_fail_closed(raw: str) -> None:
    with pytest.raises(ValueError):
        LegacyResultValue(value_json=raw)


def test_modern_refs_and_nested_projection_markers_are_not_legacy_values() -> None:
    with pytest.raises(ValueError, match="computation"):
        saved_value(computation_id="opaque-known-ref")
    with pytest.raises(ValueError, match="projection"):
        saved_value(unknown_metadata={"legacy_result_ref": "opaque"})


def test_shared_body_materializes_exact_original_without_an_archive_upcast() -> None:
    value = saved_value(source_checked_at=None)
    envelope = decoded(value)
    body = canonical_legacy_json(envelope.pop("analysis"))
    body_sha = hashlib.sha256(body.encode()).hexdigest()
    restored = materialize_legacy_value(
        canonical_legacy_json(envelope), body_sha256=body_sha, body_json=body
    )

    assert restored.value_json == value.value_json and restored.value_id == value.value_id
    assert "mnemonic_candidate" not in body
    assert materialize_legacy_value(saved_value(analysis=None).value_json) == saved_value(
        analysis=None
    )
    with pytest.raises(ValueError, match="missing"):
        materialize_legacy_value(canonical_legacy_json(envelope), body_sha256=body_sha)
    with pytest.raises(ValueError, match="checksum"):
        materialize_legacy_value(
            canonical_legacy_json(envelope), body_sha256="a" * 64, body_json=body
        )
    with pytest.raises(ValueError, match="omit"):
        materialize_legacy_value(value.value_json, body_sha256=body_sha, body_json=body)


@pytest.mark.parametrize("body", ['{"n":0,"n":1}', "[]", '{"legacy_result_ref":"bad"}'])
def test_corrupt_shared_bodies_are_not_exposed_as_original_values(body: str) -> None:
    envelope = decoded(saved_value())
    del envelope["analysis"]
    with pytest.raises(ValueError):
        materialize_legacy_value(
            canonical_legacy_json(envelope),
            body_sha256=hashlib.sha256(body.encode()).hexdigest(),
            body_json=body,
        )


def test_repository_reads_optional_shared_layout_and_rejects_corruption_on_restore(
    tmp_path: Path,
) -> None:
    path = tmp_path / "shared.db"
    repo = SqliteBillRepository(path)
    repo.migrate()
    value = saved_value()
    request = imported(value)
    repo.import_legacy_results(request)
    envelope = decoded(value)
    body = canonical_legacy_json(envelope.pop("analysis"))
    body_sha = hashlib.sha256(body.encode()).hexdigest()
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO legacy_analysis_bodies VALUES (?, ?)", (body_sha, body))
        connection.execute(
            "UPDATE legacy_result_values SET envelope_json = ?, analysis_body_sha256 = ? WHERE value_id = ?",
            (canonical_legacy_json(envelope), body_sha, value.value_id),
        )
    assert repo.get_legacy_result(value.value_id) == value
    assert not repo.import_legacy_results(request)
    assert repo.list_legacy_result_occurrences(10, "3039") == list(request.occurrences)
    restored = SqliteBillRepository(":memory:")
    restored.restore(repo.dump())
    assert restored.get_legacy_result(value.value_id) == value
    before = restored.dump()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE legacy_analysis_bodies SET body_json = '{}' WHERE body_sha256 = ?", (body_sha,)
        )
    with pytest.raises(ValueError, match="checksum"):
        repo.get_legacy_result(value.value_id)
    with pytest.raises(ValueError, match="checksum"):
        repo.import_legacy_results(request)
    with pytest.raises(ValueError, match="checksum"):
        restored.restore(repo.dump())
    assert restored.dump() == before


def test_projection_keeps_original_and_allows_exact_metadata_removals() -> None:
    value = saved_value(source_url="old", source_checked_at=None)

    def reader(identifier: str) -> LegacyResultValue | None:
        return read_value(value, identifier)

    current = legacy_json(value.value_json)
    assert isinstance(current, dict), "saved_value creates a saved JSON object"
    current["source_url"] = "new"
    del current["source_checked_at"]
    projection = legacy_projection(current, value.value_id, reader)

    assert projection is not None
    assert projection["projection_removed"] == ["source_checked_at"]
    assert hydrate_legacy_value(projection, reader) == current
    assert decoded(value)["source_url"] == "old"
    assert decoded(value)["source_checked_at"] is None


@pytest.mark.parametrize(
    "field", ["analysis", "model", "prompt_version", "created_at", "computation_id"]
)
def test_changed_answers_and_origin_fields_take_the_ordinary_path(field: str) -> None:
    value = saved_value()
    current = legacy_json(value.value_json)
    assert isinstance(current, dict), "saved_value creates a saved JSON object"
    current[field] = "changed"

    assert (
        legacy_projection(current, value.value_id, lambda identifier: read_value(value, identifier))
        is None
    )


def test_modern_records_do_not_read_or_assign_a_legacy_identity() -> None:
    def no_read(identifier: str) -> LegacyResultValue | None:
        raise AssertionError("a known modern result must skip legacy lookup")

    modern = decoded(saved_value())
    modern["computation_id"] = "result-v1:" + "a" * 64
    assert legacy_projection(modern, "unused-legacy-identity", no_read) is None


def test_versioned_current_schema_upcast_roundtrips_real_analysis_serialization() -> None:
    analysis = AnalysisRecord(
        analysis=FakeLlm().default,
        model="saved-model",
        prompt_version="saved-prompt",
        created_at=NOW,
        input_chars=1234,
        truncated=False,
        text_source="pdf",
    )
    raw = cast(dict[str, JsonValue], analysis.model_dump(mode="json"))
    answer = raw["analysis"]
    assert isinstance(answer, dict), "AnalysisRecord serializes its answer as an object"
    for field in ("mnemonic_candidate", "mnemonic_fallback", "mnemonic_basis", "mnemonic_language"):
        del answer[field]
    value = LegacyResultValue(value_json=canonical_legacy_json(raw))
    typed = AnalysisRecord.model_validate_json(value.value_json)
    typed.source_checked_at = NOW + timedelta(days=1)
    current = cast(JsonValue, typed.model_dump(mode="json"))
    projection = legacy_projection(
        current, value.value_id, lambda identifier: read_value(value, identifier)
    )

    assert projection is not None
    assert projection["projection_upcast"] == "analysis-editorial-v1"
    assert (
        hydrate_legacy_value(projection, lambda identifier: read_value(value, identifier))
        == current
    )
    assert decoded(value)["analysis"] == answer
    assert "mnemonic_candidate" not in answer


def test_upcast_preserves_present_values_and_rejects_unknown_versions() -> None:
    value = saved_value(
        analysis={"summary": "saved", "mnemonic_language": None, "mnemonic_basis": "known"}
    )

    def reader(identifier: str) -> LegacyResultValue | None:
        return read_value(value, identifier)

    marker: dict[str, JsonValue] = {
        "legacy_result_ref": value.value_id,
        "projection_upcast": "analysis-editorial-v1",
    }
    current = hydrate_legacy_value(marker, reader)
    assert isinstance(current, dict), "a legacy projection materializes a saved record object"
    answer = current["analysis"]
    assert isinstance(answer, dict), "the editorial upcast requires an analysis object"
    assert answer["mnemonic_language"] is None and answer["mnemonic_basis"] == "known"
    assert answer["mnemonic_candidate"] == "" and answer["mnemonic_fallback"] == ""
    marker["projection_upcast"] = "future"
    with pytest.raises(ValueError, match="unknown"):
        hydrate_legacy_value(marker, reader)


@pytest.mark.parametrize(
    "marker",
    [
        {"legacy_result_ref": "bad"},
        {"projection_overrides": {}},
        {"legacy_result_ref": "legacy-value-v1:" + "a" * 64, "unexpected": True},
        {"legacy_result_ref": "legacy-value-v1:" + "a" * 64},
    ],
)
def test_corrupt_or_missing_projection_references_fail_closed(marker: dict[str, JsonValue]) -> None:
    with pytest.raises(ValueError):
        hydrate_legacy_value(marker, lambda identifier: None)


@pytest.mark.parametrize(
    "fields",
    [
        {"projection_overrides": {"analysis": {}}},
        {"projection_overrides": {"selection": {"legacy_result_ref": "opaque"}}},
        {"projection_removed": ["analysis"]},
        {"projection_removed": ["source_url", "source_url"]},
        {"projection_removed": ["source_url"], "projection_overrides": {"source_url": None}},
    ],
)
def test_corrupt_overrides_cannot_change_an_answer_or_hide_markers(
    fields: dict[str, JsonValue],
) -> None:
    value = saved_value()
    marker: dict[str, JsonValue] = {"legacy_result_ref": value.value_id, **fields}

    with pytest.raises(ValueError):
        hydrate_legacy_value(marker, lambda identifier: read_value(value, identifier))


def test_ordinary_json_and_encoded_snapshot_strings_are_unchanged() -> None:
    ordinary = '{ "model": "first", "model": "last" }'
    encoded = '{"bill_json":"{ \\"legacy_result_ref\\": \\"opaque\\" }"}'

    assert read_legacy_json(ordinary, lambda identifier: None) == ordinary
    assert read_legacy_json(encoded, lambda identifier: None) == encoded
    with pytest.raises(ValueError, match="duplicate"):
        read_legacy_json(
            '{"legacy_result_ref":"first","legacy_result_ref":"last"}', lambda identifier: None
        )
    with pytest.raises(ValueError, match="missing"):
        read_legacy_json(
            '{"\\u006cegacy_result_ref":"legacy-value-v1:' + "a" * 64 + '"}',
            lambda identifier: None,
        )
    with pytest.raises(ValueError, match="duplicate"):
        read_legacy_json(
            '{"holder":{"\\u006cegacy_result_ref":"bad"},"holder":{}}',
            lambda identifier: None,
        )


def test_finite_import_is_explicit_once_only_and_has_no_paid_side_effects(
    repo: SqliteBillRepository,
) -> None:
    value = saved_value()
    before = repo.dump()
    repo.migrate()
    assert repo.dump() == before

    assert repo.import_legacy_results(imported(value))
    first = repo.dump()
    repeated = imported(value).model_copy(update={"imported_at": NOW + timedelta(days=1)})
    assert not repo.import_legacy_results(repeated)
    assert repo.dump() == first
    assert repo.get_legacy_result(value.value_id) == value
    assert repo.list_legacy_result_occurrences(10, "3039") == list(imported(value).occurrences)
    assert not repo.list_computation_results(10, "3039")
    assert not repo.list_result_applications(10, "3039")
    with pytest.raises(ValueError, match="different manifest"):
        repo.import_legacy_results(imported(saved_value(analysis={"summary": "different"})))
    assert repo.dump() == first


def test_import_joins_the_callers_transaction(repo: SqliteBillRepository) -> None:
    value = saved_value()
    before = repo.dump()
    with pytest.raises(RuntimeError), repo.atomic():
        repo.import_legacy_results(imported(value))
        raise RuntimeError("crash before canonical checkpoint")
    assert repo.dump() == before


def test_import_manifest_requires_actual_locations_and_explicit_owner_pairs() -> None:
    with pytest.raises(ValueError, match="at least one observed value"):
        LegacyResultImport(imported_at=NOW, values=(), occurrences=())
    value = saved_value()
    with pytest.raises(ValueError, match="location"):
        LegacyResultImport(imported_at=NOW, values=(value,), occurrences=())
    other = saved_value(analysis={"summary": "a different saved answer"})
    same_location = imported(value).occurrences[0]
    with pytest.raises(ValueError, match="deduplicate its occurrences"):
        LegacyResultImport(
            imported_at=NOW,
            values=(value, other),
            occurrences=(
                same_location,
                same_location.model_copy(update={"value_id": other.value_id}),
            ),
        )
    with pytest.raises(ValueError, match="both"):
        LegacyResultOccurrence(
            value_id=value.value_id,
            source_table="analysis_memo",
            source_key_json='["opaque/key"]',
            source_column="record_json",
            source_path="",
            container_term=10,
        )


@pytest.mark.parametrize("damage", ["orphan_occurrence", "missing_value", "wrong_manifest"])
def test_restore_rejects_tampered_legacy_import_before_replacing_state(
    repo: SqliteBillRepository, damage: str
) -> None:
    value = saved_value()
    request = imported(value)
    repo.import_legacy_results(request)
    script = repo.dump()
    if damage == "orphan_occurrence":
        script = script.replace(
            f"'{value.value_id}',1,'bills'",
            f"'legacy-value-v1:{'f' * 64}',1,'bills'",
        )
    elif damage == "missing_value":
        script = (
            "\n".join(
                line
                for line in script.splitlines()
                if not line.startswith('INSERT INTO "legacy_result_values"')
            )
            + "\n"
        )
    else:
        script = script.replace(request.manifest_sha256, "f" * 64)
    assert script != repo.dump()
    before = repo.dump()

    with pytest.raises(ValueError):
        repo.restore(script)

    assert repo.dump() == before


def test_same_manifest_reimport_rejects_corrupt_existing_rows(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    repo = SqliteBillRepository(path)
    repo.migrate()
    value = saved_value()
    request = imported(value)
    assert repo.import_legacy_results(request)
    assert not repo.import_legacy_results(request)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE legacy_result_occurrences SET value_id = ?",
            ("legacy-value-v1:" + "f" * 64,),
        )

    with pytest.raises(ValueError, match="broken reference"):
        repo.import_legacy_results(request)


def test_bridge_reader_materializes_bill_memo_and_restored_dump(
    tmp_path: Path, process_3039: ProcessDetail
) -> None:
    path = tmp_path / "bridge.db"
    repo = SqliteBillRepository(path)
    repo.migrate()
    repo.upsert_summary(process_3039, now=NOW)
    analysis = AnalysisRecord(
        analysis=FakeLlm().default,
        model="saved-model",
        prompt_version="saved-prompt",
        created_at=NOW,
        input_chars=1234,
        truncated=False,
        text_source="pdf",
    )
    repo.save_analysis(10, "3039", analysis)
    value = LegacyResultValue(
        value_json=canonical_legacy_json(cast(JsonValue, analysis.model_dump(mode="json")))
    )
    repo.import_legacy_results(imported(value))
    marker = json.dumps({"legacy_result_ref": value.value_id})
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE bills SET analysis_json = ? WHERE term = 10 AND number = '3039'", (marker,)
        )
        connection.execute(
            "INSERT INTO analysis_memo(key, record_json) VALUES ('key', ?)", (marker,)
        )
    bill = repo.get(10, "3039")
    assert bill is not None and bill.analysis == analysis
    assert AnalysisRecord.model_validate_json(repo.load_analysis_memo()["key"]) == analysis
    dump = repo.dump()
    assert marker in dump
    restored = SqliteBillRepository(":memory:")
    restored.restore(dump)
    restored_bill = restored.get(10, "3039")
    assert restored_bill is not None and restored_bill.analysis == analysis
    assert restored.get_legacy_result(value.value_id) == value
    assert not restored.import_legacy_results(imported(value))


def test_restore_rejects_missing_history_before_replacing_existing_state(
    repo: SqliteBillRepository,
) -> None:
    before = repo.dump()
    bad = before.replace(
        "COMMIT;",
        "INSERT INTO analysis_memo(key, record_json) VALUES ('bad', '{\"legacy_result_ref\":\"legacy-value-v1:"
        + "a" * 64
        + "\"}');\nCOMMIT;",
    )
    with pytest.raises(ValueError, match="missing"):
        repo.restore(bad)
    assert repo.dump() == before


def test_quiet_and_selection_updates_keep_one_body_and_bounded_current_delta(
    repo: SqliteBillRepository,
) -> None:
    value = saved_value(analysis={"summary": "unique answer " * 4000}, selection={"old": True})
    repo.import_legacy_results(imported(value))
    initial = legacy_json(value.value_json)
    assert isinstance(initial, dict), "saved_value creates a saved JSON object"
    first = legacy_projection(initial, value.value_id, repo.get_legacy_result)
    assert first is not None
    before = repo.dump()
    current = dict(initial)
    for day in range(100):
        current["source_checked_at"] = (NOW + timedelta(days=day)).isoformat()
        current["selection"] = {"known": True, "origin": "exact-recorded-source"}
        projection = legacy_projection(current, value.value_id, repo.get_legacy_result)
        assert projection is not None
        assert hydrate_legacy_value(projection, repo.get_legacy_result) == current
        assert len(canonical_legacy_json(projection)) < 400
        assert "unique answer" not in canonical_legacy_json(projection)
    assert repo.dump() == before
    assert len(repo.list_legacy_result_occurrences(10, "3039")) == 1
    assert repo.get_legacy_result(value.value_id) == value
    assert len(gzip.compress(canonical_legacy_json(projection).encode(), mtime=0)) < 300
