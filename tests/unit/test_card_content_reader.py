import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from tools.content_history_doctor import audit

from lexinform.adapters.content_history import validate_content_history
from lexinform.adapters.sqlite_repo import MIGRATIONS, SCHEMA_VERSION, SqliteBillRepository
from lexinform.models import (
    ContentSnapshot,
    ContentVersion,
    DeliveryPlan,
    ProcessDetail,
    Publication,
    PublicationKind,
    PublicationStatus,
)
from lexinform.models.content import card_bill_snapshot
from lexinform.services.content_history import prepare_publication_content, restore_content_delivery
from lexinform.services.content_inspection import inspect_content
from lexinform.services.message_parts import continuation_snapshot
from tests.fakes import FixedClock
from tests.formatting import NOW, bill_of
from tests.unit.test_content_history import record, version


def physical(
    repo: SqliteBillRepository,
    process: ProcessDetail,
    *,
    root: int | None = None,
    status: PublicationStatus = PublicationStatus.SENT,
) -> Publication:
    bill = bill_of(process)
    plan = DeliveryPlan(
        bill_json=bill.model_dump_json(),
        rendered_text="original" if root is None else "edited",
        today=NOW.date(),
        revision_of=root,
        edit_message_id=10 if root else None,
        continuation_parent=root,
        continuation_index=1,
        reply_to=10 if root else None,
    )
    identity = repo.create_publication(
        Publication(
            term=10,
            number=process.number,
            channel_id="channel",
            kind=PublicationKind.MESSAGE_PART if root else PublicationKind.NEW_BILL,
            status=status,
            message_id=10 if status is PublicationStatus.SENT else None,
            created_at=NOW,
            ref=f"card/{root}/{'a' * 64}-{root}/1" if root else None,
            delivery=plan,
        )
    )
    found = repo.publication_by_id(identity)
    assert found is not None, "the fixture saved its physical operation"
    return found


def retain(
    repo: SqliteBillRepository, pub: Publication, *, previous: ContentVersion | None = None
) -> ContentVersion:
    assert pub.id is not None and pub.delivery is not None, (
        "the fixture supplies a saved frozen operation"
    )
    bill = card_bill_snapshot(pub.delivery.bill_json)
    plan = ContentSnapshot(
        kind="plan",
        payload_json=pub.delivery.model_copy(
            update={"bill_json": "", "change_json": "", "rendered_text": ""}
        ).model_dump_json(),
    )
    for snapshot in (bill, plan):
        repo.save_content_snapshot(snapshot)
    value = ContentVersion(
        root_publication_id=previous.root_publication_id if previous else pub.id,
        previous_version_id=previous.content_id if previous else None,
        reason="card_revision" if previous else "original",
        language="ru",
        formatter_version="telegram-format-v2",
        bill_snapshot_id=bill.snapshot_id,
        change_snapshot_id=None,
        card_context_profile="continuation-v1",
        plan_snapshot_id=plan.snapshot_id,
        rendered_text=pub.delivery.rendered_text,
        recorded_at=NOW,
    )
    repo.save_content_version(value)
    repo.bind_publication_content(pub.id, value.content_id)
    return value


def test_nullable_change_is_honest_card_absence_and_not_a_fake_event(
    tmp_path: Path, process_3039: ProcessDetail
) -> None:
    path = tmp_path / "card.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        pub = physical(repo, process_3039)
        value = retain(repo, pub)
        assert (
            value.change_snapshot_id is None and "change_snapshot_id" not in value.model_dump_json()
        )
        view = inspect_content(repo, pub)
        assert view is not None and view.current_id == value.content_id
        before = repo.dump()
        repo.restore(before)
        assert repo.dump() == before
    report = audit(path)
    assert (
        report["status"] == "recorded"
        and report["histories"][0]["current_content_id"] == value.content_id
    )


@pytest.mark.parametrize(
    "state", [PublicationStatus.SENT, PublicationStatus.UNKNOWN, PublicationStatus.DISMISSED]
)
@pytest.mark.parametrize("bound", [False, True])
def test_latest_card_edit_requires_its_own_confirmed_known_version(
    tmp_path: Path, process_3039: ProcessDetail, state: PublicationStatus, bound: bool
) -> None:
    path = tmp_path / "card.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        root = physical(repo, process_3039)
        original = retain(repo, root)
        edit = physical(repo, process_3039, root=root.id, status=state)
        latest = retain(repo, edit, previous=original) if bound else None
        history = inspect_content(repo, root)
        assert history is not None
        expected = (
            latest.content_id if latest is not None and state is PublicationStatus.SENT else None
        )
        assert history.current_id == expected
    assert audit(path)["histories"][0]["current_content_id"] == expected


