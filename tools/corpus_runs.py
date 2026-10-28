"""Keep immutable corpus runs and compare their observations through one offline suite registry."""

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from tools.corpus_artifacts import digest, observation_changes, read_artifact, write_json
from tools.corpus_runtime import capture_code, code_dirty, code_version, source_fingerprints

SUITES = {
    "health": ("corpus_health", ()),
    "ci": ("corpus_check", ()),
    "external": ("corpus_check", ("--suite", "external")),
    "pdf": ("replay_pdf_pages", ()),
    "word": ("replay_word_blocks", ()),
    "odt": ("replay_odt_blocks", ()),
    "input": ("replay_input_coverage", ()),
    "cost": ("replay_cost_inputs", ()),
    "amendments": ("replay_amended_inputs", ()),
    "reextract": ("corpus_derivatives", ()),
    "annotations": ("corpus_annotations", ()),
    "empty-review": ("corpus_empty_review", ()),
    "physical": ("corpus_physical", ()),
    "holdout": ("corpus_holdout", ()),
    "review-form": ("corpus_review", ("--mode", "form")),
    "review": ("corpus_review", ("--mode", "record")),
    "candidates": ("replay_document_candidates", ()),
    "admissibility": ("replay_admissibility", ()),
    "review-admissibility": ("replay_admissibility", ("--operation", "review")),
    "process": ("corpus_legacy_suites", ("--kind", "process")),
    "senate": ("corpus_legacy_suites", ("--kind", "senate")),
    "scan": ("replay_scan_inputs", ()),
}

RECORD_ONLY = ("sources", "install", "sejm-snapshot", "sejm-catalogue", "sejm-sources")

ReleaseProfile = Literal["extraction", "selection", "comparison", "delivery"]
RELEASE_MATRIX: dict[ReleaseProfile, tuple[str, ...]] = {
    "extraction": (
        "external-extraction",
        "pdf",
        "word",
        "odt",
        "scan",
        "input",
        "cost",
        "amendments",
    ),
    "selection": (
        "external-selection",
        "candidates",
        "admissibility",
        "review-admissibility",
        "process",
        "senate",
    ),
    "comparison": ("external-comparison",),
    "delivery": ("external-delivery",),
}
RELEASE_JOBS = {
    **{
        f"external-{profile}": ("external", ("--profiles", profile, "--mode", "baseline"))
        for profile in ("extraction", "selection", "comparison", "delivery")
    },
    "pdf": ("pdf", ("--page-reader", "adapter")),
    "word": ("word", ("--mode", "notes-parts")),
    "odt": ("odt", ("--mode", "adapter")),
    "scan": ("scan", ()),
    "input": ("input", ("--mode", "compare")),
    "cost": ("cost", ("--mapped",)),
    "amendments": ("amendments", ("--mapped",)),
    **{
        name: (name, ())
        for name in ("candidates", "admissibility", "review-admissibility", "process", "senate")
    },
}
PIN_FLAGS = (
    "--cases",
    "--catalog",
    "--pdf-cases",
    "--inventory",
    "--manifest",
    "--reference",
    "--derivatives",
)
SHA_FLAGS = {flag + "-sha256": flag for flag in PIN_FLAGS if flag not in ("--cases", "--pdf-cases")}
RELEASE_FLAGS = (*PIN_FLAGS, *SHA_FLAGS, "--prepared-manifest", "--extraction-manifest")
REQUIRED_RELEASE_FLAGS = {
    "pdf": ("--cases", "--reference", "--reference-sha256"),
    "word": ("--catalog", "--catalog-sha256", "--reference", "--reference-sha256"),
    "odt": ("--cases", "--reference", "--reference-sha256"),
    "scan": ("--manifest", "--manifest-sha256"),
    "input": ("--catalog", "--catalog-sha256", "--pdf-cases", "--reference", "--reference-sha256"),
    "cost": ("--inventory", "--inventory-sha256"),
    "amendments": ("--inventory", "--inventory-sha256"),
}


