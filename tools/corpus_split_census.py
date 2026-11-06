"""Count explicit sealed corpus populations without promoting machine observations to labels."""

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_review import EMPTY_REVIEW_QUESTIONS, settle
from tools.corpus_runs import SUITES, verify

_SHA256 = re.compile(r"[0-9a-f]{64}")
_QUEUES = {"annotations", "empty-review", "holdout"}
_REVIEWS = {"review", "review-form"}


def load(directory: Path, sha256: str, output: Path) -> tuple[str, dict[str, Any]]:
    manifest = directory / "manifest.json"
    if not _SHA256.fullmatch(sha256) or digest(read_artifact(manifest)) != sha256:
        raise ValueError(f"Run manifest SHA mismatch: {directory.name}")
    sealed = verify(directory)
    suite = sealed["suite"]
    if suite not in _QUEUES | _REVIEWS or sealed["status"] != "recorded":
        raise ValueError(f"Unsupported recorded census input: {directory.name}")
    started_raw = read_artifact(directory / "started.json")
    if digest(started_raw) != sealed["artifacts"]["started.json"]["sha256"]:
        raise ValueError(f"Sealed census input changed: {directory.name}")
    started = json.loads(started_raw)
    corpus = started.get("corpus")
    if corpus and output.resolve().is_relative_to(Path(corpus).resolve()):
        raise ValueError("Census output must be outside the source corpus")
    module, options = SUITES[suite]
    command = started.get("command", [])
    if command[1:3] != ["-m", f"tools.{module}"] or any(flag not in command for flag in options):
        raise ValueError(f"Run lacks its expected offline producer: {directory.name}")
    report_raw = read_artifact(directory / "result.json")
    if (
        digest(report_raw) != sealed["artifacts"]["result.json"]["sha256"]
        or digest(read_artifact(manifest)) != sha256
    ):
        raise ValueError(f"Sealed census input changed: {directory.name}")
    return suite, json.loads(report_raw)


