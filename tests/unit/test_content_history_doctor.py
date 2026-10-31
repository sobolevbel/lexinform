import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from tools.content_history_doctor import audit, run

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import ContentSnapshot
from tests.scenario.test_amendments_command import published_world


def database(
    tmp_path: Path, *, revised: bool = False, unknown: bool = False, confirmed: bool = False
) -> tuple[Path, int, int | None]:
    world, original = published_world()
    assert original.id is not None, "published_world stores the frozen original"
    revision_id: int | None = None
    if revised:
        world.command("/amendments 1929")
        world.run(track=False)
        world.batch.resolve()
        if unknown:
            world.publisher.unknown_on_edit.add("1929")
        world.run()
        revision_id = world.repo.list_amendment_revisions(original.id)[0].id
        if confirmed:
            world.command(f"/delivery {revision_id} confirm {original.message_id}")
            world.run(track=False)
    path = tmp_path / "snapshot.db"
    repo = SqliteBillRepository(path)
    repo.migrate()
    repo.restore(world.repo.dump())
    repo.close()
    return path, original.id, revision_id


@pytest.mark.parametrize("unknown", [False, True])
def test_current_edition_requires_a_confirmed_physical_delivery(
    tmp_path: Path, unknown: bool
) -> None:
    path, root, revised = database(tmp_path, revised=True, unknown=unknown)
    assert revised is not None, "the fixture collects a separate amendment revision"
    before = path.read_bytes()

    result = audit(path, publication_id=revised)

    assert result["status"] == "recorded" and not result["findings"]
    history = result["histories"][0]
    assert history["root_publication_id"] == root
    editions = history["versions"]
    assert len(editions) == 2
    assert history["current_content_id"] == editions[0 if unknown else 1]["content_id"]
    assert editions[1]["publications"][0]["status"] == ("unknown" if unknown else "sent")
    assert path.read_bytes() == before
    assert "https://" not in json.dumps(result)
    assert "Исправленный разбор" not in json.dumps(result)


@pytest.mark.parametrize(
    ("statement", "code"),
    [
        ("UPDATE content_snapshots SET payload_json='bad'", "invalid_content_snapshot"),
        ("UPDATE content_versions SET version_json='bad'", "invalid_content_version"),
        ("UPDATE content_versions SET root_publication_id=999", "content_version_columns_mismatch"),
        ("DELETE FROM content_snapshots", "content_snapshot_unavailable"),
        ("UPDATE publication_content SET content_id='absent'", "content_version_unavailable"),
        ("DROP TABLE content_versions", "content_history_table_unavailable"),
    ],
)
def test_damaged_history_is_reported_without_a_guessed_current_version(
    tmp_path: Path, statement: str, code: str
) -> None:
    path, _, _ = database(tmp_path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(statement)
    before = path.read_bytes()

    result = audit(path)

    assert result["status"] == "failed"
    assert code in {row["code"] for row in result["findings"]}
    assert all(history["current_content_id"] is None for history in result["histories"])
    assert path.read_bytes() == before


@pytest.mark.parametrize("schema", [54, 55])
def test_legacy_absence_is_distinct_from_damage_to_the_current_schema(
    tmp_path: Path, schema: int
) -> None:
    path = tmp_path / "legacy.db"
    with closing(sqlite3.connect(path)) as db:
        db.execute(f"PRAGMA user_version={schema}")

    result = audit(path)

    assert result["status"] == ("not_run" if schema == 54 else "failed")
    assert result["summary"]["errors"] == (0 if schema == 54 else 3)


def test_known_empty_history_is_distinct_from_missing_legacy_tables(tmp_path: Path) -> None:
    path = tmp_path / "empty.db"
    repo = SqliteBillRepository(path)
    repo.migrate()
    repo.close()

    result = audit(path)

    assert result["status"] == "not_run" and not result["missing_tables"]
    assert all(
        result["summary"][key] == 0 for key in ("snapshot_rows", "version_rows", "binding_rows")
    )


def test_reports_are_immutable_separate_artifacts(tmp_path: Path) -> None:
    path, _, _ = database(tmp_path)
    with pytest.raises(ValueError, match="separate"):
        run(path, path)
    output = tmp_path / "report.json"
    assert run(path, output) == 0
    before = output.read_bytes()
    with pytest.raises(FileExistsError):
        run(path, output)
    assert output.read_bytes() == before


def test_unknown_opaque_refs_are_preserved_in_sql_and_omitted_from_the_report(
    tmp_path: Path,
) -> None:
    path, root, _ = database(tmp_path)
    repo = SqliteBillRepository(path)
    original = repo.get_publication_content(root)
    assert original is not None, "the fixture records the original editorial version"
    unknown = original.model_copy(
        update={
            "source_scope": None,
            "source_observation_ids": ("https://source.test/NEVER-EXPORT",),
            "computation_ids": ("OPAQUE-DO-NOT-REWRITE",),
            "formatter_version": "https://source.test/NEVER-EXPORT",
        }
    )
    repo.save_content_version(unknown)
    repo.close()
    before = path.read_bytes()

    result = audit(path)

    assert result["status"] == "recorded" and result["summary"]["warnings"] >= 1
    assert "NEVER-EXPORT" not in json.dumps(result)
    assert "OPAQUE-DO-NOT-REWRITE" not in json.dumps(result)
    assert path.read_bytes() == before


@pytest.mark.parametrize("part", ["bill", "change", "plan", "rendered"])
def test_consistent_but_wrong_content_cannot_become_the_confirmed_current_version(
    tmp_path: Path, part: str
) -> None:
    path, root, _ = database(tmp_path)
    repo = SqliteBillRepository(path)
    original = repo.get_publication_content(root)
    assert original is not None, "the fixture records the original content binding"
    if part == "rendered":
        changed = original.model_copy(update={"rendered_text": "ALTERED-HISTORY"})
    else:
        identity = {
            "bill": original.bill_snapshot_id,
            "change": original.change_snapshot_id,
            "plan": original.plan_snapshot_id,
        }[part]
        snapshot = repo.get_content_snapshot(identity)
        assert snapshot is not None, "the version has all immutable semantic snapshots"
        fields = json.loads(snapshot.payload_json)
        if part == "plan":
            fields["today"] = "1900-01-01"
        elif part == "change":
            fields["new_fingerprint"] = "ALTERED-HISTORY"
        else:
            fields["summary"]["title"] = "ALTERED-HISTORY"
        wrong = ContentSnapshot(kind=snapshot.kind, payload_json=json.dumps(fields))
        repo.save_content_snapshot(wrong)
        changed = original.model_copy(update={part + "_snapshot_id": wrong.snapshot_id})
    repo.save_content_version(changed)
    repo.close()
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "UPDATE publication_content SET content_id=? WHERE publication_id=?",
            (changed.content_id, root),
        )

    result = audit(path)

    assert result["status"] == "failed"
    assert any("frozen" in row["code"] for row in result["findings"])
    assert result["histories"][0]["current_content_id"] is None


