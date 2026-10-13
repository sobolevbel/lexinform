"""Render saved publication evidence offline; missing history is explicitly excluded."""

import argparse
import hashlib
import inspect
import json
import sqlite3
import subprocess
import tempfile
from collections import Counter
from functools import partial
from pathlib import Path
from zoneinfo import ZoneInfo

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import PublicationKind


def audit(dump: Path, destination: Path) -> dict[str, object]:
    destination.mkdir(parents=True, exist_ok=True)
    raw = dump.read_text()
    events: list[dict[str, object]] = []
    exclusions: list[dict[str, object]] = []
    counts: Counter[str] = Counter()
    with tempfile.TemporaryDirectory() as work:
        path = Path(work) / "audit.db"
        repo = SqliteBillRepository(path)
        repo.restore(raw)
        with sqlite3.connect(path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM publications ORDER BY created_at, id"
            ).fetchall()
            for row in rows:
                pub = repo.publication_by_id(row["id"])
                assert pub is not None
                plan = pub.delivery
                if plan is None or not plan.replayable(pub.kind):
                    exclusions.append(
                        {"id": pub.id, "kind": pub.kind.value, "reason": "missing saved payload"}
                    )
                    continue
                day = plan.today or pub.created_at.astimezone(ZoneInfo("Europe/Warsaw")).date()
                for language in ("ru", "en"):
                    formatter = MessageFormatter(
                        language,
                        today=partial(type(day).fromisoformat, day.isoformat()),
                        channel=pub.channel_id,
                    )
                    rendered = formatter.delivery_preview(pub)
                    filename = f"{pub.id}-{language}.html"
                    (destination / filename).write_text(rendered + "\n")
                    events.append(
                        {
                            "id": pub.id,
                            "number": pub.number,
                            "kind": pub.kind.value,
                            "language": language,
                            "created_at": pub.created_at.isoformat(),
                            "file": filename,
                            "frozen": bool(plan.rendered_text or plan.group_text),
                            "sha256": hashlib.sha256(rendered.encode()).hexdigest(),
                        }
                    )
                counts[pub.kind.value] += 1
    report: dict[str, object] = {
        "code_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "state_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "implementation_hashes": {
            cls.__name__: hashlib.sha256(Path(inspect.getfile(cls)).read_bytes()).hexdigest()
            for cls in (SqliteBillRepository, MessageFormatter)
        },
        "source": str(dump),
        "period": [events[0]["created_at"], events[-1]["created_at"]] if events else [],
        "restored_payloads": sum(counts.values()),
        "coverage": dict(counts),
        "missing_kinds": sorted(kind.value for kind in PublicationKind if kind.value not in counts),
        "exclusions": exclusions,
        "events": events,
        "rubric": {
            "dimensions": ["new information", "decision/action", "timeliness", "clarity", "trust"],
            "range": [0, 2],
            "minimum_total": 1,
            "unsafe_outcome_or_deadline_cap": 4,
        },
        "limits": [
            "No current bill substitutes for missing historical snapshots.",
            "Frozen text is preserved; non-frozen snapshots use the current formatter.",
            "No network, pipeline, credentials, paid calls or reader assessment.",
        ],
    }
    (destination / "manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dump", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    result = audit(args.dump, args.destination)
    print(
        json.dumps(
            {key: value for key, value in result.items() if key not in {"events", "exclusions"}},
            indent=2,
        )
    )
