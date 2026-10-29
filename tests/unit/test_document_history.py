import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    DocumentPart,
    DocumentSelection,
    DocumentSelectionRecord,
    SourceObservation,
    SourceObservationIdentity,
    TextDocument,
)

NOW = dt.datetime(2026, 10, 27, tzinfo=dt.UTC)
URL = "https://source.test/draft.pdf"
BODY = "EXTRACTED-BODY-IS-NOT-PERSISTED"


def decision(*, sha: str = "a" * 64) -> DocumentSelectionRecord:
    return DocumentSelectionRecord.from_selection(
        DocumentSelection(
            policy_version="document-selection-v1",
            parts=(DocumentPart(url=URL, sha256=sha, text=BODY),),
            observed_parts=(DocumentPart(url=URL, sha256=sha, text=BODY),),
        )
    )


def observation(record: DocumentSelectionRecord) -> SourceObservation:
    return SourceObservation(
        term=10,
        number="3039",
        purpose="prefilter",
        document=TextDocument(url=URL, kind="print", extra_urls=(URL + ".osr",)),
        selection_id=record.selection_id,
        recorded_at=NOW,
    )


def repository(tmp_path: Path) -> SqliteBillRepository:
    repo = SqliteBillRepository(tmp_path / "state.db")
    repo.migrate()
    return repo


def test_source_history_preserves_catalog_relations_without_bodies_or_paid_identity() -> None:
    record = decision()
    source = observation(record)

    assert DocumentSelectionRecord.model_validate_json(record.model_dump_json()) == record
    assert SourceObservation.model_validate_json(source.model_dump_json()) == source
    assert BODY not in record.model_dump_json() + source.model_dump_json()
    assert source.document.extra_urls == (URL + ".osr",)
    assert record.selection.state == "unverified"
    assert "computation_id" not in source.model_dump_json()
    assert "retained" not in source.model_dump_json()


def test_clock_free_source_identity_preserves_the_existing_record_bytes_and_id() -> None:
    source = observation(decision())
    values = source.model_dump(mode="json")
    identity_values = {key: value for key, value in values.items() if key != "recorded_at"}
    identity = SourceObservationIdentity.model_validate(identity_values)
    expected = (
        "source-observation-v1:"
        + hashlib.sha256(
            json.dumps(
                identity_values, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            ).encode()
        ).hexdigest()
    )
    assert identity.observation_id == source.observation_id == expected
    assert tuple(values) == (
        "schema_version",
        "term",
        "number",
        "purpose",
        "document",
        "selection_id",
        "recorded_at",
    )
    assert (
        SourceObservation(**identity.model_dump(), recorded_at=NOW).model_dump_json()
        == source.model_dump_json()
    )
    with pytest.raises(ValidationError):
        SourceObservationIdentity.model_validate(values)


def test_selection_record_rejects_bodies_even_after_unvalidated_copy() -> None:
    original = DocumentSelection(parts=(DocumentPart(text=BODY),))

    with pytest.raises(ValidationError, match="not extracted bodies"):
        DocumentSelectionRecord(selection=original)


def test_legacy_policy_remains_unknown_and_future_policy_stays_opaque() -> None:
    original = DocumentSelectionRecord.from_selection(DocumentSelection())
    future = DocumentSelectionRecord.from_selection(
        DocumentSelection(policy_version="future-policy-v7")
    )

    assert original.selection.policy_version is None
    assert "policy_version" not in original.model_dump_json()
    assert future.selection.policy_version == "future-policy-v7"
    assert future.selection_id != original.selection_id


def test_first_recording_time_and_dump_remain_stable_on_repeated_check(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    record = decision()
    source = observation(record)
    repo.save_source_observation(record, source)
    first = repo.dump()

    repo.save_source_observation(
        record, source.model_copy(update={"recorded_at": NOW + dt.timedelta(days=30)})
    )

    assert repo.dump() == first
    assert repo.get_source_observation(source.observation_id) == source
    assert repo.get_document_selection(record.selection_id) == record


def test_same_selection_keeps_multiple_owner_and_catalog_observations(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    record = decision()
    first = observation(record)
    other = first.model_copy(update={"number": "3040"})
    moved = first.model_copy(
        update={"document": first.document.model_copy(update={"url": URL + "?new=1"})}
    )
    for source in (first, other, moved):
        repo.save_source_observation(record, source)

    assert repo.list_source_observations(10, "3039") == [first, moved]
    assert repo.list_source_observations(10, "3040") == [other]
    with sqlite3.connect(tmp_path / "state.db") as db:
        assert db.execute("SELECT count(*) FROM document_selections").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM source_observations").fetchone()[0] == 3


def test_new_bytes_at_the_same_url_keep_the_previous_decision(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    first, second = decision(), decision(sha="b" * 64)
    repo.save_source_observation(first, observation(first))
    repo.save_source_observation(second, observation(second))

    assert first.selection_id != second.selection_id
    assert repo.get_document_selection(first.selection_id) == first
    assert repo.get_document_selection(second.selection_id) == second
    assert len(repo.list_source_observations(10, "3039")) == 2


def test_mismatched_selection_and_invalid_model_copy_write_nothing(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    record = decision()
    before = repo.dump()

    with pytest.raises(ValueError, match="exact selection"):
        repo.save_source_observation(record, observation(decision(sha="b" * 64)))
    with pytest.raises(ValidationError, match="timezone"):
        repo.save_source_observation(
            record,
            observation(record).model_copy(update={"recorded_at": dt.datetime(2026, 10, 27)}),
        )

    assert repo.dump() == before


def test_caller_transaction_rolls_back_both_metadata_tables(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    record = decision()
    before = repo.dump()

    with pytest.raises(RuntimeError, match="caller checkpoint"), repo.atomic():
        repo.save_source_observation(record, observation(record))
        raise RuntimeError("caller checkpoint failed")

    assert repo.dump() == before


@pytest.mark.parametrize("table", ["document_selections", "source_observations"])
def test_sql_identity_corruption_is_reported_instead_of_read_as_another_decision(
    tmp_path: Path, table: str
) -> None:
    repo = repository(tmp_path)
    record = decision()
    source = observation(record)
    repo.save_source_observation(record, source)
    with sqlite3.connect(tmp_path / "state.db") as db:
        if table == "document_selections":
            db.execute("UPDATE document_selections SET question='another-question'")
        else:
            db.execute("UPDATE source_observations SET number='another-owner'")

    with pytest.raises(ValueError, match="columns differ"):
        if table == "document_selections":
            repo.get_document_selection(record.selection_id)
        else:
            repo.get_source_observation(source.observation_id)


def test_source_history_survives_sql_restore_without_legacy_backfill(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    record = decision()
    source = observation(record)
    repo.save_source_observation(record, source)
    original = repo.dump()

    repo.restore(original)

    assert repo.dump() == original
    assert repo.get_source_observation(source.observation_id) == source
    assert repo.get_document_selection(record.selection_id) == record
    assert not repo.list_source_observations(10, "never-observed")
    assert BODY not in original
    assert (
        json.loads(record.model_dump_json())["selection"]["policy_version"]
        == "document-selection-v1"
    )