def population(suite: str, report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = (
        [{"id": f"doc:{key}", **value} for key, value in report["records"].items()]
        if suite == "empty-review"
        else report["cases"]
    )
    cases = {}
    for row in rows:
        key = row["id"]
        if key in cases or not key or not row["group"]:
            raise ValueError("Queue case requires unique exact identity and a recorded group")
        hashes = (
            [row["raw_sha256"]]
            if suite == "empty-review"
            else [item["sha256"] for item in row["originals"]]
            if suite == "holdout"
            else []
        )
        members = [
            leaf["sha256"]
            for leaf in row.get("leaves", ())
            if suite == "empty-review"
            and leaf.get("members")
            and leaf.get("bytes", 0) > 0
            and "sha256" in leaf
        ]
        if any(not _SHA256.fullmatch(value) for value in (*hashes, *members)):
            raise ValueError("Queue raw alias is not a SHA-256")
        cases[key] = {
            "title": row.get("title", row.get("name", key)),
            "group": row["group"],
            "questions": list(
                EMPTY_REVIEW_QUESTIONS if suite == "empty-review" else row["questions"]
            ),
            "holdout_eligible": (
                suite == "holdout"
                and row["split"] == "holdout_pending"
                and row["used_for_tuning"] is False
            ),
            "raw": hashes,
            "member_raw": members,
        }
    return cases


def audit(runs: list[tuple[Path, str]], output: Path) -> dict[str, Any]:
    roots = [(path.resolve(strict=True), pin) for path, pin in runs]
    if len({pin for _, pin in roots}) != len(roots):
        raise ValueError("Supply each run seal once")
    if (
        output.is_symlink()
        or output.exists()
        or any(output.resolve().is_relative_to(path) for path, _ in roots)
    ):
        raise ValueError("Census output must be new and outside the sealed runs")

    sealed = {pin: load(path, pin, output) for path, pin in roots}
    queues = {
        pin: population(suite, report)
        for pin, (suite, report) in sealed.items()
        if suite in _QUEUES
    }
    if not queues:
        raise ValueError("Supply at least one pinned population queue")
    case_groups: dict[str, set[str]] = defaultdict(set)
    case_suites: dict[str, set[str]] = defaultdict(set)
    aliases: dict[str, set[str]] = defaultdict(set)
    for pin, cases in queues.items():
        for identity, row in cases.items():
            case_groups[identity].add(row["group"])
            case_suites[identity].add(sealed[pin][0])
            for sha256 in (*row["raw"], *row["member_raw"]):
                aliases[sha256].add(identity)

    parent = {identity: identity for identity in case_groups}

    def representative(identity: str) -> str:
        while identity != parent[identity]:
            parent[identity] = parent[parent[identity]]
            identity = parent[identity]
        return identity

    def connect(identities: set[str]) -> None:
        anchor, *others = sorted(identities)
        for other in others:
            left, right = sorted((representative(anchor), representative(other)))
            parent[right] = left

    for identities in aliases.values():
        connect(identities)
    raw_component_count = len({representative(key) for key in case_groups})
    group_members: dict[str, set[str]] = defaultdict(set)
    for identity, groups in case_groups.items():
        for group in groups:
            group_members[group].add(identity)
    for identities in group_members.values():
        connect(identities)

    review_records: dict[str, dict[str, Any]] = defaultdict(dict)
    review_targets: dict[str, dict[str, Any]] = {}
    counts = Counter(suite for suite, _ in sealed.values())
    for suite, report in sealed.values():
        if suite not in _REVIEWS:
            continue
        queue = report["queue"]
        pin = queue["manifest_sha256"]
        if pin not in queues or queue["suite"] != sealed[pin][0]:
            raise ValueError("Supply the exact pinned parent of each review or form")
        if suite == "review-form":
            continue
        for row in report["cases"]:
            identity = row["id"]
            target = {
                key: value for key, value in queues[pin][identity].items() if key != "member_raw"
            }
            if target["group"] != row["group"] or any(
                value not in report["answers"].values()
                for value in (review["answers_sha256"] for review in row["reviews"])
            ):
                raise ValueError("Recorded review differs from its pinned queue or answers")
            resolved = settle(identity, target, row["reviews"])
            if any(row[key] != resolved[key] for key in ("status", "split", "oracle", "labels")):
                raise ValueError("Recorded labels differ from their human reviews")
            previous = review_targets.setdefault(identity, target)
            if previous != target:
                raise ValueError("Case has conflicting review contracts across queues")
            for review in row["reviews"]:
                key = review["answers_sha256"]
                if review_records[identity].setdefault(key, review) != review:
                    raise ValueError("One pinned answer has contradictory review rows")

    diagnostics = {key for key, suites in case_suites.items() if suites - {"holdout"}}
    reserved = {key for key, suites in case_suites.items() if "holdout" in suites}
    diagnostic_components = {representative(key) for key in diagnostics}
    diagnostic_groups = set().union(*(case_groups[key] for key in diagnostics))
    overlaps = {key for key in reserved if representative(key) in diagnostic_components}
    labels: Counter[str] = Counter()
    independent = set()
    for identity, reviews in review_records.items():
        if len({review["reviewer"] for review in reviews.values()}) != len(reviews):
            raise ValueError(f"Multiple pinned answers by one reviewer for case: {identity}")
        row = settle(identity, review_targets[identity], list(reviews.values()))
        if row["oracle"] == "human_review":
            labels.update(
                "conflict"
                if isinstance(value, dict)
                else "unknown"
                if value == "unknown"
                else "known"
                for value in row["labels"].values()
            )
        if row["split"] == "holdout" and identity not in overlaps:
            independent.add(identity)
    result = {
        "version": 1,
        "inputs": {pin: suite for pin, (suite, _) in sealed.items()},
        "runs_by_suite": dict(counts),
        "exact_case_ids": len(case_groups),
        "case_components_after_raw_aliases": raw_component_count,
        "recorded_groups": len(set().union(*case_groups.values())),
        "case_ids_with_conflicting_groups": sorted(
            key for key, groups in case_groups.items() if len(groups) > 1
        ),
        "diagnostic_case_ids": len(diagnostics),
        "diagnostic_recorded_groups": len(diagnostic_groups),
        "reserved_holdout_case_ids": len(reserved),
        "reserved_recorded_groups": len(set().union(*(case_groups[key] for key in reserved))),
        "reserved_overlap_case_ids": sorted(overlaps),
        "independence_confirmed_case_ids": sorted(independent),
        "human_review_question_counts": {
            key: labels[key] for key in ("known", "unknown", "conflict")
        },
        "holdout_evaluation_use": "unknown",
        "scope": "Supplied sealed runs only; raw aliases record identical bytes.",
    }
    write_json(output, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run", nargs=2, metavar=("DIRECTORY", "SHA256"), action="append", required=True
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        audit([(Path(path), pin) for path, pin in args.run], args.output)
    except (KeyError, OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