def test_card_scope_rejects_change_event_and_wrong_physical_kind(
    tmp_path: Path, process_3039: ProcessDetail
) -> None:
    with closing(SqliteBillRepository(tmp_path / "card.db")) as repo:
        repo.migrate()
        pub = physical(repo, process_3039)
        value = retain(repo, pub)
        with pytest.raises(ValueError):
            repo.save_content_version(value.model_copy(update={"reason": "amendments_revision"}))
        with pytest.raises(ValueError):
            repo.save_content_version(value.model_copy(update={"status_change_id": 1}))
        with closing(sqlite3.connect(tmp_path / "card.db")) as db:
            db.execute("UPDATE publications SET kind='status_update'")
            db.commit()
        with pytest.raises(ValueError):
            repo.get_content_version(value.content_id)


def test_migration_preserves_populated_legacy_ids_json_bindings_and_foreign_keys(
    tmp_path: Path,
) -> None:
    path = tmp_path / "old.db"
    with closing(sqlite3.connect(path)) as db:
        for n, script in enumerate(MIGRATIONS[:-1], start=1):
            db.executescript(f"BEGIN;{script}PRAGMA user_version={n};COMMIT;")
    with closing(SqliteBillRepository(path)) as repo:
        first = version()
        record(repo, first)
        second = version(previous=first.content_id, text="revision")
        record(repo, second)
        with closing(sqlite3.connect(path)) as db:
            db.execute(
                "UPDATE content_versions SET rowid=7 WHERE content_id=?", (second.content_id,)
            )
            db.execute("INSERT INTO publication_content VALUES(1,?)", (first.content_id,))
            db.commit()
            before = {
                table: db.execute(f"SELECT rowid,* FROM {table}").fetchall()
                for table in ("content_snapshots", "content_versions", "publication_content")
            }
        repo.migrate()
        assert repo.schema_version == SCHEMA_VERSION
        with closing(sqlite3.connect(path)) as db:
            after = {
                table: db.execute(f"SELECT rowid,* FROM {table}").fetchall() for table in before
            }
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []
            assert (
                next(
                    row
                    for row in db.execute("PRAGMA table_info(content_versions)")
                    if row[1] == "change_snapshot_id"
                )[3]
                == 0
            )
        assert before == after
        assert (
            repo.get_content_version(first.content_id) == first
            and repo.get_content_version(second.content_id) == second
        )


@pytest.mark.parametrize("shape", ["alias", "malformed_ref", "extra_part", "digest"])
def test_unbound_or_unsupported_physical_evidence_never_certifies_original_current(
    tmp_path: Path,
    process_3039: ProcessDetail,
    shape: str,
) -> None:
    path = tmp_path / "card.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        root = physical(repo, process_3039)
        value = retain(repo, root)
        assert root.id is not None, "the fixture saved its root"
        if shape == "alias":
            repo.create_publication(
                Publication(
                    term=10,
                    number="other",
                    channel_id="channel",
                    kind=PublicationKind.NEW_BILL,
                    status=PublicationStatus.SENT,
                    message_id=10,
                    created_at=NOW,
                )
            )
        else:
            plan = DeliveryPlan(
                bill_json=root.delivery.bill_json if root.delivery else "",
                rendered_text="untracked",
                revision_of=root.id,
                edit_message_id=10,
                continuation_parent=root.id,
                reply_to=10,
                destination="digest_only" if shape == "digest" else "direct",
            )
            repo.create_publication(
                Publication(
                    term=10,
                    number=process_3039.number,
                    channel_id="channel",
                    kind=PublicationKind.MESSAGE_PART,
                    status=PublicationStatus.SENT,
                    message_id=10,
                    created_at=NOW,
                    ref=f"card/{root.id}/invalid/2" if shape == "extra_part" else "malformed-ref",
                    delivery=plan,
                )
            )
        history = inspect_content(repo, root)
        assert (
            history is not None
            and history.original_id == value.content_id
            and history.current_id is None
        )
        saved = repo.dump()
        repo.restore(saved)
        assert repo.dump() == saved
    assert audit(path)["histories"][0]["current_content_id"] is None


