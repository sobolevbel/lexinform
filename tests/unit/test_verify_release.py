import argparse
import json
import subprocess
from pathlib import Path

import pytest
from tools.verify_release import EvidenceError, full_sha, gh_read, main, relay_proof, verify

TARGET = "563b3e805db04301ae18f5d5e9555d51a9639b19"
PREVIOUS = "7287059ab139" + "a" * 28
CI = 40683990897
DEPLOY = 40685055464
JOB = 122805151925
PROOF = (
    f"2026-10-28T12:05:58.2118724Z   COMMIT: {TARGET}\n"
    "2026-10-28T12:06:01.6968121Z 7287059ab139 -> 563b3e805db0\n"
    "2026-10-28T12:06:47.0363978Z lexinform-listen up 45 s after restart\n"
    "2026-10-28T12:06:47.4812543Z lexinform-batch-poll.timer enabled\n"
)


def run(identity: int, workflow: str, event: str) -> dict[str, object]:
    return {
        "id": identity,
        "run_attempt": 1,
        "head_sha": TARGET,
        "head_branch": "main",
        "path": f".github/workflows/{workflow}",
        "event": event,
        "status": "completed",
        "conclusion": "success",
        "created_at": "2026-10-28T12:05:54Z" if event == "workflow_run" else "2026-10-28T11:57:51Z",
        "updated_at": "2026-10-28T12:06:49Z" if event == "workflow_run" else "2026-10-28T12:05:51Z",
    }


def job(identity: int, name: str) -> dict[str, object]:
    return {"id": identity, "name": name, "status": "completed", "conclusion": "success"}


class GitHub:
    def __init__(self) -> None:
        self.main_sha = TARGET
        self.ci = [run(CI, "ci.yml", "push")]
        self.deploy = [run(DEPLOY, "deploy-relay.yml", "workflow_run")]
        self.ci_jobs = [job(1, "docs"), job(2, "web"), job(3, "test (3.14)")]
        self.deploy_jobs = [job(JOB, "deploy")]
        self.log = PROOF
        self.calls: list[str] = []

    def read(self, endpoint: str) -> str:
        self.calls.append(endpoint)
        if endpoint == "git/ref/heads/main":
            return json.dumps({"object": {"sha": self.main_sha}})
        if endpoint.startswith("actions/workflows/"):
            items = self.ci if "/ci.yml/" in endpoint else self.deploy
            return json.dumps({"total_count": len(items), "workflow_runs": items})
        if endpoint.endswith("/jobs?per_page=100"):
            items = self.ci_jobs if f"/{CI}/" in endpoint else self.deploy_jobs
            return json.dumps({"total_count": len(items), "jobs": items})
        assert endpoint == f"actions/jobs/{JOB}/logs", "only the chosen deploy job's logs are read"
        return self.log


@pytest.mark.parametrize("conclusion", ["success", "failure", "skipped"])
def test_separate_lint_job_must_finish_successfully(conclusion: str) -> None:
    github = GitHub()
    lint = job(4, "lint")
    lint["conclusion"] = conclusion
    github.ci_jobs.append(lint)

    report = verify(TARGET, previous=PREVIOUS, read=github.read)

    assert report["status"] == ("verified" if conclusion == "success" else "failed")


def test_duplicate_lint_jobs_cannot_verify() -> None:
    github = GitHub()
    github.ci_jobs.extend([job(4, "lint"), job(5, "lint")])

    report = verify(TARGET, previous=PREVIOUS, read=github.read)

    assert report["status"] == "unproven"


def test_current_known_valid_automatic_deploy_has_one_canonical_report() -> None:
    github = GitHub()

    report = verify(TARGET, previous=PREVIOUS, read=github.read)

    assert report["status"] == "verified"
    assert report["target_sha"] == report["observed_main_sha"] == TARGET
    assert report["proof"]["complete"]
    assert report["ci"] is not None, "the verified report requires an exact successful CI"
    assert report["deploy"] is not None, "the verified report requires an exact automatic deploy"
    assert report["ci"]["id"] == CI and report["deploy"]["id"] == DEPLOY
    assert set(report) == {
        "schema_version",
        "status",
        "reason",
        "target_sha",
        "observed_main_sha",
        "ci",
        "deploy",
        "proof",
    }
    assert not {"verified", "head_sha", "automatic_deploy"} & report.keys()


