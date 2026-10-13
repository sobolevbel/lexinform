"""Build and render a week from copied state without sources, credentials or delivery."""

import argparse
import datetime as dt
import hashlib
import json
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

from lexinform.adapters.console import ConsolePublisher
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.services.digest import DigestService


@dataclass(frozen=True)
class ReplayClock:
    at: dt.datetime

    def now(self) -> dt.datetime:
        return self.at


def audit(dump: Path, output: Path, ref: str) -> None:
    output.mkdir(parents=True, exist_ok=True)
    raw = dump.read_text()
    reports: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory() as work:
        path = Path(work) / "copy.db"
        repo = SqliteBillRepository(path)
        repo.restore(raw)
        with sqlite3.connect(path) as conn:
            channels = [
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT channel_id FROM publications"
                    " WHERE kind='new_bill' AND status='sent'"
                )
            ]
        for channel_index, channel in enumerate(channels):
            for language in ("ru", "en"):
                publisher = ConsolePublisher(
                    MessageFormatter(language, attention_enabled=True, channel=channel)
                )
                service = DigestService(
                    repo,
                    publisher,
                    None,
                    ReplayClock(dt.datetime(2026, 10, 13, tzinfo=dt.UTC)),
                    channel_id=channel,
                    draft_channel_id="",
                    approve_label="",
                    attention_enabled=True,
                )
                week = service.build(ref)
                parts = publisher.render_digest_parts(week)
                for index, part in enumerate(parts):
                    (output / f"channel-{channel_index}-{language}-{index + 1}.html").write_text(
                        part.text
                    )
                reports.append(
                    {
                        "channel_index": channel_index,
                        "language": language,
                        "cards": len(week.cards),
                        "updates": len(week.updates),
                        "parts": len(parts),
                        "pending": week.pending_events,
                        "limited_history": sum(
                            not entry.historical_snapshot for entry in (*week.cards, *week.updates)
                        ),
                        "entries": [
                            {
                                "number": entry.number,
                                "kind": entry.kind.value,
                                "event": entry.event,
                                "decision_key": entry.decision_key,
                            }
                            for entry in week.updates
                        ],
                    }
                )
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "ref": ref,
                "state_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                "replays": reports,
                "limits": [
                    "Historical snapshots alone support outcomes; "
                    "current bills supply labels and future windows.",
                    "No reader assessment or paid experiment.",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dump", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("ref")
    args = parser.parse_args()
    audit(args.dump, args.output, args.ref)