def test_cleaned_card_bindings_keep_history_and_restore_without_payloads(
    tmp_path: Path, process_3039: ProcessDetail
) -> None:
    path = tmp_path / "card.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        root = physical(repo, process_3039)
        original = retain(repo, root)
        edit = physical(repo, process_3039, root=root.id)
        current = retain(repo, edit, previous=original)
    with closing(sqlite3.connect(path)) as db:
        db.execute("UPDATE publications SET delivery_json=NULL")
        db.commit()
    with closing(SqliteBillRepository(path)) as repo:
        fresh = repo.publication_by_id(root.id or 0)
        assert fresh is not None, "retention preserves the physical identity"
        history = inspect_content(repo, fresh)
        assert history is not None and history.current_id == current.content_id
        script = repo.dump()
        repo.restore(script)
        assert repo.dump() == script


@pytest.mark.parametrize("fault", ["snapshot", "version_json", "columns", "duplicate", "deep"])
def test_corrupt_card_evidence_fails_atomic_restore_with_bounded_doctor(
    tmp_path: Path, process_3039: ProcessDetail, fault: str
) -> None:
    path = tmp_path / "card.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        root = physical(repo, process_3039)
        value = retain(repo, root)
        valid = repo.dump()
    with closing(sqlite3.connect(path)) as db:
        if fault == "snapshot":
            db.execute("DELETE FROM content_snapshots WHERE kind='bill'")
        elif fault == "columns":
            db.execute("UPDATE content_versions SET plan_snapshot_id='missing'")
        elif fault == "version_json":
            db.execute("UPDATE content_versions SET version_json='bad'")
        elif fault == "duplicate":
            raw = value.model_dump_json().replace(
                '"reason":"original"', '"reason":"wrong","reason":"original"'
            )
            db.execute("UPDATE content_versions SET version_json=?", (raw,))
        else:
            db.execute(
                "UPDATE content_versions SET version_json=?", ("[" * 2000 + "0" + "]" * 2000,)
            )
        db.commit()
    with closing(SqliteBillRepository(path)) as repo:
        broken = repo.dump()
        repo.restore(valid)
        with pytest.raises(ValueError, match="invalid_content_history"):
            repo.restore(broken)
        assert repo.dump() == valid
    diagnostic = tmp_path / "broken.db"
    with closing(sqlite3.connect(diagnostic)) as db:
        db.executescript(broken)
    report = audit(diagnostic)
    assert report["status"] == "failed" and "original" not in str(report["findings"])


def test_card_validator_preserves_caller_row_factory(
    tmp_path: Path, process_3039: ProcessDetail
) -> None:
    path = tmp_path / "card.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        retain(repo, physical(repo, process_3039))
    with closing(sqlite3.connect(path)) as db:
        validate_content_history(db)
        assert db.row_factory is None and isinstance(db.execute("SELECT 1").fetchone(), tuple)


def test_government_rehome_preserves_frozen_owner_and_restorable_history(
    tmp_path: Path, process_3039: ProcessDetail
) -> None:
    path = tmp_path / "card.db"
    government = process_3039.model_copy(update={"number": "RCL/123"})
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        repo.upsert_summary(government, now=NOW)
        root = physical(repo, government)
        original = retain(repo, root)
        old = repo.get_content_snapshot(original.bill_context_id)
        assert repo.move_government_rows(10, 11) == 1
        assert repo.get_content_snapshot(original.bill_context_id) == old
        saved = repo.dump()
        repo.restore(saved)
        assert repo.dump() == saved
        assert repo.get_content_version(original.content_id) == original
    assert audit(path)["status"] == "recorded"


def test_legacy_unbound_card_has_no_invented_history(
    tmp_path: Path, process_3039: ProcessDetail
) -> None:
    with closing(SqliteBillRepository(tmp_path / "card.db")) as repo:
        repo.migrate()
        root = physical(repo, process_3039)
        assert inspect_content(repo, root) is None
        assert prepare_publication_content(repo, FixedClock(NOW), root) is None
        assert repo.list_content_versions(root.id or 0) == []


