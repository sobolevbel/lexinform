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
    telegram_poll: bool
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


def one_run(
    read: Read, workflow: str, event: str, target: str, *, not_before: str | None = None
) -> dict[str, Any] | None:
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
    if not_before is not None:
        for run in matches:
            created = run.get("created_at")
            if not isinstance(created, str) or API_DATE.fullmatch(created) is None:
                raise EvidenceError("invalid_workflow_timestamp")
        eligible = [run for run in matches if run["created_at"] >= not_before]
        if matches and not eligible:
            raise EvidenceError("deploy_predates_ci_completion")
        matches = eligible
    if len(matches) > 1:
        raise EvidenceError("ambiguous_workflow_runs")
    return matches[0] if matches else None


def run_summary(read: Read, run: dict[str, Any]) -> dict[str, Any]:
    identity = run.get("id")
    attempt = run.get("run_attempt")
    if type(identity) is not int or identity <= 0 or type(attempt) is not int or attempt <= 0:
        raise EvidenceError("invalid_run_identity")
    try:
        jobs = items_at(
            read, f"actions/runs/{identity}/attempts/{attempt}/jobs?per_page=100", "jobs"
        )
    except EvidenceError as exc:
        if str(exc) != "github_read_failed":
            raise
        jobs = []
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
                if job.get("name") in ("docs", "web", "lint", "test (3.14)", "deploy")
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
    if run["conclusion"] not in (None, "success") or any(
        job["conclusion"] not in (None, "success") for job in jobs
    ):
        return "failed"
    if (
        run["conclusion"] is None
        or not jobs
        or any(job["status"] != "completed" or job["conclusion"] is None for job in jobs)
    ):
        return "pending"
    names = [job["name"] for job in jobs]
    if deploy:
        return "verified" if names == ["deploy"] else "unproven"
    expected = {"docs", "web", "test (3.14)"}
    return (
        "verified"
        if len(names) == len(set(names)) and set(names) in (expected, expected | {"lint"})
        else "unproven"
    )


def relay_proof(
    log: str,
    target: str,
    previous: str | None,
    *,
    mode: Literal["normal", "interrupted", "retry"] = "normal",
) -> Proof:
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
    readiness = [
        i for i, line in enumerate(lines) if line == "lexinform-listen accepted first Telegram poll"
    ]
    timers = [i for i, line in enumerate(lines) if line == "lexinform-batch-poll.timer enabled"]
    skipped = any(line.startswith(("skip:", "already at ")) for line in lines)
    proof: Proof = {
        "commit_sha": commits[0][1] if len(commits) == 1 else None,
        "transition_from": transitions[0][1][0] if len(transitions) == 1 else None,
        "transition_to": transitions[0][1][1] if len(transitions) == 1 else None,
        "restart45": len(health) == 1,
        "telegram_poll": len(readiness) == 1,
        "timer_enabled": len(timers) == 1,
        "skip_observed": skipped,
        "complete": False,
    }
    if len(commits) == len(transitions) == 1:
        old, new = transitions[0][1]
        expected_checkout = old == new if mode == "retry" else old != new
        transition_valid = (
            commits[0][1] == target
            and new == target[:12]
            and expected_checkout
            and (previous is None or old == previous[:12])
            and commits[0][0] < transitions[0][0]
            and not skipped
        )
        if mode == "interrupted":
            proof["complete"] = (
                transition_valid
                and not health
                and not timers
                and (not readiness or (len(readiness) == 1 and transitions[0][0] < readiness[0]))
            )
        else:
            proof["complete"] = (
                transition_valid
                and len(readiness) == len(health) == len(timers) == 1
                and transitions[0][0] < readiness[0] < health[0] < timers[0]
            )
    return proof


def relay_retry_proof(
    read: Read, deploy: dict[str, Any], log: str, target: str, previous: str | None
) -> Proof | None:
    if deploy["run_attempt"] != 2 or previous is None:
        return None
    current = relay_proof(log, target, target, mode="retry")
    if not current["complete"]:
        return None
    run_id = deploy["id"]
    first = object_at(read, f"actions/runs/{run_id}/attempts/1")
    expected = {
        "id": run_id,
        "run_attempt": 1,
        "head_sha": target,
        "head_branch": "main",
        "event": "workflow_run",
        "path": ".github/workflows/deploy-relay.yml",
        "status": "completed",
        "conclusion": "failure",
    }
    if any(first.get(key) != value for key, value in expected.items()):
        return None
    prior = run_summary(read, first)
    if not prior["created_at"] <= prior["updated_at"] <= deploy["updated_at"]:
        return None
    jobs = prior["jobs"]
    if len(jobs) != 1 or (jobs[0]["name"], jobs[0]["status"], jobs[0]["conclusion"]) != (
        "deploy",
        "completed",
        "failure",
    ):
        return None
    first_job = jobs[0]["id"]
    if first_job == deploy["jobs"][0]["id"]:
        return None
    transition = relay_proof(
        read(f"actions/jobs/{first_job}/logs"), target, previous, mode="interrupted"
    )
    if not transition["complete"]:
        return None
    current["transition_from"] = transition["transition_from"]
    return current


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
            ci = report["ci"]
            boundary = ci["updated_at"] if key == "deploy" and ci is not None else None
            run = one_run(read, workflow, event, target, not_before=boundary)
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
                reason = (
                    "metadata_pending"
                    if state == "pending" and summary["status"] == "completed"
                    else f"{key}_not_successful"
                )
                report.update({"status": state, "reason": reason})
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
        log = read(f"actions/jobs/{identity}/logs")
        report["proof"] = relay_proof(log, target, previous)
        recovered = False
        if not report["proof"]["complete"]:
            retry = relay_retry_proof(read, deploy, log, target, previous)
            if retry is not None:
                report["proof"] = retry
                recovered = True
        if not report["proof"]["complete"]:
            reason = (
                "relay_telegram_readiness_unproven"
                if report["proof"]["restart45"] and not report["proof"]["telegram_poll"]
                else "relay_transition_health_timer_unproven"
            )
            raise EvidenceError(reason)
        main_object = object_at(read, "git/ref/heads/main").get("object")
        final_main = main_object.get("sha") if isinstance(main_object, dict) else None
        if not isinstance(final_main, str) or SHA.fullmatch(final_main) is None:
            raise EvidenceError("invalid_main_identity")
        report["observed_main_sha"] = final_main
        report.update(
            {
                "status": "verified" if final_main == target else "superseded",
                "reason": (
                    "exact_release_retry_confirmed" if recovered else "exact_release_confirmed"
                )
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
    waiting = parser.add_mutually_exclusive_group()
    waiting.add_argument("--wait-seconds", type=int, choices=range(61), default=0, metavar="0..60")
    waiting.add_argument(
        "--wait-timeout",
        type=int,
        choices=range(1, 3601),
        metavar="1..3600",
        help="Poll pending releases and failed GitHub reads for up to this many seconds.",
    )
    args = parser.parse_args()
    deadline = time.monotonic() + args.wait_timeout if args.wait_timeout is not None else None
    report = verify(args.target_sha, previous=args.previous_verified_sha)
    if report["status"] == "pending" and args.wait_seconds:
        time.sleep(args.wait_seconds)
        report = verify(args.target_sha, previous=args.previous_verified_sha)
    while deadline is not None and (
        report["status"] == "pending"
        or (report["status"], report["reason"]) == ("unproven", "github_read_failed")
    ):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(30, remaining))
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
