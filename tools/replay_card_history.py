"""Replay public card delivery on pinned local state without models or network."""

import argparse
import gzip
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tests.fakes import FakePublisher, FakeSejmGateway, FixedClock

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.errors import DeliveryUncertainError
from lexinform.models import DeliveryPlan, Mnemonic, Publication, PublicationKind, PublicationStatus
from lexinform.models.content import editorial_bill_snapshot
from lexinform.services.card_edits import edit_card
from lexinform.services.message_parts import continuation_snapshot
from lexinform.services.publishing import PublishingService
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_runtime import CorpusReads, source_fingerprints

NOW = datetime(2026, 11, 5, tzinfo=UTC)
CHANNEL = "@offline_card_history_fixture"
MODES = ("confirmed", "uncertain", "unbound", "multipart", "rehome")


def table_rows(sql: str, query: str, parameters: tuple[object, ...] = ()) -> list[tuple[Any, ...]]:
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.executescript(sql)
        return conn.execute(query, parameters).fetchall()


def snapshot(sql: str, term: int, number: str, mode: str) -> dict[str, Any]:
    if mode not in MODES:
        raise ValueError("Unknown card replay mode")
    with closing(SqliteBillRepository(Path(":memory:"))) as repo:
        repo.migrate()
        repo.restore(sql)
        before = repo.dump()
        bill = repo.get(term, number)
        if bill is None or bill.analysis is None:
            raise ValueError("The pinned source requires an analysed Bill")
        clock = FixedClock(NOW)
        gateway = FakeSejmGateway()
        formatter = MessageFormatter("ru", today=lambda: NOW.date(), channel=CHANNEL)
        publisher = FakePublisher(formatter=formatter, publication_by_id=repo.publication_by_id)
        service = PublishingService(
            gateway,
            repo,
            publisher,
            clock,
            channel_id=CHANNEL,
            analysis=None,
            attention_enabled=False,
            attention_assess=False,
        )
        result = service.publish_bill(bill)
        root = service.card_of(bill)
        if root is None or root.id is None or root.delivery is None:
            raise ValueError("The public original card was not frozen")
        marker = Mnemonic(
            name="Офлайн проверка",
            language="ru",
            basis="Controlled render-only fixture",
            selected_at=NOW,
            origin="offline_fixture",
        )
        edited = bill.model_copy(
            update={"editorial": bill.editorial.model_copy(update={"mnemonic": marker})}
        )
        if mode == "uncertain":
            publisher.unknown_on_edit.add(number)
        try:
            edit_card(repo, clock, publisher, edited, root, None, channel_id=CHANNEL)
        except DeliveryUncertainError:
            if mode != "uncertain":
                raise
        if mode in ("unbound", "multipart"):
            repo.create_publication(
                Publication(
                    term=term,
                    number=number,
                    kind=PublicationKind.MESSAGE_PART,
                    status=PublicationStatus.QUEUED,
                    channel_id=CHANNEL,
                    ref=f"card/{root.id}/unsupported/2",
                    created_at=NOW,
                    delivery=DeliveryPlan(
                        bill_json=continuation_snapshot(edited),
                        rendered_text="Explicit unsupported fixture",
                        revision_of=root.id,
                        edit_message_id=root.message_id,
                        continuation_parent=root.id,
                        continuation_index=2 if mode == "multipart" else 1,
                        reply_to=root.message_id,
                    ),
                )
            )
        moved = repo.move_government_rows(term, term + 1) if mode == "rehome" else 0
        finished = repo.dump()
        if mode != "rehome":
            service.publish_bill(bill)
            edit_card(repo, clock, publisher, edited, root, None, channel_id=CHANNEL)
        repeat_exact = finished == repo.dump()
        physical = []
        for (identity,) in table_rows(
            finished, "SELECT id FROM publications WHERE channel_id=? ORDER BY id", (CHANNEL,)
        ):
            publication = repo.publication_by_id(identity)
            if publication is None:
                raise ValueError("Frozen physical operation disappeared")
            physical.append(
                {
                    "id": identity,
                    "kind": publication.kind,
                    "status": publication.status,
                    "sha256": digest(publication.model_dump_json().encode()),
                    "bill_utf8_bytes": len(publication.delivery.bill_json.encode())
                    if publication.delivery
                    else None,
                    "render_sha256": digest(publication.delivery.rendered_text.encode())
                    if publication.delivery
                    else None,
                }
            )

        def counts(dump: str) -> tuple[Any, ...]:
            return table_rows(
                dump,
                "SELECT (SELECT count(*) FROM llm_batch_intents), "
                "(SELECT count(*) FROM computation_results)",
            )[0]

        requests_before, requests_after = counts(before), counts(finished)
        repo.restore(finished)
        restored = repo.dump() == finished
        versions = repo.list_content_versions(root.id)
        core = {
            "published": bool(result),
            "source_bill_sha256": digest(bill.model_dump_json().encode()),
            "physical": physical,
            "repeat_dump_exact": repeat_exact,
            "repeat_not_applicable": mode == "rehome",
            "restore_exact": restored,
            "new_model_requests": requests_after[0] - requests_before[0],
            "new_computation_results": requests_after[1] - requests_before[1],
            "request_rows_sha256": digest(
                json.dumps(
                    table_rows(finished, "SELECT * FROM llm_batch_intents ORDER BY custom_id"),
                    separators=(",", ":"),
                ).encode()
            ),
            "result_rows_sha256": digest(
                json.dumps(
                    table_rows(finished, "SELECT * FROM computation_results ORDER BY result_id"),
                    separators=(",", ":"),
                ).encode()
            ),
            "gateway_calls": json.loads(json.dumps(gateway.calls)),
            "sent": [digest(value.text.encode()) for value in publisher.sent],
            "edited": [
                [digest(value.text.encode()), identity] for value, identity in publisher.edited
            ],
            "rehome_rows": moved,
        }
        return {
            "core": core,
            "metadata": {
                "content_versions": len(versions),
                "source_bill_utf8_bytes": len(bill.model_dump_json().encode()),
                "canonical_context_utf8_bytes": len(
                    editorial_bill_snapshot(continuation_snapshot(bill)).payload_json.encode()
                ),
                "sql_bytes": len(finished.encode()),
                "gzip_bytes": len(gzip.compress(finished.encode(), mtime=0)),
                "sql_sha256": digest(finished.encode()),
            },
        }