@pytest.mark.parametrize("identity", [0, -1])
def test_invalid_publication_identity_is_rejected_before_reading_a_database(
    tmp_path: Path, identity: int
) -> None:
    with pytest.raises(ValueError, match="positive"):
        audit(tmp_path / "missing.db", publication_id=identity)


def test_operator_confirmation_is_distinct_from_a_prepared_unknown_edit(tmp_path: Path) -> None:
    path, _, revision = database(tmp_path, revised=True, unknown=True, confirmed=True)
    assert revision is not None, "the fixture records and confirms its uncertain edit"

    result = audit(path, publication_id=revision)

    assert result["status"] == "recorded" and not result["findings"]
    history = result["histories"][0]
    current = next(
        version
        for version in history["versions"]
        if version["content_id"] == history["current_content_id"]
    )
    physical = current["publications"][0]
    assert physical["publication_id"] == revision and physical["status"] == "sent"
    assert physical["operator_decisions"][0]["action"] == "confirm"


@pytest.mark.parametrize("column,value", [("publication_id", 999), ("command_update_id", 999)])
def test_operator_json_cannot_confirm_a_different_sql_identity(
    tmp_path: Path, column: str, value: int
) -> None:
    path, _, _ = database(tmp_path, revised=True, unknown=True, confirmed=True)
    with closing(sqlite3.connect(path)) as db, db:
        if column == "publication_id":
            raw = db.execute("SELECT resolution_json FROM delivery_resolutions").fetchone()[0]
            data = json.loads(raw)
            data[column] = value
            db.execute("UPDATE delivery_resolutions SET resolution_json=?", (json.dumps(data),))
        else:
            db.execute("UPDATE delivery_resolutions SET command_update_id=?", (value,))

    result = audit(path)

    assert result["status"] == "failed"
    assert "content_operator_columns_mismatch" in {row["code"] for row in result["findings"]}
    assert result["histories"][0]["current_content_id"] is None


def test_deleted_original_keeps_history_without_guessing_the_current_message(
    tmp_path: Path,
) -> None:
    path, root, _ = database(tmp_path, revised=True)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DELETE FROM publications WHERE id=?", (root,))
    before = path.read_bytes()

    result = audit(path)

    assert result["status"] == "recorded" and not result["summary"]["errors"]
    assert result["summary"]["warnings"] == 1
    assert len(result["histories"][0]["versions"]) == 2
    assert result["histories"][0]["current_content_id"] is None
    assert path.read_bytes() == before
    repo = SqliteBillRepository(path)
    versions = repo.list_content_versions(root)
    assert len(versions) == 2
    assert all(repo.get_content_version(version.content_id) == version for version in versions)
    repo.close()


@pytest.mark.parametrize("column,value", [("number", "OTHER"), ("kind", "hearing_deadline")])
def test_known_wrong_physical_owner_still_invalidates_scoped_history(
    tmp_path: Path, column: str, value: str
) -> None:
    path, root, _ = database(tmp_path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(f"UPDATE publications SET {column}=? WHERE id=?", (value, root))
    before = path.read_bytes()

    result = audit(path)

    assert result["status"] == "failed"
    assert "content_source_reference_invalid" in {row["code"] for row in result["findings"]}
    assert path.read_bytes() == before
