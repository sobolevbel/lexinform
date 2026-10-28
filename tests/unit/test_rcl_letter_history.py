import datetime as dt
import json
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

import pytest
from tools.source_history_doctor import audit

from lexinform.adapters.sqlite_repo import MIGRATIONS, SCHEMA_VERSION, SqliteBillRepository
from lexinform.concurrency import fan_out
from lexinform.models import (
    ArtifactIdentity,
    ArtifactReadIdentity,
    RclLetterUse,
    RclLetterUseObservation,
)
from lexinform.models.rcl_letter_use import RclLetterPhysical, RclLetterUseOmission
from lexinform.services.rcl_letter_history import RclLetterUseCollector, record_rcl_letter_uses
from tests.unit.test_physical_extraction import docx, extractor
from tests.unit.test_rcl_letter_use import NOW, TEXT, URL, letter_payload


def repository(tmp_path: Path) -> SqliteBillRepository:
    repo = SqliteBillRepository(tmp_path / "history.db")
    repo.migrate()
    return repo


def known_observation(*, omitted: bool = False, exact: bool = True) -> RclLetterUseObservation:
    body = docx(TEXT)
    observed = extractor().extract_document_with_provenance(body)
    result = observed.result
    artifact = ArtifactIdentity(sha256=result.artifact_sha256, byte_size=len(body))
    read = ArtifactReadIdentity(**artifact.model_dump(), requested_url=URL)
    payload = letter_payload(name="ą" * 300 if omitted else "Pismo.pdf")
    assert payload.input is not None, "The parsed-letter fixture supplies its exact input metadata"
    input = payload.input.model_copy(
        update={
            "sha256": result.output_text_sha256 if exact else "c" * 64,
            "chars": len(observed.document.text),
            "utf8_bytes": len(observed.document.text.encode()),
        }
    )
    physical = RclLetterPhysical(
        state="known_complete_body",
        artifact_sha256=artifact.sha256,
        artifact_bytes=artifact.byte_size,
        artifact_read_id=read.read_id,
        extraction_id=result.result_id,
        input_extraction_relation="exact_output_text_sha256" if exact else "unknown",
    )
    use = RclLetterUse.of(payload.model_copy(update={"physical": physical, "input": input}))
    return RclLetterUseObservation(use=use, artifact=artifact, read=read, extraction=result)


@pytest.mark.parametrize("known", [False, True])
@pytest.mark.parametrize("omitted", [False, True])
def test_first_time_repeat_dump_restore_and_parent_history_survive_retention(
    tmp_path: Path,
    known: bool,
    omitted: bool,
) -> None:
    repo = repository(tmp_path)
    observation = (
        known_observation(omitted=omitted)
        if known
        else RclLetterUseObservation(
            use=RclLetterUse.of(
                letter_payload(name="ą" * 300 if omitted else "Pismo.pdf", stage_id=None)
            )
        )
    )
    with repo.atomic():
        record_rcl_letter_uses(repo, (observation,), recorded_at=NOW)
    before = repo.dump()
    with repo.atomic():
        record_rcl_letter_uses(repo, (observation,), recorded_at=NOW + dt.timedelta(days=365))
        repo.prune_runs(before=NOW + dt.timedelta(days=366))
        repo.prune_history(
            before=NOW + dt.timedelta(days=366), memo_before=NOW + dt.timedelta(days=366)
        )
    record = repo.get_rcl_letter_use(observation.use.use_id)
    assert record is not None and record.recorded_at == NOW, (
        "The caller transaction persists the first history fact"
    )
    assert repo.dump() == before
    assert list(repo.iter_rcl_letter_uses(12402153)) == [record]
    assert list(repo.iter_rcl_letter_uses(12402154)) == []
    restored = SqliteBillRepository(tmp_path / "restored.db")
    restored.restore(before)
    assert restored.get_rcl_letter_use(record.use_id) == record
    assert restored.dump() == before
    assert restored.schema_version == SCHEMA_VERSION
    repo.close()
    report = audit(tmp_path / "history.db")
    assert report["status"] == "recorded" and not report["findings"]
    assert report["summary"]["rcl_letter_use_rows"] == 1
    assert report["summary"]["rcl_letter_use_" + record.payload_state] == 1
    assert URL not in json.dumps(report) and TEXT not in json.dumps(report)
    restored.close()
    repo.close()


