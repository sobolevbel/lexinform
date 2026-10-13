"""Replay a saved SQL dump without network, pipeline calls or changes to the source."""

import argparse
import datetime as dt
import json
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.attention import assess_attention
from lexinform.delivery_schedule import daily_schedule
from lexinform.models import (
    AgendaItem,
    Bill,
    ParticipationSnapshot,
    PublicationKind,
    Stage,
    StatusChange,
    participation_view,
)


def audit(dump: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as work:
        path = Path(work) / "audit.db"
        repo = SqliteBillRepository(path)
        repo.restore(dump.read_text())
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM publications WHERE status = 'sent' AND kind != 'digest'"
            " ORDER BY sent_at, id"
        ).fetchall()
        events: list[dict[str, object]] = []
        counts: Counter[str] = Counter()
        for row in rows:
            bill = repo.get(row["term"], row["number"])
            if bill is None:
                continue
            delivery = json.loads(row["delivery_json"] or "{}")
            if delivery.get("bill_json"):
                bill = Bill.model_validate_json(delivery["bill_json"])
                if delivery.get("participation"):
                    bill = participation_view(
                        bill, ParticipationSnapshot.model_validate(delivery["participation"])
                    )
            change = (
                repo.get_status_change(row["status_change_id"]) if row["status_change_id"] else None
            )
            if delivery.get("change_json"):
                change = StatusChange.model_validate_json(delivery["change_json"])
            now = dt.datetime.fromisoformat(row["sent_at"] or row["created_at"])
            assessment = assess_attention(
                bill,
                PublicationKind(row["kind"]),
                now=now,
                change=change,
                with_act=bool(delivery.get("with_act")),
                hearing=Stage.model_validate_json(delivery["hearing_json"])
                if delivery.get("hearing_json")
                else None,
                item=AgendaItem.model_validate_json(delivery["item_json"])
                if delivery.get("item_json")
                else None,
                moved_from=AgendaItem.model_validate_json(delivery["moved_from_json"])
                if delivery.get("moved_from_json")
                else None,
            )
            schedule = (
                daily_schedule(
                    assessment,
                    PublicationKind(row["kind"]),
                    now=dt.datetime.fromisoformat(row["created_at"]),
                    release_hour=18,
                    urgent_days=3,
                )
                if delivery.get("bill_json")
                else None
            )
            counts[f"{assessment.importance}/{assessment.urgency}"] += 1
            events.append(
                {
                    "id": row["id"],
                    "number": bill.number,
                    "kind": row["kind"],
                    "sent_at": now.isoformat(),
                    "message_id": row["message_id"],
                    "assessment": assessment.model_dump(mode="json"),
                    "has_snapshot": bool(delivery.get("bill_json")),
                    "daily_schedule": schedule.model_dump(mode="json") if schedule else None,
                }
            )
        events.sort(key=lambda item: str(item["sent_at"]))
        days = Counter(str(item["sent_at"])[:10] for item in events)
        windows = Counter(
            (schedule["route"], schedule["not_before"])
            for event in events
            if isinstance(schedule := event.get("daily_schedule"), dict)
        )
        report = {
            "source": str(dump),
            "period": [events[0]["sent_at"], events[-1]["sent_at"]] if events else [],
            "events": len(events),
            "physical_messages": len({item["message_id"] for item in events}),
            "matrix": dict(counts),
            "busiest_days": days.most_common(5),
            "without_snapshot": sum(not item["has_snapshot"] for item in events),
            "daily_snapshot_replay": {
                "eligible_events": sum(windows.values()),
                "release_windows": len(windows),
                "largest_windows": windows.most_common(5),
            },
            "limits": [
                "Missing historic snapshots use current bills; labels are not independent.",
                "No generated names, paid model requests or reader recall test.",
                "Daily replay excludes missing snapshots; windows are not guaranteed send times.",
            ],
        }
        (destination / "baseline.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        )
        (destination / "events.json").write_text(
            json.dumps(events, ensure_ascii=False, indent=2) + "\n"
        )
        projects = [
            {
                "number": bill.number,
                "title": bill.summary.title,
                "analysis": bill.analysis.model_dump(mode="json"),
            }
            for row in connection.execute(
                "SELECT term,number FROM bills WHERE analysis_json IS NOT NULL ORDER BY term,number"
            )
            if (bill := repo.get(row["term"], row["number"])) is not None
            and bill.analysis is not None
        ][:100]
        (destination / "editorial-sample.json").write_text(
            json.dumps(projects, ensure_ascii=False, indent=2) + "\n"
        )
        connection.close()
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dump", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    audit(args.dump, args.destination)