@pytest.mark.parametrize("incident", [40659234925, 40659847080])
def test_incident_false_greens_never_verify(incident: int) -> None:
    github = GitHub()
    github.deploy[0]["id"] = incident
    if incident == 40659234925:
        github.log = PROOF.replace("563b3e805db0", "1f4331a3b190").split("lexinform-listen")[0]
    else:
        github.log = f"COMMIT: {TARGET}\nalready at {TARGET[:12]}, lexinform-listen running\n"

    report = verify(TARGET, read=github.read)

    assert report["status"] == "unproven"
    assert not report["proof"]["complete"]


@pytest.mark.parametrize(
    "change",
    [
        "wrong_sha",
        "manual",
        "stale",
        "ambiguous",
        "failed_ci",
        "skipped_ci_job",
        "missing_ci_job",
        "pending",
        "no_deploy",
    ],
)
def test_missing_stale_ambiguous_or_unsuccessful_metadata_cannot_verify(change: str) -> None:
    github = GitHub()
    expected = "unproven"
    if change == "wrong_sha":
        github.deploy[0]["head_sha"] = "a" * 40
        expected = "pending"
    elif change == "manual":
        github.deploy[0]["event"] = "workflow_dispatch"
        expected = "pending"
    elif change == "stale":
        github.deploy[0]["created_at"] = "2026-10-28T11:00:00Z"
    elif change == "ambiguous":
        github.deploy.append(run(99, "deploy-relay.yml", "workflow_run"))
    elif change == "failed_ci":
        github.ci[0]["conclusion"] = "failure"
        expected = "failed"
    elif change == "skipped_ci_job":
        github.ci_jobs[0]["conclusion"] = "skipped"
        expected = "failed"
    elif change == "missing_ci_job":
        github.ci_jobs.pop()
    elif change == "pending":
        github.ci[0]["status"] = "in_progress"
        github.ci[0]["conclusion"] = None
        expected = "pending"
    else:
        github.deploy.clear()
        expected = "pending"

    assert verify(TARGET, read=github.read)["status"] == expected


@pytest.mark.parametrize(
    "change",
    [
        "no_health",
        "no_timer",
        "wrong_commit",
        "same_head",
        "wrong_previous",
        "duplicate",
        "reordered",
        "echo_only",
        "skip",
    ],
)
def test_strict_filtered_proof_rejects_missing_ambiguous_or_echoed_lines(change: str) -> None:
    log = PROOF
    previous = PREVIOUS
    if change == "no_health":
        log = "\n".join(line for line in log.splitlines() if "up 45 s" not in line)
    elif change == "no_timer":
        log = "\n".join(line for line in log.splitlines() if "timer enabled" not in line)
    elif change == "wrong_commit":
        log = log.replace(f"COMMIT: {TARGET}", "COMMIT: " + "a" * 40)
    elif change == "same_head":
        log = log.replace("7287059ab139", TARGET[:12])
    elif change == "wrong_previous":
        previous = "b" * 40
    elif change == "duplicate":
        log += log
    elif change == "reordered":
        log = "\n".join(reversed(log.splitlines()))
    elif change == "echo_only":
        log = "\n".join(f'echo "{line}"' for line in log.splitlines())
    else:
        log += "skip: CI passed old, waiting for CI on origin/main new\n"

    assert not relay_proof(log, TARGET, previous)["complete"]


def test_main_advancing_during_the_observation_is_superseded() -> None:
    github = GitHub()

    def read(endpoint: str) -> str:
        if endpoint == "git/ref/heads/main" and github.calls:
            github.main_sha = "a" * 40
        return github.read(endpoint)

    assert verify(TARGET, read=read)["status"] == "superseded"


