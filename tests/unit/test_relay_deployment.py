import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

UNITS = (
    "lexinform-listen.service",
    "lexinform-batch-poll.service",
    "lexinform-batch-poll.timer",
)

UV_STUB = """#!/bin/bash
set -eu
printf 'uv %s downloads=%s builds=%s installs=%s\\n' "$*" "${UV_CONCURRENT_DOWNLOADS:-}" "${UV_CONCURRENT_BUILDS:-}" "${UV_CONCURRENT_INSTALLS:-}" >> "$FAKE_ROOT/events"
printf 'proof uv:%s\\n' "$(cat "$FAKE_CHECKOUT/.git/lexinform-relay-deployed" 2>/dev/null || true)" >> "$FAKE_ROOT/events"
if [ "${FAKE_UV_FAILURE:-}" = term ]; then kill -TERM "$$"; fi
if [ "${FAKE_UV_FAILURE:-}" = exit ]; then exit 17; fi
"""

SYSTEMCTL_STUB = """#!/bin/bash
set -eu
printf 'systemctl %s\\n' "$*" >> "$FAKE_ROOT/events"
printf 'proof %s:%s\\n' "$1" "$(cat "$FAKE_CHECKOUT/.git/lexinform-relay-deployed" 2>/dev/null || true)" >> "$FAKE_ROOT/events"
if [ "$*" = "${FAKE_SYSTEMCTL_FAILURE:-}" ]; then exit 19; fi
case "$1" in
  is-active)
    count=$(cat "$FAKE_ROOT/sleeps")
    if [ "${FAKE_HEALTH_FAILURE_AFTER:-0}" -gt 0 ] && [ "$count" -ge "$FAKE_HEALTH_FAILURE_AFTER" ]; then exit 1; fi
    test -f "$FAKE_ROOT/active"
    ;;
  restart) touch "$FAKE_ROOT/active" ;;
  is-enabled) test -f "$FAKE_ROOT/enabled" ;;
  enable) touch "$FAKE_ROOT/enabled" ;;
  daemon-reload|status) ;;
  *) exit 97 ;;
esac
"""

SLEEP_STUB = """#!/bin/bash
set -eu
printf 'sleep %s\\n' "$*" >> "$FAKE_ROOT/events"
count=$(cat "$FAKE_ROOT/sleeps")
printf '%s\\n' "$((count+1))" > "$FAKE_ROOT/sleeps"
"""


def git(directory: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=Deploy test", "-c", "user.email=deploy@example.test", *args],
        cwd=directory,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"},
        text=True,
        capture_output=True,
        check=True,
        timeout=15,
    )
    return result.stdout.strip()


@dataclass(frozen=True)
class Deployment:
    root: Path
    seed: Path
    checkout: Path
    units: Path
    executable: Path
    bin: Path

    @property
    def marker(self) -> Path:
        return self.checkout / ".git/lexinform-relay-deployed"

    @property
    def pending(self) -> Path:
        return self.checkout / ".git/lexinform-relay-deploy-pending"

    @property
    def head(self) -> str:
        return git(self.checkout, "rev-parse", "HEAD")

    @property
    def events(self) -> list[str]:
        return (self.root / "events").read_text().splitlines()

    def advance(self, *, unit: str | None = None) -> str:
        if unit is not None:
            path = self.seed / "deploy" / unit
            path.write_text(path.read_text() + "\nUpdated unit\n")
        (self.seed / "release").write_text(git(self.seed, "rev-parse", "HEAD"))
        git(self.seed, "add", ".")
        git(self.seed, "commit", "-qm", "Next deployment")
        git(self.seed, "push", "-q", "origin", "main")
        return git(self.seed, "rev-parse", "HEAD")

    def run(self, **overrides: str) -> subprocess.CompletedProcess[str]:
        (self.root / "events").write_text("")
        (self.root / "sleeps").write_text("0\n")
        return subprocess.run(
            ["bash", str(self.executable)],
            cwd=self.root,
            env={
                "PATH": f"{self.bin}{os.pathsep}{os.defpath}",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
                "FAKE_ROOT": str(self.root),
                "FAKE_CHECKOUT": str(self.checkout),
                **overrides,
            },
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )


