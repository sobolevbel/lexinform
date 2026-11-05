"""Replay fresh amendment history using frozen local state and existing projection APIs."""

import argparse
import gzip
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tests.fakes import FakePublisher, FakeSejmGateway, FixedClock

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    AmendmentRevisionApplicationWork,
    AmendmentRevisionTarget,
    AmendmentsRecord,
    Bill,
    ComputationResult,
    ContentVersion,
    DeliveryPlan,
    DeliveryResolution,
    IncomingCommand,
    Publication,
    PublicationKind,
    PublicationStatus,
    StatusChange,
)
from lexinform.services.posting import Poster
from lexinform.services.revision_applications import AmendmentRevisionApplications
from lexinform.services.status_change_applications import record_status_change

NOW = datetime(2026, 11, 5, 12, tzinfo=UTC)
CHANNEL = "@offline_application_history"
HISTORY_TABLES = {"content_versions", "publication_content"}


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def rows(script: str) -> dict[str, list[tuple[Any, ...]]]:
    with closing(sqlite3.connect(":memory:")) as db:
        db.executescript(script)
        return {
            name: db.execute(f'SELECT rowid,* FROM "{name}" ORDER BY rowid').fetchall()
            for (name,) in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        }


def packet(repo: SqliteBillRepository, root_id: int, publisher: FakePublisher) -> dict[str, Any]:
    sql = repo.dump()
    table_rows = rows(sql)
    versions = repo.list_content_versions(root_id)
    normalized: list[ContentVersion] = []
    ids: dict[str, str] = {}
    for version in versions:
        previous = ids.get(version.previous_version_id or "")
        plain = version.model_copy(
            update={
                "application_ref_profile": None,
                "application_ids": (),
                "previous_version_id": previous,
            }
        )
        ids[version.content_id] = plain.content_id
        normalized.append(plain)
    before = sql
    repo.restore(sql)
    repo.restore(sql)
    if repo.dump() != before:
        raise ValueError("Replay must preserve exact repeated restore")
    return {
        "core_tables": {
            name: digest(value) for name, value in table_rows.items() if name not in HISTORY_TABLES
        },
        "normalized_versions": [value.model_dump(mode="json") for value in normalized],
        "normalized_bindings": [
            (row[1], ids.get(row[2], row[2])) for row in table_rows["publication_content"]
        ],
        "raw_versions": [value.model_dump(mode="json") for value in versions],
        "actual_application_rows": table_rows["result_applications"],
        "physical_payloads": [
            (row[0], row[1], row[2], row[3], row[4], row[5]) for row in table_rows["publications"]
        ],
        "sent": len(publisher.sent),
        "edited": len(publisher.edited),
        "sql_bytes": len(sql.encode()),
        "gzip_bytes": len(gzip.compress(sql.encode(), mtime=0)),
        "sql": sql,
        "double_restore_exact": True,
    }


def queued(
    repo: SqliteBillRepository, bill: Bill, change: StatusChange, result: ComputationResult
) -> Publication:
    repo.save_computation_result(result)
    assert isinstance(result.record, AmendmentsRecord), "the replay requires retained amendments"
    offered = change.model_copy(
        update={
            "id": None,
            "amendments": result.record,
            "new_fingerprint": "offline-reference-original",
        }
    )
    saved = record_status_change(repo, offered, now=NOW)
    assert saved is not None and saved.id is not None, (
        "the public projection records its actual event"
    )
    repo.create_publication(
        Publication(
            term=bill.term,
            number=bill.number,
            kind=PublicationKind.NEW_BILL,
            channel_id=CHANNEL,
            status=PublicationStatus.SENT,
            created_at=NOW,
            sent_at=NOW,
            message_id=100,
            delivery=DeliveryPlan(
                bill_json=bill.model_dump_json(), rendered_text="Technical existing root"
            ),
        )
    )
    identity = repo.create_publication(
        Publication(
            term=bill.term,
            number=bill.number,
            kind=PublicationKind.STATUS_UPDATE,
            status=PublicationStatus.QUEUED,
            channel_id=CHANNEL,
            created_at=NOW,
            status_change_id=saved.id,
            delivery=DeliveryPlan(
                bill_json=bill.model_dump_json(),
                change_json=saved.model_dump_json(),
                reply_to=100,
                today=NOW.date(),
            ),
        )
    )
    publication = repo.publication_by_id(identity)
    assert publication is not None, "the original exists before its first render"
    return publication


