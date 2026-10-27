"""Keep immutable corpus runs and compare their observations through one offline suite registry."""

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_runtime import capture_code, code_dirty, code_version, source_fingerprints

SUITES = {
    "health": ("corpus_health", ()),
    "ci": ("corpus_check", ()),
    "external": ("corpus_check", ("--suite", "external")),
    "pdf": ("replay_pdf_pages", ()),
    "word": ("replay_word_blocks", ()),
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
    "process": ("corpus_legacy_suites", ("--kind", "process")),
    "senate": ("corpus_legacy_suites", ("--kind", "senate")),
    "scan": ("replay_scan_inputs", ()),
}

RECORD_ONLY = ("sources", "install", "sejm-snapshot", "sejm-catalogue", "sejm-sources")


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
    controlled = (
        "--output",
        "--corpus",
        "--suite",
        *(arg for arg in preset if arg.startswith("--")),
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
        for identity in sorted(left.keys() | right.keys())
        if left.get(identity) != right.get(identity)
    ]
    old_inputs, new_inputs = (
        old.get("read_inputs", old.get("inputs", {})),
        new.get("read_inputs", new.get("inputs", {})),
    )
    inputs = sorted(
        key
        for key in old_inputs.keys() | new_inputs.keys()
        if old_inputs.get(key) != new_inputs.get(key)
    )
    summary = {
        key: {"before": old.get(key), "after": new.get(key)}
        for key in (
            "counts",
            "action_counts",
            "profiles",
            "requested_profiles",
            "scope",
            "exclusions",
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