class ReleasePin(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ReleaseJob(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")
    suite: str
    args: tuple[str, ...] = ()
    pins: tuple[ReleasePin, ...] = Field(min_length=1)
    expected_observations: int = Field(gt=0)
    expected_ids_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ReleasePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: Literal[1]
    index_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    jobs: tuple[ReleaseJob, ...]


def observation_ids_sha256(rows: dict[str, Any]) -> str:
    return digest(json.dumps(sorted(rows), separators=(",", ":")).encode())


def controlled_options(suite: str, extra: list[str], owned: tuple[str, ...] = ()) -> None:
    controlled = (
        "--output",
        "--corpus",
        "--suite",
        *owned,
        *(arg for arg in SUITES[suite][1] if arg.startswith("--")),
    )
    if any(
        arg == "--"
        or (
            arg.startswith("--")
            and any(reserved.startswith(arg.split("=", 1)[0]) for reserved in controlled)
        )
        for arg in extra
    ):
        raise ValueError("Corpus, output and suite are controlled by the run registry")


def observations(report: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(report, dict):
        raise ValueError("Report must be a JSON object")
    for field in ("observations", "records", "results"):
        if isinstance(report.get(field), dict):
            return dict(report[field])
    if isinstance(report.get("cases"), list):
        return {row["id"]: row for row in report["cases"]}
    raise ValueError("Report has no supported observation collection")


def new_run(root: Path, identity: str, suite: str) -> Path:
    if suite not in (*SUITES, *RECORD_ONLY) or not re.fullmatch(
        r"[a-z0-9][a-z0-9_-]{0,79}", identity
    ):
        raise ValueError("Unknown suite or invalid run ID")
    resolved = root.resolve()
    if root.absolute() != resolved:
        raise ValueError("Run root must not contain symlinks")
    resolved.mkdir(parents=True, exist_ok=True)
    directory = resolved / identity
    directory.mkdir()
    return directory


def write_started(directory: Path, started: dict[str, Any]) -> None:
    if started.get("source_sha256"):
        started["code_snapshot"] = capture_code(directory, started["source_sha256"])
    write_json(directory / "started.json", started)


def finish(
    directory: Path, suite: str, *, returncode: int, started: dict[str, Any]
) -> dict[str, Any]:
    target = directory / "result.json"
    report: dict[str, Any] = {}
    error = ""
    try:
        report = json.loads(read_artifact(target))
        count = len(observations(report))
        if not count:
            error = "Requested suite produced no observations"
    except (OSError, ValueError, TypeError) as exc:
        count, error = 0, f"{type(exc).__name__}: {exc}"
    if "source_sha256" in started and started["source_sha256"] != source_fingerprints():
        returncode, error = 1, "Code changed during the run"
    status = (
        "failed"
        if returncode not in (0, 2) or report.get("status") == "failed"
        else "not_run"
        if error or returncode == 2 or report.get("status") == "not_run"
        else report.get("status")
        if report.get("status") in ("passed", "recorded")
        else "failed"
    )
    artifacts = {
        str(path.relative_to(directory)): {
            "sha256": digest(read_artifact(path)),
            "bytes": path.stat().st_size,
        }
        for path in directory.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    manifest = {
        "version": 1,
        "id": directory.name,
        "suite": suite,
        "status": status,
        "returncode": returncode,
        "observations": count,
        "error": error,
        "started": started,
        "finished_at": dt.datetime.now(dt.UTC).isoformat(),
        "producer": {
            key: report.get(key) for key in ("code_sha", "source_sha256", "scope", "exclusions")
        },
        "artifacts": artifacts,
    }
    write_json(directory / "manifest.json", manifest)
    with (directory / "manifest.sha256").open("x") as stream:
        stream.write(digest(read_artifact(directory / "manifest.json")) + "\n")
    return manifest


def execute(
    root: Path, identity: str, suite: str, corpus: Path, extra: list[str]
) -> dict[str, Any]:
    if suite not in SUITES:
        raise ValueError("Online or installation suites require their explicit collector")
    module, preset = SUITES[suite]
    controlled_options(suite, extra)
    if root.resolve().is_relative_to(corpus.resolve()):
        raise ValueError("Runs must be outside the corpus")
    directory = new_run(root, identity, suite)
    command = [
        sys.executable,
        "-m",
        f"tools.{module}",
        "--corpus",
        str(corpus.resolve()),
        "--output",
        str(directory / "result.json"),
        *preset,
        *extra,
    ]
    started = {
        "command": command,
        "corpus": str(corpus.resolve()),
        "code_sha": code_version(),
        "code_dirty": code_dirty(),
        "source_sha256": source_fingerprints(),
        "started_at": dt.datetime.now(dt.UTC).isoformat(),
        "network": "prohibited by offline suite",
    }
    write_started(directory, started)
    with (directory / "console.log").open("xb") as stream:
        try:
            process = subprocess.run(
                command,
                stdout=stream,
                stderr=subprocess.STDOUT,
                check=False,
                cwd=Path(__file__).resolve().parents[1],
            )
            returncode = process.returncode
        except OSError as exc:
            stream.write(f"{type(exc).__name__}: {exc}\n".encode())
            returncode = 1
    return finish(directory, suite, returncode=returncode, started=started)


def register(root: Path, identity: str, suite: str, artifact: Path) -> dict[str, Any]:
    data = read_artifact(artifact)
    report = json.loads(data)
    observations(report)
    directory = new_run(root, identity, suite)
    started = {
        "registration": True,
        "source_artifact": str(artifact.resolve()),
        "source_artifact_sha256": digest(data),
        "registered_at": dt.datetime.now(dt.UTC).isoformat(),
    }
    write_json(directory / "started.json", started)
    (directory / "result.json").write_bytes(data)
    return finish(directory, suite, returncode=0, started=started)


def verify(directory: Path) -> dict[str, Any]:
    root = directory.resolve(strict=True)
    manifest_path = root / "manifest.json"
    if manifest_path.is_symlink() or (root / "manifest.sha256").is_symlink():
        raise ValueError("Run manifest must not be a symlink")
    data = read_artifact(manifest_path)
    if digest(data) != (root / "manifest.sha256").read_text().strip():
        raise ValueError("Run manifest SHA mismatch")
    manifest: dict[str, Any] = json.loads(data)
    if manifest.get("version") != 1 or manifest.get("id") != root.name:
        raise ValueError("Run manifest identity mismatch")
    if "started.json" not in manifest["artifacts"] or (
        manifest["status"] in ("passed", "recorded") and "result.json" not in manifest["artifacts"]
    ):
        raise ValueError("Run manifest omits a required artifact")
    for relative, expected in manifest["artifacts"].items():
        path = root / relative
        if path.resolve() != path or not path.is_relative_to(root):
            raise ValueError("Run artifact escapes its directory or uses a symlink")
        data = read_artifact(path)
        if digest(data) != expected["sha256"] or len(data) != expected["bytes"]:
            raise ValueError(f"Run artifact SHA/size mismatch: {relative}")
    return manifest


def compare(before: Path, after: Path, output: Path) -> dict[str, Any]:
    if any(output.resolve().is_relative_to(path.resolve()) for path in (before, after)):
        raise ValueError("Diff must be outside the immutable runs")
    old_manifest, new_manifest = verify(before), verify(after)
    if old_manifest["suite"] != new_manifest["suite"]:
        raise ValueError("Cannot compare different suites")
    if any(row["status"] not in ("passed", "recorded") for row in (old_manifest, new_manifest)):
        raise ValueError("Comparison requires two completed runs")
    old, new = [json.loads(read_artifact(path / "result.json")) for path in (before, after)]
    left, right = observations(old), observations(new)
    differences = [
        {
            "id": identity,
            "kind": "added"
            if identity not in left
            else "removed"
            if identity not in right
            else "changed",
            "before": left.get(identity),
            "after": right.get(identity),
        }
        for identity in observation_changes(left, right)
    ]
    old_inputs, new_inputs = (
        old.get("read_inputs", old.get("inputs", {})),
        new.get("read_inputs", new.get("inputs", {})),
    )
    inputs = observation_changes(old_inputs, new_inputs)
    summary = {
        key: {"before": old.get(key), "after": new.get(key)}
        for key in (
            "counts",
            "action_counts",
            "profiles",
            "requested_profiles",
            "scope",
            "exclusions",
            "case_manifest_sha256",
            "catalog_sha256",
            "index_sha256",
        )
        if old.get(key) != new.get(key)
    }
    result = {
        "version": 1,
        "suite": old_manifest["suite"],
        "status": "changed" if differences or inputs or summary else "same",
        "before_manifest_sha256": digest(read_artifact(before / "manifest.json")),
        "after_manifest_sha256": digest(read_artifact(after / "manifest.json")),
        "changes": differences,
        "changed_inputs": inputs,
        "summary_changes": summary,
        "scope": "Observation differences, not an independent legal accuracy verdict",
    }
    write_json(output, result)
    return result


def release_arguments(job: ReleaseJob) -> list[str]:
    suite, preset = RELEASE_JOBS[job.id]
    if job.suite != suite:
        raise ValueError("Release job has the wrong registered suite")
    controlled_options(suite, list(job.args), tuple(arg for arg in preset if arg.startswith("--")))
    pins = {pin.path: pin.sha256 for pin in job.pins}
    if len(pins) != len(job.pins):
        raise ValueError("Release pins must have unique paths")
    options: dict[str, str] = {}
    iterator = iter(job.args)
    for flag in iterator:
        if flag not in RELEASE_FLAGS or flag in options:
            raise ValueError("Release arguments require unique explicit supported flags")
        value = (
            "true"
            if flag in ("--prepared-manifest", "--extraction-manifest")
            else next(iterator, "")
        )
        if not value or value.startswith("--"):
            raise ValueError("Release argument has no value")
        options[flag] = value
        if flag in PIN_FLAGS and value not in pins:
            raise ValueError("Release argument file has no explicit pin")
    for flag, source_flag in SHA_FLAGS.items():
        if flag in options and pins.get(options.get(source_flag, "")) != options[flag]:
            raise ValueError("Release argument SHA differs from its pin")
    if any(flag not in options for flag in REQUIRED_RELEASE_FLAGS.get(job.id, ())):
        raise ValueError("Release job omits required pinned inputs")
    return [*preset, *job.args]


def release_population(job: ReleaseJob, manifest: dict[str, Any], report: dict[str, Any]) -> str:
    rows = observations(report)
    inputs = report.get("read_inputs", {})
    absent = (
        {name for name, sha in inputs.items() if sha is None} if isinstance(inputs, dict) else set()
    )
    absent_ids = (
        {f"{name}/cover={cover}" for name in absent for cover in (False, True)}
        if job.suite == "scan"
        else absent
    )
    missing = (
        bool(absent or report.get("missing_inputs")) or report.get("complete_population") is False
    )
    errors = report.get("errors", {})
    allowed_errors = absent | ({"population"} if job.suite == "odt" and not rows else set())
    missing_errors = (
        bool(absent and errors)
        and isinstance(errors, dict)
        and set(errors).issubset(allowed_errors)
    )
    drift = any(
        report.get(key)
        for key in (
            "changed_inputs",
            "changed_code",
            "changed_during_replay",
        )
    )
    changes = (*report.get("changes", ()), *report.get("core_changes", ()))
    semantic_drift = any(
        not isinstance(name, str) or name not in absent_ids or name in rows for name in changes
    )
    explicit_not_run = manifest["status"] == report.get("status") == "not_run"
    if (
        drift
        or semantic_drift
        or (report.get("error") and not explicit_not_run)
        or (errors and not missing_errors)
    ):
        return "failed"
    if manifest["status"] == "failed" and not (missing and missing_errors and not drift):
        return "failed"
    if missing or manifest["status"] == "not_run" or len(rows) < job.expected_observations:
        return "not_run"
    if (
        len(rows) != job.expected_observations
        or observation_ids_sha256(rows) != job.expected_ids_sha256
    ):
        return "failed"
    if job.suite == "cost" and not any(
        isinstance(row, dict) and row.get("cases") for row in rows.values()
    ):
        return "not_run"
    return "recorded"


def release_sidecar(output: Path) -> Path:
    return output.with_name(output.name + ".runs")


def release_reference(
    reference: Path,
    pin: str | None,
    plan_pin: str,
    profiles: tuple[ReleaseProfile, ...],
    required: list[str],
) -> dict[str, Any]:
    raw = read_artifact(reference)
    if digest(raw) != pin:
        raise ValueError("Release reference SHA mismatch")
    previous: dict[str, Any] = json.loads(raw)
    if (
        previous.get("suite") != "release-v1"
        or previous.get("status") not in ("recorded", "passed")
        or previous.get("qualified") is not True
        or previous.get("plan_sha256") != plan_pin
        or previous.get("requested_profiles") != list(profiles)
        or set(previous.get("children", {})) != set(required)
    ):
        raise ValueError("Release reference must qualify the same complete plan and profiles")
    for identity in required:
        directory = release_sidecar(reference) / identity
        manifest = verify(directory)
        if (
            manifest["status"] not in ("recorded", "passed")
            or digest(read_artifact(directory / "manifest.json"))
            != previous["children"][identity]["manifest_sha256"]
        ):
            raise ValueError("Release reference child differs from its pinned manifest")
    return previous


def check(
    corpus: Path,
    output: Path,
    profiles: tuple[ReleaseProfile, ...],
    plan: Path,
    plan_sha256: str,
    *,
    mode: Literal["baseline", "compare"],
    reference: Path | None = None,
    reference_sha256: str | None = None,
) -> int:
    target = output.absolute()
    root, sidecar = corpus.resolve(), release_sidecar(target)
    if (
        target != target.resolve()
        or target.exists()
        or sidecar.exists()
        or sidecar.is_symlink()
        or target.is_relative_to(root)
    ):
        raise ValueError("Release output and sidecar must be new and outside the corpus")
    if (
        not profiles
        or len(set(profiles)) != len(profiles)
        or any(profile not in RELEASE_MATRIX for profile in profiles)
    ):
        raise ValueError("Choose nonempty unique release profiles")
    if (mode == "compare") != (reference is not None and reference_sha256 is not None) or (
        mode == "baseline" and (reference is not None or reference_sha256 is not None)
    ):
        raise ValueError(
            "Compare requires a pinned release reference; baseline does not accept one"
        )
    before = source_fingerprints()
    required = list(
        dict.fromkeys(identity for profile in profiles for identity in RELEASE_MATRIX[profile])
    )
    children: dict[str, Any] = {}
    checked_pins: dict[str, str] = {}
    result: dict[str, Any] = {
        "suite": "release-v1",
        "mode": mode,
        "requested_profiles": list(profiles),
        "plan_sha256": plan_sha256,
        "code_sha": code_version(),
        "code_dirty": code_dirty(),
        "source_sha256": before,
        "network": "prohibited by fixed offline child suites",
        "children": children,
        "scope": "Pinned regression observations, not independent legal or production acceptance",
    }
    try:
        raw = read_artifact(plan)
        if digest(raw) != plan_sha256:
            raise ValueError("Release plan SHA mismatch")
        manifest = ReleasePlan.model_validate_json(raw)
        jobs = {job.id: job for job in manifest.jobs}
        if len(jobs) != len(manifest.jobs) or any(
            identity not in RELEASE_JOBS for identity in jobs
        ):
            raise ValueError("Release jobs must be unique fixed matrix entries")
        for job in manifest.jobs:
            release_arguments(job)
        if not (root / "INDEX.json").resolve().is_relative_to(root):
            raise ValueError("Release corpus INDEX escapes the corpus")
        if digest(read_artifact(root / "INDEX.json")) != manifest.index_sha256:
            raise ValueError("Release corpus INDEX SHA mismatch")
        if reference is not None:
            release_reference(reference, reference_sha256, plan_sha256, profiles, required)
        sidecar.mkdir(parents=True)
        for identity in required:
            if identity not in jobs:
                children[identity] = {
                    "status": "not_run",
                    "reason": "Required release job is absent",
                }
                continue
            job = jobs[identity]
            try:
                for artifact in job.pins:
                    path = Path(artifact.path)
                    if (
                        not path.is_absolute()
                        or path != path.resolve()
                        or digest(read_artifact(path)) != artifact.sha256
                    ):
                        raise ValueError("Release artifact path or SHA mismatch")
                    checked_pins[artifact.path] = artifact.sha256
                child = execute(sidecar, identity, job.suite, root, release_arguments(job))
                directory = sidecar / identity
                verify(directory)
                if not (directory / "result.json").is_file():
                    children[identity] = {"status": child["status"], "reason": child["error"]}
                    continue
                report = json.loads(read_artifact(directory / "result.json"))
                status = release_population(job, child, report)
                children[identity] = {
                    "status": status,
                    "manifest_sha256": digest(read_artifact(directory / "manifest.json")),
                    "observations": len(observations(report)),
                    "ids_sha256": observation_ids_sha256(observations(report)),
                }
                if status == "recorded" and reference is not None:
                    release_reference(reference, reference_sha256, plan_sha256, profiles, required)
                    diff = compare(
                        release_sidecar(reference) / identity,
                        directory,
                        sidecar / f"{identity}-diff.json",
                    )
                    children[identity]["comparison"] = diff["status"]
                    if diff["status"] != "same":
                        children[identity]["status"] = "failed"
            except FileNotFoundError as exc:
                children[identity] = {"status": "not_run", "reason": str(exc)}
            except (OSError, ValueError, TypeError, KeyError) as exc:
                children[identity] = {"status": "failed", "reason": f"{type(exc).__name__}: {exc}"}
        if (
            not plan.is_file()
            or not (root / "INDEX.json").is_file()
            or not (root / "INDEX.json").resolve().is_relative_to(root)
            or digest(read_artifact(plan)) != plan_sha256
            or digest(read_artifact(root / "INDEX.json")) != manifest.index_sha256
            or before != source_fingerprints()
        ):
            raise ValueError("Release inputs or code changed during replay")
        for name, expected in checked_pins.items():
            path = Path(name)
            if (
                not path.is_file()
                or path != path.resolve()
                or digest(read_artifact(path)) != expected
            ):
                raise ValueError("Release pinned artifact changed during replay")
        if reference is not None:
            try:
                release_reference(reference, reference_sha256, plan_sha256, profiles, required)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                raise ValueError("Release reference changed during replay") from exc
        result["profiles"] = {
            profile: "failed"
            if any(children[identity]["status"] == "failed" for identity in RELEASE_MATRIX[profile])
            else "not_run"
            if any(
                children[identity]["status"] == "not_run" for identity in RELEASE_MATRIX[profile]
            )
            else "passed"
            if mode == "compare"
            else "recorded"
            for profile in profiles
        }
        states = set(result["profiles"].values())
        result["status"] = (
            "failed"
            if "failed" in states
            else "not_run"
            if "not_run" in states
            else "passed"
            if mode == "compare"
            else "recorded"
        )
    except FileNotFoundError as exc:
        result.update(status="not_run", reason=str(exc))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result.update(status="failed", reason=f"{type(exc).__name__}: {exc}")
    result["qualified"] = result["status"] in ("recorded", "passed")
    target.parent.mkdir(parents=True, exist_ok=True)
    write_json(target, result)
    return 0 if result["qualified"] else 2 if result["status"] == "not_run" else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "register"):
        command = commands.add_parser(name)
        command.add_argument("--root", type=Path, required=True)
        command.add_argument("--id", required=True)
        command.add_argument(
            "--suite",
            choices=tuple(SUITES) if name == "run" else (*SUITES, *RECORD_ONLY),
            required=True,
        )
        if name == "run":
            command.add_argument("--corpus", type=Path, required=True)
            command.add_argument("extra", nargs=argparse.REMAINDER)
        else:
            command.add_argument("--artifact", type=Path, required=True)
    check = commands.add_parser("verify")
    check.add_argument("directory", type=Path)
    diff = commands.add_parser("diff")
    diff.add_argument("before", type=Path)
    diff.add_argument("after", type=Path)
    diff.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "run":
        extra = args.extra[1:] if args.extra[:1] == ["--"] else args.extra
        result = execute(args.root, args.id, args.suite, args.corpus, extra)
    elif args.command == "register":
        result = register(args.root, args.id, args.suite, args.artifact)
    elif args.command == "verify":
        result = verify(args.directory)
    else:
        result = compare(args.before, args.after, args.output)
    print(
        json.dumps(
            {key: result[key] for key in ("id", "suite", "status", "observations") if key in result}
        )
    )
    raise SystemExit(
        0
        if result["status"] in ("passed", "recorded", "same")
        else 2
        if result["status"] == "not_run"
        else 1
    )


if __name__ == "__main__":
    main()