@pytest.fixture
def deployment(tmp_path: Path) -> Deployment:
    project = Path(__file__).resolve().parents[2]
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    seed.mkdir()
    git(tmp_path, "init", "-q", "--bare", "--initial-branch=main", str(remote))
    git(seed, "init", "-q", "--initial-branch=main")
    (seed / "deploy").mkdir()
    units = tmp_path / "units"
    units.mkdir()
    for unit in UNITS:
        shutil.copyfile(project / "deploy" / unit, seed / "deploy" / unit)
        shutil.copyfile(project / "deploy" / unit, units / unit)
    git(seed, "add", ".")
    git(seed, "commit", "-qm", "Initial deployment")
    git(seed, "remote", "add", "origin", str(remote))
    git(seed, "push", "-q", "origin", "main")
    checkout = tmp_path / "checkout"
    git(tmp_path, "clone", "-q", str(remote), str(checkout))
    binaries = tmp_path / "bin"
    binaries.mkdir()
    for name, contents in (("uv", UV_STUB), ("systemctl", SYSTEMCTL_STUB), ("sleep", SLEEP_STUB)):
        executable = binaries / name
        executable.write_text(contents)
        executable.chmod(0o755)
    source = (project / "deploy/update.sh").read_text()
    for before, after in (
        ("cd /opt/lexinform", f"cd {shlex.quote(str(checkout))}"),
        ("UV=/root/.local/bin/uv", f"UV={shlex.quote(str(binaries / 'uv'))}"),
        ("/etc/systemd/system", shlex.quote(str(units))),
    ):
        assert before in source, "the harness must replace each privileged deployment path"
        source = source.replace(before, after)
    # The documented SSH command is compared as data and must retain its literal spelling.
    literal_comparison = 'if [ "$wanted" = "/opt/lexinform/deploy/update.sh" ]; then'
    commands = "\n".join(
        line
        for line in source.splitlines()
        if not line.lstrip().startswith("#") and line.strip() != literal_comparison
    )
    assert "/opt/lexinform" not in commands, "the harness cannot execute a real deployment path"
    script = tmp_path / "update.sh"
    script.write_text(source)
    (tmp_path / "active").touch()
    (tmp_path / "enabled").touch()
    return Deployment(tmp_path, seed, checkout, units, script, binaries)


def assert_installed(deployment: Deployment, expected: str) -> None:
    assert deployment.marker.read_text() == expected + "\n"
    assert not deployment.pending.exists()
    assert sum(event.startswith("uv sync --frozen --no-dev") for event in deployment.events) == 1
    assert "systemctl restart lexinform-listen" in deployment.events
    assert deployment.events.count("sleep 5") == 9


@pytest.mark.parametrize("marker", ["absent", "older"])
def test_matching_checkout_without_successful_deployment_proof_is_installed(
    deployment: Deployment, marker: str
) -> None:
    if marker == "older":
        deployment.marker.write_text("0" * 40 + "\n")
    before = deployment.head

    result = deployment.run(
        UV_CONCURRENT_DOWNLOADS="9", UV_CONCURRENT_BUILDS="8", UV_CONCURRENT_INSTALLS="7"
    )

    assert result.returncode == 0, result.stderr
    assert deployment.head == before
    assert_installed(deployment, before)
    assert any("downloads=1 builds=1 installs=1" in event for event in deployment.events)


@pytest.mark.parametrize("failure", ["exit", "term"])
def test_failed_sync_after_checkout_advance_is_retried_at_the_same_head(
    deployment: Deployment, failure: str
) -> None:
    old = deployment.head
    deployment.marker.write_text(old + "\n")
    target = deployment.advance()

    failed = deployment.run(FAKE_UV_FAILURE=failure)

    assert failed.returncode != 0
    assert deployment.head == target
    assert deployment.marker.read_text() == old + "\n"
    assert deployment.pending.exists()
    assert "systemctl restart lexinform-listen" not in deployment.events

    retried = deployment.run()

    assert retried.returncode == 0, retried.stderr
    assert_installed(deployment, target)
    assert all(
        event.endswith(":" + old) for event in deployment.events if event.startswith("proof ")
    )


@pytest.mark.parametrize("stage", ["restart", "health", "timer"])
def test_later_failure_keeps_success_marker_and_requires_complete_retry(
    deployment: Deployment, stage: str
) -> None:
    old = deployment.head
    deployment.marker.write_text(old + "\n")
    target = deployment.advance()
    failures = {
        "restart": {"FAKE_SYSTEMCTL_FAILURE": "restart lexinform-listen"},
        "health": {"FAKE_HEALTH_FAILURE_AFTER": "3"},
        "timer": {"FAKE_SYSTEMCTL_FAILURE": "enable --now lexinform-batch-poll.timer"},
    }
    if stage == "timer":
        (deployment.root / "enabled").unlink()

    failed = deployment.run(**failures[stage])

    assert failed.returncode != 0
    assert deployment.marker.read_text() == old + "\n"
    assert deployment.pending.exists()
    if stage == "health":
        assert deployment.events.count("sleep 5") == 3
        assert "systemctl status lexinform-listen --no-pager -l" in deployment.events

    retried = deployment.run()

    assert retried.returncode == 0, retried.stderr
    assert_installed(deployment, target)