def choose(sql: str) -> list[tuple[int, str]]:
    identities = table_rows(sql, "SELECT term,number FROM bills ORDER BY term,number")
    with closing(SqliteBillRepository(Path(":memory:"))) as repo:
        repo.migrate()
        repo.restore(sql)
        bills = [
            bill
            for term, number in identities
            if (bill := repo.get(term, number)) is not None and bill.analysis is not None
        ]
        rcl = [bill for bill in bills if bill.number.startswith("RCL/")]
        unlinked = [bill for bill in rcl if bill.rcl is not None and not bill.rcl.print_number]
        if not rcl or not unlinked:
            raise ValueError("Pinned state lacks both natural RCL strata")
        selected = [
            max(rcl, key=lambda bill: len(bill.model_dump_json().encode())),
            max(unlinked, key=lambda bill: len(bill.model_dump_json().encode())),
            max(
                bills,
                key=lambda bill: len(
                    editorial_bill_snapshot(continuation_snapshot(bill)).payload_json.encode()
                ),
            ),
        ]
        return list(dict.fromkeys((bill.term, bill.number) for bill in selected))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    reads = CorpusReads(args.source.resolve().parent)
    with reads.guard():
        raw = read_artifact(args.source)
        if len(raw) > 32 * 1024 * 1024 or digest(raw) != args.source_sha256:
            raise ValueError("Source SQL pin or size bound failed")
        sql = raw.decode()
        chosen = choose(sql)
        cases = {
            f"{term}/{number}/{mode}": snapshot(sql, term, number, mode)
            for term, number in chosen
            for mode in MODES
            if mode != "rehome" or number != chosen[0][1]
        }
    if reads.changed_inputs():
        raise ValueError("Pinned input changed during replay")
    report = {
        "schema_version": 1,
        "kind": "public-card-history-replay",
        "source_sha256": digest(raw),
        "fixture_policy": (
            "Real frozen Bills; synthetic render-only mnemonic and unsupported controls; "
            "fake transport; no gold labels"
        ),
        "cases": cases,
        "sources": source_fingerprints(),
        "driver_sha256": digest(Path(__file__).read_bytes()),
    }
    if args.baseline:
        baseline = json.loads(read_artifact(args.baseline))
        if (
            baseline["driver_sha256"] != report["driver_sha256"]
            or baseline["source_sha256"] != report["source_sha256"]
        ):
            raise ValueError("Baseline driver/source differs")
        if {key: value["core"] for key, value in baseline["cases"].items()} != {
            key: value["core"] for key, value in cases.items()
        }:
            raise ValueError("Public physical/render/IO core changed")
        report["paired_core_exact"] = True
    write_json(args.output, report)


if __name__ == "__main__":
    main()
