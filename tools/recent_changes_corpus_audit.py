"""Compare process outcomes, RCL text selection and migration history without network calls."""

import argparse
import datetime as dt
import gzip
import hashlib
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from lexinform.adapters.sejm_api import parse_process_detail
from lexinform.adapters.sqlite_repo import MIGRATIONS
from lexinform.models import Bill, BillStatus, RclProject, is_over, next_phase


def replay(corpus: Path) -> dict[str, Any]:
    day = dt.date(2026, 10, 14)
    now = dt.datetime.combine(day, dt.time(), tzinfo=dt.UTC)
    sources: dict[str, str] = {}
    processes: dict[str, dict[str, Any]] = {}
    excluded_processes = 0
    for term in (8, 9, 10):
        path = corpus / f"sejm/term{term}/process_details.json.gz"
        raw = path.read_bytes()
        sources[str(path.relative_to(corpus))] = hashlib.sha256(raw).hexdigest()
        records = json.loads(gzip.decompress(raw))
        for number, record in records.items():
            if record.get("documentTypeEnum") != "BILL":
                excluded_processes += 1
                continue
            detail = parse_process_detail(record)
            bill = Bill(
                summary=detail,
                stages=detail.stages,
                status=BillStatus.ANALYZED,
                first_seen_at=now,
                last_checked_at=now,
            )
            phase = next_phase(bill, today=day)
            processes[f"{term}/{number}"] = {
                "over": is_over(bill, today=day),
                "phase": phase.key if phase is not None else None,
            }
    path = corpus / "INDEX.json"
    raw = path.read_bytes()
    sources["INDEX.json"] = hashlib.sha256(raw).hexdigest()
    projects: dict[str, dict[str, Any]] = {}
    missing_projects: list[str] = []
    for identity, entity in json.loads(raw)["entities"].items():
        if not identity.startswith("rcl:"):
            continue
        path = corpus / entity["project"]
        if not path.is_file():
            missing_projects.append(identity)
            continue
        raw = path.read_bytes()
        sources[str(path.relative_to(corpus))] = hashlib.sha256(raw).hexdigest()
        project = RclProject.model_validate_json(raw)
        projects[identity] = {
            role: {"id": document.id, "name": document.name}
            for role, document in project.text_documents().items()
        }
    return {
        "day": str(day),
        "processes": processes,
        "projects": projects,
        "sources": sources,
        "excluded_non_bill_processes": excluded_processes,
        "missing_project_metadata": missing_projects,
        "migration_hashes": [hashlib.sha256(sql.encode()).hexdigest() for sql in MIGRATIONS],
        "implementation_hashes": {
            name: hashlib.sha256(Path(inspect.getfile(value)).read_bytes()).hexdigest()
            for name, value in (
                ("process_parser", parse_process_detail),
                ("process_outcomes", is_over),
                ("rcl_selection", RclProject),
            )
        },
    }


def audit(corpus: Path, destination: Path, baseline_source: Path | None) -> dict[str, Any]:
    destination.mkdir(parents=True, exist_ok=True)
    current = replay(corpus)
    (destination / "current.json").write_text(json.dumps(current, ensure_ascii=False, indent=1))
    report: dict[str, Any] = {
        "processes": len(current["processes"]),
        "projects": len(current["projects"]),
        "excluded_non_bill_processes": current["excluded_non_bill_processes"],
        "missing_project_metadata": current["missing_project_metadata"],
        "limits": [
            "Recorded metadata only; no source refresh or document download.",
            "No act contents, Telegram delivery or model quality assessment.",
            "No previous decisions are automatically treated as an independent oracle.",
        ],
    }
    if baseline_source is not None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(baseline_source.resolve())
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), str(corpus.resolve()), "--worker"],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        baseline = json.loads(result.stdout)
        (destination / "baseline.json").write_text(
            json.dumps(baseline, ensure_ascii=False, indent=1)
        )
        if current["sources"] != baseline["sources"]:
            raise ValueError("Corpus inputs changed between replays")
        for name in ("processes", "projects"):
            changes = [
                {
                    "identity": identity,
                    "before": baseline[name].get(identity),
                    "after": current[name].get(identity),
                }
                for identity in sorted(set(baseline[name]) | set(current[name]))
                if baseline[name].get(identity) != current[name].get(identity)
            ]
            report[f"changed_{name}"] = len(changes)
            (destination / f"{name}-changes.json").write_text(
                json.dumps(changes, ensure_ascii=False, indent=1)
            )
        previous = baseline["migration_hashes"]
        report["migration_prefix_unchanged"] = (
            previous == current["migration_hashes"][: len(previous)]
        )
        report["schema_before"] = len(previous)
        report["schema_after"] = len(current["migration_hashes"])
    (destination / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("destination", type=Path, nargs="?")
    parser.add_argument("--baseline-source", type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(replay(args.corpus), ensure_ascii=False))
    elif args.destination is None:
        parser.error("destination is required")
    else:
        print(json.dumps(audit(args.corpus, args.destination, args.baseline_source), indent=1))