def test_failed_reload_after_unit_copy_is_retried_despite_identical_unit_files(
    deployment: Deployment,
) -> None:
    old = deployment.head
    deployment.marker.write_text(old + "\n")
    target = deployment.advance(unit=UNITS[0])

    failed = deployment.run(FAKE_SYSTEMCTL_FAILURE="daemon-reload")

    assert failed.returncode != 0
    assert deployment.marker.read_text() == old + "\n" and deployment.pending.exists()
    assert (deployment.units / UNITS[0]).read_bytes() == (
        deployment.checkout / "deploy" / UNITS[0]
    ).read_bytes()

    retried = deployment.run()

    assert retried.returncode == 0, retried.stderr
    assert "systemctl daemon-reload" in deployment.events
    assert_installed(deployment, target)


def test_failed_same_head_unit_repair_cannot_reuse_a_previous_success_marker(
    deployment: Deployment,
) -> None:
    current = deployment.head
    deployment.marker.write_text(current + "\n")
    (deployment.units / UNITS[0]).write_text("stale unit\n")

    failed = deployment.run(FAKE_SYSTEMCTL_FAILURE="daemon-reload")

    assert failed.returncode != 0
    assert deployment.marker.read_text() == current + "\n" and deployment.pending.exists()

    retried = deployment.run()

    assert retried.returncode == 0, retried.stderr
    assert "systemctl daemon-reload" in deployment.events
    assert_installed(deployment, current)


def test_healthy_verified_deployment_skips_install_restart_and_health_wait(
    deployment: Deployment,
) -> None:
    deployment.marker.write_text(deployment.head + "\n")

    result = deployment.run(FAKE_UV_FAILURE="exit", FAKE_SYSTEMCTL_FAILURE="daemon-reload")

    assert result.returncode == 0, result.stderr
    assert "already at" in result.stdout
    assert not any(event.startswith(("uv ", "sleep ")) for event in deployment.events)
    assert "systemctl restart lexinform-listen" not in deployment.events
    assert not deployment.pending.exists()


@pytest.mark.parametrize("unit", UNITS)
def test_changed_units_force_a_complete_deployment_even_at_a_verified_head(
    deployment: Deployment, unit: str
) -> None:
    deployment.marker.write_text(deployment.head + "\n")
    (deployment.units / unit).write_text("stale unit\n")

    result = deployment.run()

    assert result.returncode == 0, result.stderr
    assert_installed(deployment, deployment.head)
    assert "systemctl daemon-reload" in deployment.events
    assert (deployment.units / unit).read_bytes() == (
        deployment.checkout / "deploy" / unit
    ).read_bytes()


def test_inactive_service_forces_installation_despite_a_matching_success_marker(
    deployment: Deployment,
) -> None:
    deployment.marker.write_text(deployment.head + "\n")
    (deployment.root / "active").unlink()

    result = deployment.run()

    assert result.returncode == 0, result.stderr
    assert_installed(deployment, deployment.head)


def test_stale_ci_commit_cannot_reset_checkout_or_modify_runtime_units(
    deployment: Deployment,
) -> None:
    verified = deployment.head
    deployment.marker.write_text(verified + "\n")
    original_units = {unit: (deployment.units / unit).read_bytes() for unit in UNITS}
    newer = deployment.advance(unit=UNITS[0])
    assert newer != verified, "the remote advanced beyond the commit whose CI triggered deployment"

    result = deployment.run(SSH_ORIGINAL_COMMAND=verified)

    assert result.returncode == 0, result.stderr
    assert deployment.head == verified
    assert deployment.marker.read_text() == verified + "\n"
    assert not deployment.pending.exists()
    assert deployment.events == []
    assert {unit: (deployment.units / unit).read_bytes() for unit in UNITS} == original_units


def test_documented_manual_ssh_command_installs_an_unverified_matching_checkout(
    deployment: Deployment,
) -> None:
    before = deployment.head

    result = deployment.run(SSH_ORIGINAL_COMMAND="/opt/lexinform/deploy/update.sh")

    assert result.returncode == 0, result.stderr
    assert deployment.head == before
    assert_installed(deployment, before)