def test_exact_compact_outbox_context_is_supported_without_full_bill_copy(
    tmp_path: Path, process_3039: ProcessDetail
) -> None:
    with closing(SqliteBillRepository(tmp_path / "card.db")) as repo:
        repo.migrate()
        root = physical(repo, process_3039)
        original = retain(repo, root)
        assert root.id is not None, "the fixture saved its card"
        compact = continuation_snapshot(bill_of(process_3039))
        plan = DeliveryPlan(
            bill_json=compact,
            rendered_text="compact edit",
            revision_of=root.id,
            edit_message_id=10,
            reply_to=10,
            continuation_parent=root.id,
        )
        identity = repo.create_publication(
            Publication(
                term=10,
                number=process_3039.number,
                channel_id="channel",
                kind=PublicationKind.MESSAGE_PART,
                status=PublicationStatus.SENT,
                message_id=10,
                created_at=NOW,
                ref=f"card/{root.id}/{'b' * 64}-{root.id}/1",
                delivery=plan,
            )
        )
        operation = repo.publication_by_id(identity)
        assert operation is not None, "the fixture saved its compact physical edition"
        edition = retain(repo, operation, previous=original)
        snapshot = repo.get_content_snapshot(edition.bill_context_id)
        assert snapshot is not None and snapshot.payload_json == compact
        assert "analysis" not in json.loads(compact) and "rcl" not in json.loads(compact)
        before = repo.dump()
        assert retain(repo, operation, previous=original) == edition
        assert repo.dump() == before


@pytest.mark.parametrize("fault", ["identity", "foreign_key"])
def test_declared_legacy_integrity_failure_rejects_restore_without_new_physical_assumptions(
    tmp_path: Path,
    fault: str,
) -> None:
    path = tmp_path / "legacy.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        old = version().model_copy(update={"rendered_text": None, "language": None})
        record(repo, old)
        valid = repo.dump()
        repo.restore(valid)
        assert repo.dump() == valid and repo.get_content_version(old.content_id) == old
    with closing(sqlite3.connect(path)) as db:
        if fault == "identity":
            db.execute("UPDATE content_versions SET content_id='wrong'")
        else:
            db.execute("INSERT INTO publication_content VALUES(999,'missing')")
        db.commit()
    with closing(SqliteBillRepository(path)) as repo:
        broken = repo.dump()
        repo.restore(valid)
        with pytest.raises(ValueError, match="invalid_content_history"):
            repo.restore(broken)
        assert repo.dump() == valid


def test_all_controlled_legacy_version_json_and_ids_match_pre_reader_baseline() -> None:
    first = version()
    second = version(previous=first.content_id, text="revision")
    assert [
        (value.content_id, hashlib.sha256(value.model_dump_json().encode()).hexdigest())
        for value in (first, second)
    ] == [
        (
            "content-v1:c990b31a83365aa7341e1d62b8c189b5cd4f16480089651e4f9fc7bc4a5525d8",
            "4f34a657b18f246c8444c7b896792284b0fd4c3610ff5d6a6a6fae690a986ef9",
        ),
        (
            "content-v1:196eba30b571e0e25d111a5e8d0bb84227ac7a18201af15900d6544f4e3c4dc7",
            "68853bc7f943135bb4ea70a3dc8aece4299c87d62809848134d28cf3e1750158",
        ),
    ]


@pytest.mark.parametrize(
    "statement",
    [
        "DROP TABLE publications",
        "ALTER TABLE publications DROP COLUMN document_message_ids",
        "ALTER TABLE publications DROP COLUMN delivery_json",
    ],
)
def test_nullable_card_doctor_bounds_missing_physical_layout(
    tmp_path: Path, process_3039: ProcessDetail, statement: str
) -> None:
    path = tmp_path / "card.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        retain(repo, physical(repo, process_3039))
    with closing(sqlite3.connect(path)) as db:
        db.execute(statement)
        db.commit()
    before = path.read_bytes()
    report = audit(path)
    assert report["status"] == "failed"
    assert any(row["code"] == "invalid_card_content_history" for row in report["findings"])
    assert all(row["current_content_id"] is None for row in report["histories"])
    assert path.read_bytes() == before


