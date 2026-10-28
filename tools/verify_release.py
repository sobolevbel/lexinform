"""Verify one main release through read-only GitHub metadata and narrowly filtered relay proof."""

import argparse
import json
import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, TypedDict

REPOSITORY = "sobolevbel/lexinform"
SHA = re.compile(r"[0-9a-f]{40}")
TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z\s+")
API_DATE = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
Status = Literal["pending", "verified", "failed", "superseded", "unproven"]
Read = Callable[[str], str]


class Proof(TypedDict):
    commit_sha: str | None
    transition_from: str | None
    transition_to: str | None
    restart45: bool
    timer_enabled: bool
    skip_observed: bool
    complete: bool


class Report(TypedDict):
    schema_version: int
    status: Status
    reason: str
    target_sha: str
    observed_main_sha: str | None
    ci: dict[str, Any] | None
    deploy: dict[str, Any] | None
    proof: Proof


class EvidenceError(Exception):
    pass


def full_sha(value: str) -> str:
    if SHA.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("a full lowercase 40-character commit SHA is required")
    return value


def gh_read(endpoint: str) -> str:
    options = (
        ["--allow-escape-sequences"]
        if re.fullmatch(r"actions/jobs/[1-9][0-9]*/logs", endpoint)
        else []
    )
    try:
        result = subprocess.run(
            ["gh", "api", "--method", "GET", *options, f"repos/{REPOSITORY}/{endpoint}"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EvidenceError("github_read_failed") from exc
    if result.returncode:
        raise EvidenceError("github_read_failed")
    return result.stdout


def object_at(read: Read, endpoint: str) -> dict[str, Any]:
    try:
        result = json.loads(read(endpoint))
    except ValueError as exc:
        raise EvidenceError("invalid_github_response") from exc
    if not isinstance(result, dict):
        raise EvidenceError("invalid_github_response")
    return result


def items_at(read: Read, endpoint: str, key: str) -> list[dict[str, Any]]:
    body = object_at(read, endpoint)
    items = body.get(key)
    count = body.get("total_count")
    if (
        type(count) is not int
        or not isinstance(items, list)
        or count != len(items)
        or count > 100
        or not all(isinstance(item, dict) for item in items)
    ):
        raise EvidenceError("incomplete_github_listing")
    return items


def one_run(read: Read, workflow: str, event: str, target: str) -> dict[str, Any] | None:
    runs = items_at(
        read,
        f"actions/workflows/{workflow}/runs?branch=main&event={event}&head_sha={target}&per_page=100",
        "workflow_runs",
    )
    matches = [
        run
        for run in runs
        if run.get("head_sha") == target
        and run.get("head_branch") == "main"
        and run.get("event") == event
        and run.get("path") == f".github/workflows/{workflow}"
    ]
    if len(matches) > 1:
        raise EvidenceError("ambiguous_workflow_runs")
    return matches[0] if matches else None


def run_summary(read: Read, run: dict[str, Any]) -> dict[str, Any]:
    identity = run.get("id")
    attempt = run.get("run_attempt")
    if type(identity) is not int or identity <= 0 or type(attempt) is not int or attempt <= 0:
        raise EvidenceError("invalid_run_identity")
    jobs = items_at(read, f"actions/runs/{identity}/attempts/{attempt}/jobs?per_page=100", "jobs")
    for item in [run, *jobs]:
        if item.get("status") not in (
            "queued",
            "in_progress",
            "completed",
            "waiting",
            "pending",
            "requested",
        ) or item.get("conclusion") not in (
            None,
            "success",
            "failure",
            "cancelled",
            "skipped",
            "timed_out",
            "action_required",
            "neutral",
            "stale",
            "startup_failure",
        ):
            raise EvidenceError("invalid_workflow_state")
    for key in ("created_at", "updated_at"):
        if not isinstance(run.get(key), str) or API_DATE.fullmatch(run[key]) is None:
            raise EvidenceError("invalid_workflow_timestamp")
    for job in jobs:
        if type(job.get("id")) is not int or job["id"] <= 0:
            raise EvidenceError("invalid_job_identity")
    return {
        "id": identity,
        "head_sha": run["head_sha"],
        "event": run["event"],
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "run_attempt": attempt,
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
        "jobs": [
            {
                "id": job["id"],
                "name": job.get("name")
                if job.get("name") in ("docs", "web", "test (3.14)", "deploy")
                else "unexpected_job",
                "status": job["status"],
                "conclusion": job.get("conclusion"),
            }
            for job in jobs
        ],
    }


def run_state(run: dict[str, Any], *, deploy: bool) -> Status:
    jobs = run["jobs"]
    if run["status"] != "completed":
        return "pending"
    if run["conclusion"] != "success" or any(job["conclusion"] != "success" for job in jobs):
        return "failed"
    if any(job["status"] != "completed" for job in jobs):
        return "pending"
    names = [job["name"] for job in jobs]
    if deploy:
        return "verified" if names == ["deploy"] else "unproven"
    expected = {"docs", "web", "test (3.14)"}
    return "verified" if len(names) == len(expected) and set(names) == expected else "unproven"


def relay_proof(log: str, target: str, previous: str | None) -> Proof:
    lines = [TIMESTAMP.sub("", line).strip() for line in log.splitlines()]
    commits = [
        (i, line[8:]) for i, line in enumerate(lines) if re.fullmatch(r"COMMIT: [0-9a-f]{40}", line)
    ]
    transitions = [
        (i, line.split(" -> "))
        for i, line in enumerate(lines)
        if re.fullmatch(r"[0-9a-f]{12} -> [0-9a-f]{12}", line)
    ]
    health = [i for i, line in enumerate(lines) if line == "lexinform-listen up 45 s after restart"]
    timers = [i for i, line in enumerate(lines) if line == "lexinform-batch-poll.timer enabled"]
    skipped = any(line.startswith(("skip:", "already at ")) for line in lines)
    proof: Proof = {
        "commit_sha": commits[0][1] if len(commits) == 1 else None,
        "transition_from": transitions[0][1][0] if len(transitions) == 1 else None,
        "transition_to": transitions[0][1][1] if len(transitions) == 1 else None,
        "restart45": len(health) == 1,
        "timer_enabled": len(timers) == 1,
        "skip_observed": skipped,
        "complete": False,
    }
    if len(commits) == len(transitions) == len(health) == len(timers) == 1:
        old, new = transitions[0][1]
        proof["complete"] = (
            commits[0][1] == target
            and new == target[:12]
            and old != new
            and (previous is None or old == previous[:12])
            and commits[0][0] < transitions[0][0] < health[0] < timers[0]
            and not skipped
        )
    return proof


def verify(target: str, *, previous: str | None = None, read: Read = gh_read) -> Report:
    full_sha(target)
    if previous is not None:
        full_sha(previous)
    report: Report = {
        "schema_version": 1,
        "status": "unproven",
        "reason": "missing_evidence",
        "target_sha": target,
        "observed_main_sha": None,
        "ci": None,
        "deploy": None,
        "proof": relay_proof("", target, previous),
    }
    try:
        main_object = object_at(read, "git/ref/heads/main").get("object")
        main = main_object.get("sha") if isinstance(main_object, dict) else None
        if not isinstance(main, str) or SHA.fullmatch(main) is None:
            raise EvidenceError("invalid_main_identity")
        report["observed_main_sha"] = main
        if main != target:
            report.update({"status": "superseded", "reason": "main_differs_from_target"})
            return report
        for key, workflow, event in (
            ("ci", "ci.yml", "push"),
            ("deploy", "deploy-relay.yml", "workflow_run"),
        ):
            run = one_run(read, workflow, event, target)
            if run is None:
                report.update({"status": "pending", "reason": f"no_exact_{key}_run"})
                return report
            summary = run_summary(read, run)
            if key == "ci":
                report["ci"] = summary
            else:
                report["deploy"] = summary
            state = run_state(summary, deploy=key == "deploy")
            if state != "verified":
                report.update({"status": state, "reason": f"{key}_not_successful"})
                return report
        ci, deploy = report["ci"], report["deploy"]
        assert ci is not None and deploy is not None, (
            "both exact successful runs were selected above"
        )
        if (
            not isinstance(ci["updated_at"], str)
            or not isinstance(deploy["created_at"], str)
            or deploy["created_at"] < ci["updated_at"]
        ):
            raise EvidenceError("deploy_predates_ci_completion")
        identity = deploy["jobs"][0]["id"]
        if type(identity) is not int or identity <= 0:
            raise EvidenceError("invalid_job_identity")
        report["proof"] = relay_proof(read(f"actions/jobs/{identity}/logs"), target, previous)
        if not report["proof"]["complete"]:
            raise EvidenceError("relay_transition_health_timer_unproven")
        main_object = object_at(read, "git/ref/heads/main").get("object")
        final_main = main_object.get("sha") if isinstance(main_object, dict) else None
        if not isinstance(final_main, str) or SHA.fullmatch(final_main) is None:
            raise EvidenceError("invalid_main_identity")
        report["observed_main_sha"] = final_main
        report.update(
            {
                "status": "verified" if final_main == target else "superseded",
                "reason": "exact_release_confirmed"
                if final_main == target
                else "main_changed_during_observation",
            }
        )
    except EvidenceError as exc:
        report.update({"status": "unproven", "reason": str(exc)})
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target_sha", type=full_sha)
    parser.add_argument("--previous-verified-sha", type=full_sha)
    parser.add_argument("--output", type=Path, help="Create a new JSON artifact; never overwrite.")
    parser.add_argument("--wait-seconds", type=int, choices=range(61), default=0, metavar="0..60")
    args = parser.parse_args()
    report = verify(args.target_sha, previous=args.previous_verified_sha)
    if report["status"] == "pending" and args.wait_seconds:
        time.sleep(args.wait_seconds)
        report = verify(args.target_sha, previous=args.previous_verified_sha)
    payload = json.dumps(report, indent=2) + "\n"
    if args.output is None:
        print(payload, end="")
    else:
        try:
            with args.output.open("x", encoding="utf-8") as output:
                output.write(payload)
        except OSError:
            report.update({"status": "unproven", "reason": "output_not_created"})
            print(json.dumps(report, indent=2))
    return 0 if report["status"] == "verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
