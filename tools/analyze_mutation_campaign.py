"""Map observed mutant failures to tests and simulate explicit test removals."""

import argparse
import json
from collections import defaultdict
from pathlib import Path


def analyze(campaign: Path, excluded: list[str], refresh: Path | None = None) -> dict[str, object]:
    run = json.loads((campaign / "run.json").read_text())
    selected = set(run["selected"])
    inputs = [(campaign, selected)]
    if refresh is not None:
        updated = json.loads((refresh / "run.json").read_text())
        refreshed = set(updated["selected"])
        if (
            not refreshed <= selected
            or updated["source_sha256_before"] != run["source_sha256_before"]
        ):
            raise ValueError("A refresh must use the original source and selected mutations")
        run["outcomes"].update(updated["outcomes"])
        inputs = [(campaign, selected - refreshed), (refresh, refreshed)]
    covered: dict[str, set[str]] = defaultdict(set)
    failures: dict[str, set[str]] = defaultdict(set)
    call_failures: dict[str, set[str]] = defaultdict(set)
    assertion_failures: dict[str, set[str]] = defaultdict(set)
    baseline_seconds: dict[str, float] = defaultdict(float)
    for directory, active in inputs:
        local_baseline: dict[str, float] = defaultdict(float)
        for path in sorted((directory / "reports").glob("*.jsonl")):
            for line in path.read_text().splitlines():
                row = json.loads(line)
                mutant, nodeid = row["mutant"], row["nodeid"]
                if mutant == "":
                    local_baseline[nodeid] += row["seconds"]
                if mutant not in active:
                    continue
                covered[nodeid].add(mutant)
                if row["outcome"] == "failed":
                    failures[mutant].add(nodeid)
                    if row["phase"] == "call":
                        call_failures[mutant].add(nodeid)
                        text = row["failure"] or ""
                        if "AssertionError" in text or "DID NOT RAISE" in text:
                            assertion_failures[mutant].add(nodeid)
        baseline_seconds.update(local_baseline)
    detected = {mutant for mutant, tests in failures.items() if tests}
    remaining = {
        mutant
        for mutant, tests in failures.items()
        if any(
            not any(test == name or test.startswith(name + "[") for name in excluded)
            for test in tests
        )
    }
    by_test = []
    for nodeid, mutants in covered.items():
        killed = {mutant for mutant in mutants if nodeid in failures[mutant]}
        exclusive = {mutant for mutant in killed if failures[mutant] == {nodeid}}
        by_test.append(
            {
                "nodeid": nodeid,
                "observed_mutations": len(mutants),
                "failing_mutations": sorted(killed),
                "exclusive_failing_mutations": sorted(exclusive),
                "unmutated_seconds": baseline_seconds.get(nodeid),
            }
        )
    return {
        "selected": len(selected),
        "exit_codes": {
            str(code): list(run["outcomes"].values()).count(code)
            for code in set(run["outcomes"].values())
        },
        "detected_with_reports": len(detected),
        "detected_by_call_assertion": len(assertion_failures),
        "setup_or_teardown_only": sorted(detected - set(call_failures)),
        "ablation": {
            "excluded": excluded,
            "lost_detections": sorted(detected - remaining),
            "remaining_detections": len(remaining),
        },
        "failures_by_mutant": {
            key: sorted(value) for key, value in sorted(failures.items()) if value
        },
        "call_assertions_by_mutant": {
            key: sorted(value) for key, value in sorted(assertion_failures.items()) if value
        },
        "tests": sorted(by_test, key=lambda row: str(row["nodeid"])),
        "limits": "A bounded sample cannot prove test redundancy or complete mutation coverage.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign", type=Path)
    parser.add_argument("--exclude-nodeid", action="append", default=[])
    parser.add_argument("--refresh", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    result = analyze(arguments.campaign, arguments.exclude_nodeid, arguments.refresh)
    with arguments.output.open("x") as output:
        output.write(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                key: value
                for key, value in result.items()
                if key not in {"tests", "failures_by_mutant", "call_assertions_by_mutant"}
            },
            indent=2,
        )
    )