def test_typed_numeric_string_targets_cannot_hide_unbound_edit(
    tmp_path: Path, process_3039: ProcessDetail
) -> None:
    path = tmp_path / "card.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        root = physical(repo, process_3039)
        original = retain(repo, root)
        plan = DeliveryPlan(
            bill_json=bill_of(process_3039).model_dump_json(),
            rendered_text="untracked",
            revision_of=root.id,
            edit_message_id=10,
            continuation_parent=root.id,
            reply_to=10,
        )
        identity = repo.create_publication(
            Publication(
                term=10,
                number=process_3039.number,
                channel_id="channel",
                kind=PublicationKind.MESSAGE_PART,
                status=PublicationStatus.SENT,
                message_id=10,
                created_at=NOW,
                ref="unrecognized",
                delivery=plan,
            )
        )
    raw = plan.model_dump(mode="json")
    for field in ("revision_of", "edit_message_id", "continuation_parent"):
        raw[field] = str(raw[field])
    with closing(sqlite3.connect(path)) as db:
        db.execute(
            "UPDATE publications SET delivery_json=? WHERE id=?", (json.dumps(raw), identity)
        )
        db.commit()
    with closing(SqliteBillRepository(path)) as repo:
        loaded = repo.publication_by_id(identity)
        assert loaded is not None and loaded.delivery is not None, (
            "the stored operation has valid typed targets"
        )
        assert loaded.delivery.revision_of == root.id
        history = inspect_content(repo, root)
        assert (
            history is not None
            and history.original_id == original.content_id
            and history.current_id is None
        )
    assert audit(path)["histories"][0]["current_content_id"] is None


def test_card_requires_explicit_compact_profile_and_cannot_restore_semantic_context(
    tmp_path: Path, process_3039: ProcessDetail
) -> None:
    with closing(SqliteBillRepository(tmp_path / "card.db")) as repo:
        repo.migrate()
        pub = physical(repo, process_3039)
        value = retain(repo, pub)
        payload = value.model_dump(mode="json")
        payload.pop("card_context_profile", None)
        with pytest.raises(ValueError):
            ContentVersion.model_validate(payload)
        with pytest.raises(ValueError, match="full original context"):
            restore_content_delivery(repo, value)


def test_compact_card_keeps_render_and_confirmation_after_payload_cleanup(
    tmp_path: Path, process_3039: ProcessDetail
) -> None:
    path = tmp_path / "card.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        pub = physical(repo, process_3039)
        retain(repo, pub)
        view = inspect_content(repo, pub)
        assert view is not None and view.current is not None
        assert view.current.full_context_available is False
        assert view.current.rendered_text_known
        assert view.integrity == "valid"
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("UPDATE publications SET delivery_json=NULL WHERE id=?", (pub.id,))
        cleaned = repo.publication_by_id(pub.id or 0)
        assert cleaned is not None, "cleanup preserves the actual physical publication"
        assert inspect_content(repo, cleaned) == view
        before = repo.dump()
        repo.restore(before)
        assert repo.dump() == before
    assert audit(path)["status"] == "recorded"


def test_card_profile_uses_exact_continuation_projection_and_rejects_full_body(
    tmp_path: Path, process_3039: ProcessDetail
) -> None:
    with closing(SqliteBillRepository(tmp_path / "card.db")) as repo:
        repo.migrate()
        pub = physical(repo, process_3039)
        value = retain(repo, pub)
        assert pub.delivery is not None, "the fixture stores the actual frozen physical plan"
        compact = repo.get_content_snapshot(value.bill_context_id)
        assert compact is not None, "the fixture retains its compact context"
        payload = json.loads(compact.payload_json)
        assert "commission_review" in payload and "analysis" not in payload and "rcl" not in payload
        assert compact == card_bill_snapshot(pub.delivery.bill_json)
        full = ContentSnapshot(kind="bill", payload_json=pub.delivery.bill_json)
        repo.save_content_snapshot(full)
        with pytest.raises(ValueError):
            repo.save_content_version(
                value.model_copy(update={"bill_snapshot_id": full.snapshot_id})
            )
        legacy = version()
        with pytest.raises(ValueError):
            ContentVersion.model_validate(
                legacy.model_dump(mode="json") | {"card_context_profile": "continuation-v1"}
            )
        assert "card_context_profile" not in legacy.model_dump_json()