def test_only_filtered_metadata_leaves_the_observer() -> None:
    github = GitHub()
    github.log += "secret-token /private/state.sql https://private.example\n"
    report = verify(TARGET, read=github.read)
    assert report["status"] == "verified"
    assert all(
        value not in json.dumps(report)
        for value in ("secret-token", "state.sql", "private.example")
    )


def test_github_read_uses_get_only_and_never_returns_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def execute(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        assert kwargs["timeout"] == 60
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="SECRET")

    monkeypatch.setattr("tools.verify_release.subprocess.run", execute)
    with pytest.raises(EvidenceError, match="^github_read_failed$"):
        gh_read("git/ref/heads/main")
    assert commands == [
        ["gh", "api", "--method", "GET", "repos/sobolevbel/lexinform/git/ref/heads/main"]
    ]


def test_job_log_transport_accepts_ansi_only_in_captured_memory(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    raw = "\x1b[36;1mecho private workflow commands\x1b[0m\n" + PROOF

    def execute(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs["capture_output"] is True and kwargs["text"] is True
        if "--allow-escape-sequences" not in command:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="terminal protection")
        assert command == [
            "gh",
            "api",
            "--method",
            "GET",
            "--allow-escape-sequences",
            f"repos/sobolevbel/lexinform/actions/jobs/{JOB}/logs",
        ]
        return subprocess.CompletedProcess(command, 0, stdout=raw, stderr="")

    monkeypatch.setattr("tools.verify_release.subprocess.run", execute)
    captured = gh_read(f"actions/jobs/{JOB}/logs")
    proof = relay_proof(captured, TARGET, PREVIOUS)

    assert proof["complete"]
    assert "\x1b" not in json.dumps(proof) and "private" not in json.dumps(proof)
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("value", ["563b3e8", "main", "A" * 40, "a" * 39, "a" * 41])
def test_only_full_commit_arguments_are_accepted(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        full_sha(value)


def test_cli_writes_new_artifacts_only_and_snapshot_is_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    github = GitHub()
    report = verify(TARGET, read=github.read)
    artifact = tmp_path / "release.json"
    monkeypatch.setattr("sys.argv", ["verify_release.py", TARGET, "--output", str(artifact)])
    monkeypatch.setattr("tools.verify_release.verify", lambda *args, **kwargs: report)
    assert main() == 0
    assert json.loads(artifact.read_text())["status"] == "verified"
    saved = artifact.read_bytes()
    assert main() == 1
    assert artifact.read_bytes() == saved
    assert json.loads(capsys.readouterr().out)["reason"] == "output_not_created"


def test_cli_waits_at_most_once_for_pending_and_default_never_sleeps(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    github = GitHub()
    github.deploy.clear()
    report = verify(TARGET, read=github.read)
    waits: list[int] = []
    calls: list[str] = []

    def snapshot(target: str, *, previous: str | None) -> dict[str, object]:
        calls.append(target)
        return dict(report)

    monkeypatch.setattr("tools.verify_release.verify", snapshot)
    monkeypatch.setattr("tools.verify_release.time.sleep", waits.append)
    monkeypatch.setattr("sys.argv", ["verify_release.py", TARGET])
    assert main() == 1
    assert not waits and calls == [TARGET]
    assert json.loads(capsys.readouterr().out)["status"] == "pending"
    calls.clear()
    monkeypatch.setattr("sys.argv", ["verify_release.py", TARGET, "--wait-seconds", "60"])
    assert main() == 1
    assert waits == [60] and calls == [TARGET, TARGET]
    assert json.loads(capsys.readouterr().out)["status"] == "pending"


@pytest.mark.parametrize("body", ["[]", '{"object": null}', '{"object": {"sha": true}}'])
def test_malformed_github_identity_is_unproven_without_raw_response(body: str) -> None:
    report = verify(TARGET, read=lambda endpoint: body)
    assert report["status"] == "unproven"
    assert report["observed_main_sha"] is None