def poster(repo: SqliteBillRepository, publisher: FakePublisher) -> Poster:
    return Poster(
        repo,
        publisher,
        FixedClock(NOW),
        gateway=FakeSejmGateway(),
        channel_id=CHANNEL,
        max_attempts=3,
        attention_mode="off",
    )


def revision(
    repo: SqliteBillRepository, original: Publication, result: ComputationResult
) -> Publication:
    assert original.id is not None and original.delivery is not None, (
        "the original has its confirmed frozen frame"
    )
    assert isinstance(result.record, AmendmentsRecord), "the revision reuses retained amendments"
    target = AmendmentRevisionTarget(
        publication_id=original.id,
        channel_id=CHANNEL,
        source_url=result.record.source_url or "",
        delivery=original.delivery.model_copy(
            update={
                "rendered_text": "",
                "revision_of": original.id,
                "edit_message_id": original.message_id,
            }
        ),
    )
    service = AmendmentRevisionApplications(repo, FixedClock(NOW))
    _, snapshots = service.freeze(
        Bill.model_validate_json(target.delivery.bill_json), target, amendment_key=result.custom_id
    )
    for snapshot in snapshots:
        repo.save_content_snapshot(snapshot)
    work = AmendmentRevisionApplicationWork(
        term=original.term,
        number=original.number,
        result_id=result.result_id,
        request_key=result.custom_id,
        request_sha256=result.request_sha256 or "",
        original_publication_id=original.id,
        channel_id=CHANNEL,
        message_id=original.message_id or 0,
        source_url=target.source_url,
        bill_snapshot_id=snapshots[0].snapshot_id,
        change_snapshot_id=snapshots[1].snapshot_id,
        plan_snapshot_id=snapshots[2].snapshot_id,
        created_at=NOW,
        next_at=NOW,
    )
    repo.save_revision_application_work(work)
    service.drain()
    revisions = repo.list_amendment_revisions(original.id)
    assert len(revisions) == 1, (
        "the existing public work projection creates one revision and its receipt"
    )
    return revisions[0]


def replay(
    repo: SqliteBillRepository,
    bill: Bill,
    change: StatusChange,
    result: ComputationResult,
    *,
    recovery: bool = False,
) -> dict[str, Any]:
    publisher = FakePublisher(publication_by_id=repo.publication_by_id)
    service = poster(repo, publisher)
    original = queued(repo, bill, change, result)
    assert original.id is not None, "the queued original has its actual identity"
    if recovery:
        repo.mark_publication(
            original.id,
            PublicationStatus.QUEUED,
            error="source_context_invalid",
            count_attempt=False,
        )
        command = IncomingCommand(
            update_id=77701,
            chat_id=CHANNEL,
            message_id=1,
            text=f"/delivery {original.id} retry",
            received_at=NOW,
        )
        repo.record_command(command)
        resolution = DeliveryResolution(
            publication_id=original.id,
            publication_ids=(original.id,),
            term=original.term,
            number=original.number,
            kind=original.kind,
            channel_id=CHANNEL,
            action="retry",
            command=command,
            resolved_at=NOW,
        )
        if not service.recover_source_context_retry(resolution):
            raise ValueError("The public audited recovery must retain a known original source")
        recovered = repo.publication_by_id(original.id)
        assert recovered is not None, "audited recovery retains its original operation"
        original = recovered
    if not service.deliver(original):
        raise ValueError("The controlled original must deliver")
    confirmed = repo.publication_by_id(original.id or 0)
    assert confirmed is not None, "the confirmed original remains stored"
    first = packet(repo, confirmed.id or 0, publisher)
    revised = revision(repo, confirmed, result)
    if not service.deliver(revised):
        raise ValueError("The controlled revision must edit its confirmed original")
    second = packet(repo, confirmed.id or 0, publisher)
    frozen = repo.dump()
    assert service.deliver(repo.publication_by_id(revised.id or 0) or revised)
    if repo.dump() != frozen:
        raise ValueError("Confirmed retry cannot enrich existing history")
    return {
        "original": first,
        "revision": second,
        "repeat_exact": True,
        "provider_calls": 0,
        "network_calls": 0,
        "recovery": recovery,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    value = json.loads(args.input.read_text())
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        report = replay(
            repo,
            Bill.model_validate_json(value["bill_json"]),
            StatusChange.model_validate_json(value["change_json"]),
            ComputationResult.model_validate_json(value["result_json"]),
        )
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