@pytest.mark.parametrize("exact", [False, True])
def test_known_extraction_relation_is_honest_and_rejects_missing_or_wrong_parent(
    tmp_path: Path,
    exact: bool,
) -> None:
    repo = repository(tmp_path)
    observation = known_observation(exact=exact)
    with pytest.raises(ValueError, match="complete metadata"):
        repo.save_rcl_letter_use(observation.use, recorded_at=NOW)
    with repo.atomic():
        record_rcl_letter_uses(repo, (observation,), recorded_at=NOW)
    saved = repo.get_rcl_letter_use(observation.use.use_id)
    assert saved is not None, "The caller transaction persists the supplied known-origin fact"
    assert saved.payload.physical.input_extraction_relation == (
        "exact_output_text_sha256" if exact else "unknown"
    )
    assert (
        observation.artifact is not None
        and observation.read is not None
        and observation.extraction is not None
    ), "The known-origin fixture supplies each exact physical parent"
    with pytest.raises(ValueError, match="outer body"):
        observation.use.verify_parents(
            observation.artifact,
            observation.read,
            observation.extraction.model_copy(update={"artifact_sha256": "d" * 64}),
        )
    with pytest.raises(ValueError, match="read parent"):
        observation.use.verify_parents(
            observation.artifact,
            observation.read.model_copy(update={"requested_url": URL + "alias"}),
            observation.extraction,
        )
    repo.close()


def test_caller_transaction_rolls_back_parent_and_use_on_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = repository(tmp_path)
    observation = known_observation()
    before = repo.dump()

    def fail(use: RclLetterUse, *, recorded_at: dt.datetime) -> None:
        raise sqlite3.OperationalError("injected use persistence failure")

    monkeypatch.setattr(repo, "save_rcl_letter_use", fail)
    with pytest.raises(sqlite3.OperationalError), repo.atomic():
        record_rcl_letter_uses(repo, (observation,), recorded_at=NOW)
    assert repo.dump() == before
    repo.close()


@pytest.mark.parametrize("workers", [1, 4])
def test_collector_is_clock_free_deduplicated_and_writer_stays_on_caller(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    workers: int,
) -> None:
    repo = repository(tmp_path)
    collector = RclLetterUseCollector()
    observations = [
        RclLetterUseObservation(use=RclLetterUse.of(letter_payload(stage_id=stage)))
        for stage in (None, 13156437)
    ]

    def observe(observation: RclLetterUseObservation) -> None:
        collector.observe_rcl_letter_use(observation)

    assert all(
        outcome.error is None for outcome in fan_out(observations * 3, observe, workers=workers)
    )
    snapshot = collector.read_rcl_letter_uses()
    assert [item.use.use_id for item in snapshot] == sorted(
        item.use.use_id for item in observations
    )
    assert list(repo.iter_rcl_letter_uses(12402153)) == []
    saved_threads: list[int] = []
    save = repo.save_rcl_letter_use

    def record(use: RclLetterUse, *, recorded_at: dt.datetime) -> None:
        saved_threads.append(threading.get_ident())
        save(use, recorded_at=recorded_at)

    monkeypatch.setattr(repo, "save_rcl_letter_use", record)
    with repo.atomic():
        record_rcl_letter_uses(repo, snapshot, recorded_at=NOW)
    assert saved_threads == [threading.get_ident()] * 2
    assert collector.read_rcl_letter_uses() == snapshot
    repo.close()


@pytest.mark.parametrize(
    ("statement", "finding"),
    [
        ("UPDATE rcl_letter_uses SET stage_id=3", "rcl_letter_use_columns_mismatch"),
        ("UPDATE rcl_letter_uses SET record_json='bad'", "invalid_rcl_letter_use"),
        ("DELETE FROM artifacts", "rcl_letter_use_parent_mismatch"),
        ("UPDATE extraction_results SET artifact_sha256='bad'", "rcl_letter_use_parent_mismatch"),
    ],
)
def test_corrupt_metadata_is_rejected_by_reader_and_read_only_doctor(
    tmp_path: Path,
    statement: str,
    finding: str,
) -> None:
    repo = repository(tmp_path)
    observation = known_observation()
    with repo.atomic():
        record_rcl_letter_uses(repo, (observation,), recorded_at=NOW)
    valid_dump = repo.dump()
    with closing(sqlite3.connect(tmp_path / "history.db")) as db, db:
        db.execute(statement)
    before = (tmp_path / "history.db").read_bytes()
    with pytest.raises(ValueError):
        repo.get_rcl_letter_use(observation.use.use_id)
    corrupt_dump = repo.dump()
    repo.close()
    target = SqliteBillRepository(tmp_path / "restore-target.db")
    target.restore(valid_dump)
    with pytest.raises(ValueError):
        target.restore(corrupt_dump)
    assert target.dump() == valid_dump
    target.close()
    before = (tmp_path / "history.db").read_bytes()
    report = audit(tmp_path / "history.db")
    assert report["status"] == "failed"
    assert finding in {item["code"] for item in report["findings"]}
    assert (tmp_path / "history.db").read_bytes() == before
    repo.close()


@pytest.mark.parametrize("schema", [59, 60])
def test_absent_current_table_and_legacy_unavailable_are_distinct(
    tmp_path: Path, schema: int
) -> None:
    repo = repository(tmp_path)
    repo.close()
    with closing(sqlite3.connect(tmp_path / "history.db")) as db, db:
        db.execute("DROP TABLE rcl_letter_uses")
        db.execute(f"PRAGMA user_version={schema}")
    report = audit(tmp_path / "history.db")
    assert report["status"] == ("not_run" if schema == 59 else "failed")
    assert not report["rcl_letter_uses_available"]


@pytest.mark.parametrize(("state", "chars"), [("complete", 2049), ("omitted_over_limit", 1025)])
def test_migration_is_one_append_only_step_and_sql_counts_utf8_bytes(
    tmp_path: Path,
    state: str,
    chars: int,
) -> None:
    repo = repository(tmp_path)
    repo.close()
    assert len(MIGRATIONS) == SCHEMA_VERSION
    with closing(sqlite3.connect(tmp_path / "history.db")) as db, db:
        columns = (
            "rcl-letter-use-v1:" + "a" * 64,
            12402153,
            None,
            3,
            737331,
            state,
            None,
            None,
            None,
            "a" * 64,
            NOW.isoformat(),
            "ą" * chars,
        )
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            db.execute("INSERT INTO rcl_letter_uses VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", columns)


def test_empty_table_is_available_without_claiming_any_parsed_history(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    repo.close()
    report = audit(tmp_path / "history.db")
    assert report["status"] == "not_run" and report["rcl_letter_uses_available"]
    assert not report["findings"]


def test_opaque_omission_cannot_replace_a_first_record_or_collector_fact(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    observation = known_observation(omitted=True)
    assert isinstance(observation.use.payload, RclLetterUseOmission), (
        "The fixture omits its oversized descriptor"
    )
    changed = observation.use.model_copy(
        update={"payload": observation.use.payload.model_copy(update={"output_sha256": "f" * 64})}
    )
    with repo.atomic():
        record_rcl_letter_uses(repo, (observation,), recorded_at=NOW)
    before = repo.dump()
    with pytest.raises(ValueError, match="immutable"):
        repo.save_rcl_letter_use(changed, recorded_at=NOW + dt.timedelta(days=1))
    assert repo.dump() == before
    collector = RclLetterUseCollector()
    collector.observe_rcl_letter_use(observation)
    with pytest.raises(ValueError, match="immutable"):
        collector.observe_rcl_letter_use(observation.model_copy(update={"use": changed}))
    assert collector.read_rcl_letter_uses() == (observation,)
    repo.close()
