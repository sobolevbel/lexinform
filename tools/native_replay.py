"""Run and verify six fixed offline native observer suites."""

import argparse
import gzip
import importlib.metadata
import json
import os
import platform
import sqlite3
import subprocess
import sys
import tarfile
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from tools.corpus_artifacts import digest, write_json
from tools.corpus_runtime import capture_code, source_fingerprints

RUNNER = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Suite:
    name: str
    filename: str
    variable: str
    count: int
    source_sha256: str
    cases_sha256: str


SUITES: tuple[Suite, ...] = (
    Suite(
        "normal",
        "projection_profile.py",
        "DA_PROJECTION_PROFILE_OUTPUT",
        12,
        "ea04424fccc679b9ad4569ec7c07cc348a73d92c29bb0aa38cb7e517d7e50f0e",
        "36a2504a4ee69efde94605bc530e736c47850cd0ec5ab7dee3d126cdbf09de57",
    ),
    Suite(
        "handoff",
        "handoff_profile.py",
        "DA_HANDOFF_PROFILE_OUTPUT",
        24,
        "da7b56044237f17489d0f702a868114232c9e88a2503168ca77f2a37065365ce",
        "25c63871b466edcc4ffbe10884c494385460a8bb4a4d594c38081aa8fa94bb62",
    ),
    Suite(
        "reuse",
        "reuse_profile.py",
        "DA_REUSE_PROFILE_OUTPUT",
        16,
        "6a75655ca4b5fd1e4b81e049ba5e7b4a23b7ed3b75656e3584bf9563b05d3962",
        "6c2c6eda6ed153ad7733ae805aeeafeda8689c1a3c25ff7bee31dcaf1cfe5240",
    ),
    Suite(
        "explanation",
        "explanation_profile.py",
        "DA_EXPLANATION_PROFILE_OUTPUT",
        16,
        "cff55fa57bfe4df9bfe142c92c697e751720b09512c63733052c3e70632cf777",
        "199042ec03d9fc3e73ca4f84e470ab2314997ad3cbb4500941ed2a5560145b5a",
    ),
    Suite(
        "primary",
        "primary_profile.py",
        "DA_PRIMARY_PROFILE_OUTPUT",
        36,
        "f11f5516c55b6993a27029c92fde9eb32075666b7c24a969a3dd849e0cad191e",
        "3507705b06dd4ac8fc5d24e39bdcb624c4d94c5283808078fd8b3080116114f3",
    ),
    Suite(
        "admission",
        "admission_flows.py",
        "DA_ADMISSION_OUTPUT",
        64,
        "185e456e5bc01bf5c69efe1e12c6dd15138a5d547f3e5231fbeaad386dac6801",
        "0decceef0163922d76e747329bfe5566865a15b16ff69175f712f6e94de3c2d0",
    ),
)


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Artifact(FrozenModel):
    sha256: str
    bytes: int


class Guard(FrozenModel):
    exitstatus: int
    spec_sha256: str
    imports: dict[str, str]
    reads: dict[str, str | None]
    violations: tuple[str, ...]
    source_unchanged: bool


class Spec(FrozenModel):
    code: str
    output: str
    inputs: dict[str, str]
    runner: dict[str, str]


class Environment(FrozenModel):
    python: str
    executable_sha256: str
    platform: str
    sqlite: str
    zlib: str
    packages: dict[str, str]


class NativeRun(FrozenModel):
    version: int
    source: Spec
    environment: Environment
    cases: dict[str, tuple[str, ...]]
    imports: dict[str, str]
    reads: dict[str, str | None]
    artifacts: dict[str, Artifact]


class ReuseDecision(FrozenModel):
    reusable: bool
    differences: tuple[str, ...]


def environment() -> Environment:
    return Environment(
        python=sys.version,
        executable_sha256=digest(Path(sys.executable).resolve().read_bytes()),
        platform=platform.platform(),
        sqlite=sqlite3.sqlite_version,
        zlib=zlib.ZLIB_RUNTIME_VERSION,
        packages={
            item.metadata["Name"]: item.version for item in importlib.metadata.distributions()
        },
    )


def runner_pins() -> dict[str, str]:
    files = [
        RUNNER / "native_replay.py",
        RUNNER / "native_replay_guard.py",
        RUNNER / "corpus_artifacts.py",
        RUNNER / "corpus_runtime.py",
    ]
    for suite in SUITES:
        path = RUNNER / "native_observers" / suite.filename
        safe_path(RUNNER, str(path.relative_to(RUNNER)))
        if digest(path.read_bytes()) != suite.source_sha256:
            raise ValueError("Native observer source changed: " + suite.name)
        files.append(path)
    for path in files:
        safe_path(RUNNER, str(path.relative_to(RUNNER)))
    return {str(path): digest(path.read_bytes()) for path in files}


def inputs(code: Path) -> dict[str, str]:
    result = source_fingerprints(root=code)
    result["pyproject.toml"] = digest(safe_path(code, "pyproject.toml").read_bytes())
    fixtures = safe_path(code, "tests/fixtures")
    for path in sorted(fixtures.rglob("*")):
        safe_path(code, str(path.relative_to(code)))
        if path.is_file():
            result[str(path.relative_to(code))] = digest(path.read_bytes())
    for relative in result:
        safe_path(code, relative)
    return result


def safe_path(root: Path, relative: str) -> Path:
    path = root / relative
    if (
        Path(relative).is_absolute()
        or path.resolve() != path.absolute()
        or ".." in Path(relative).parts
    ):
        raise ValueError("Native artifact path is not a contained regular path")
    if not path.is_relative_to(root) or path.name == ".env":
        raise ValueError("Native artifact path escapes root")
    return path


def files_at(root: Path) -> dict[str, Artifact]:
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Native artifacts must not contain symlinks")
        if path.is_file() and path != root / "manifest.json":
            safe_path(root, str(path.relative_to(root)))
            result[str(path.relative_to(root))] = Artifact(
                sha256=digest(path.read_bytes()), bytes=path.stat().st_size
            )
    return result


def merged(target: dict[str, Any], values: dict[str, Any]) -> None:
    if any(name in target and target[name] != value for name, value in values.items()):
        raise ValueError("Native suite input closures disagree")
    target.update(values)


def validate_sql(raw: bytes, *, profile: dict[str, Any] | None = None, group: str = "") -> None:
    connection = sqlite3.connect(":memory:")
    connection.set_authorizer(
        lambda action, first, second, database, trigger: (
            sqlite3.SQLITE_DENY
            if action in {sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH}
            else sqlite3.SQLITE_OK
        )
    )
    try:
        connection.executescript(raw.decode())
        if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("Native SQL integrity failure")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Native SQL foreign-key failure")
        if profile is not None:
            expected: dict[str, Any] = {"sql_sha256": digest(raw)}
            if group != "admission":
                expected.update(
                    sql_bytes=len(raw),
                    gzip_bytes=len(gzip.compress(raw, compresslevel=9, mtime=0)),
                    sqlite_bytes=len(connection.serialize()),
                )
            if group == "normal":
                expected.pop("sql_sha256")
                tables = {}
                for (table,) in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ):
                    query = 'SELECT * FROM "' + table.replace('"', '""') + '"'
                    if table == "result_applications":
                        query += " WHERE generation IS NOT NULL"
                    rows = sorted(
                        json.dumps(row, ensure_ascii=False) for row in connection.execute(query)
                    )
                    tables[table] = digest(json.dumps(rows).encode())
                expected["data"] = tables
            if any(
                type(profile.get(key)) is not type(value) or profile[key] != value
                for key, value in expected.items()
            ):
                raise ValueError("Native profile derived SQL values disagree")
    except sqlite3.Error, UnicodeError:
        raise ValueError("Native SQL validation failed") from None
    finally:
        connection.close()


def observations(
    output: Path, spec: Spec
) -> tuple[dict[str, tuple[str, ...]], dict[str, str], dict[str, str | None]]:
    cases: dict[str, tuple[str, ...]] = {}
    imports: dict[str, str] = {}
    reads: dict[str, str | None] = {}
    spec_sha = digest(safe_path(output, "spec.json").read_bytes())
    for suite in SUITES:
        guard = Guard.model_validate_json(
            safe_path(output, suite.name + "-guard.json").read_bytes()
        )
        if (
            guard.exitstatus != 0
            or guard.violations
            or not guard.source_unchanged
            or guard.spec_sha256 != spec_sha
        ):
            raise ValueError("Native suite guard did not pass: " + suite.name)
        for relative in guard.imports.values():
            if relative not in spec.inputs or not relative.endswith(".py"):
                raise ValueError("Native import is outside the frozen source")
        if not {"lexinform", "tests.conftest"} <= guard.imports.keys():
            raise ValueError("Native suite import closure is incomplete")
        merged(imports, guard.imports)
        merged(reads, guard.reads)
        xml = ET.parse(safe_path(output, suite.name + ".xml")).getroot()
        tests = xml.findall(".//testcase")
        if not tests or any(
            item.find(tag) is not None for item in tests for tag in ("failure", "error", "skipped")
        ):
            raise ValueError("Native pytest suite did not finish all tests")
        profiles = json.loads(safe_path(output, suite.name + "/profiles.json").read_bytes())
        if not isinstance(profiles, dict) or len(profiles) != suite.count:
            raise ValueError("Native observer case count changed: " + suite.name)
        keys = tuple(sorted(profiles))
        if digest(json.dumps(keys).encode()) != suite.cases_sha256:
            raise ValueError("Native observer case identities changed: " + suite.name)
        actual_sql = {path.stem for path in (output / suite.name).glob("*.sql")}
        if actual_sql != set(keys):
            raise ValueError("Native observer SQL population differs from profiles")
        for key in keys:
            if not isinstance(profiles[key], dict):
                raise ValueError("Native observer profile is not an object")
            sql_profile = profiles[key]
            if suite.name in {"handoff", "reuse"}:
                sql_profile = profiles[key].get("snapshot")
                if not isinstance(sql_profile, dict):
                    raise ValueError("Native observer SQL snapshot is not an object")
            validate_sql(
                safe_path(output, f"{suite.name}/{key}.sql").read_bytes(),
                profile=sql_profile,
                group=suite.name,
            )
        cases[suite.name] = keys
    return cases, imports, reads


def replay_environment(code: Path, output: Path) -> dict[str, str]:
    return {
        "PATH": os.defpath,
        "HOME": str(output / "home"),
        "LANG": "C.UTF-8",
        "TZ": "UTC",
        "PYTHONUTF8": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONPATH": os.pathsep.join((str(code / "src"), str(code), str(RUNNER))),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONPYCACHEPREFIX": str(output / "pycache"),
        "UV_NO_SYNC": "1",
        "UV_OFFLINE": "1",
        "UV_FROZEN": "1",
        "DA_EXPECTED_CODE": str(code),
        "LEXINFORM_NATIVE_SPEC": str(output / "spec.json"),
    }


def run(code_root: Path, output: Path) -> NativeRun:
    code, output = code_root.resolve(), output.absolute()
    if output.resolve() != output or output == code or code.is_relative_to(output):
        raise ValueError("Native output must be a fresh contained directory")
    if output.is_relative_to(code) and not output.is_relative_to(code / "out"):
        raise ValueError("Native output inside the source tree must be under out")
    pins = runner_pins()
    frozen = inputs(code)
    output.mkdir(parents=True, exist_ok=False)
    spec = Spec(code=str(code), output=str(output), inputs=frozen, runner=pins)
    write_json(output / "spec.json", spec.model_dump(mode="json"))
    started_environment = environment()
    env = replay_environment(code, output)
    for suite in SUITES:
        temporary = output / (suite.name + "-tmp")
        temporary.mkdir()
        env.update(
            {
                suite.variable: str(output / suite.name),
                "TMPDIR": str(temporary),
                "LEXINFORM_NATIVE_GUARD_OUTPUT": str(output / (suite.name + "-guard.json")),
            }
        )
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-c",
            str(code / "pyproject.toml"),
            "--rootdir=" + str(code),
            "-n0",
            "-p",
            "xdist.plugin",
            "-p",
            "native_replay_guard",
            "-p",
            "no:cacheprovider",
            "-p",
            "tests.conftest",
            str(RUNNER / "native_observers" / suite.filename),
            "--log-file=" + str(output / (suite.name + "-pytest.log")),
            "--basetemp=" + str(temporary / "pytest"),
            "--junitxml=" + str(output / (suite.name + ".xml")),
        ]
        with (output / (suite.name + ".log")).open("xb") as stream:
            process = subprocess.run(
                command, cwd=code, env=env, stdout=stream, stderr=subprocess.STDOUT, check=False
            )
        if process.returncode != 0:
            raise ValueError("Native observer failed; inspect local log: " + suite.name)
    if inputs(code) != frozen or runner_pins() != pins or environment() != started_environment:
        raise ValueError("Native source or environment changed during replay")
    cases, imports, reads = observations(output, spec)
    snapshot = dict(frozen)
    snapshot.update({name: sha for name, sha in reads.items() if sha is not None})
    capture_code(output, snapshot, root=code)
    manifest = NativeRun(
        version=1,
        source=spec,
        environment=started_environment,
        cases=cases,
        imports=imports,
        reads=reads,
        artifacts=files_at(output),
    )
    write_json(output / "manifest.json", manifest.model_dump(mode="json"))
    return verify(output)


def verify(output: Path) -> NativeRun:
    output = output.resolve()
    manifest = NativeRun.model_validate_json(safe_path(output, "manifest.json").read_bytes())
    if manifest.version != 1 or manifest.artifacts != files_at(output):
        raise ValueError("Native artifact inventory changed")
    spec = Spec.model_validate_json(safe_path(output, "spec.json").read_bytes())
    if spec != manifest.source:
        raise ValueError("Native source manifest differs from guard specification")
    expected = dict(spec.inputs)
    expected.update({name: sha for name, sha in manifest.reads.items() if sha is not None})
    with tarfile.open(safe_path(output, "code.tar.gz"), "r:gz") as archive:
        observed = {}
        for item in archive:
            relative = Path(item.name)
            if relative.is_absolute() or ".." in relative.parts or relative.name == ".env":
                raise ValueError("Native snapshot member escapes source")
            stream = archive.extractfile(item) if item.isfile() else None
            if stream is None or item.name in observed:
                raise ValueError("Native source snapshot contains an invalid member")
            observed[item.name] = digest(stream.read())
        if observed != expected:
            raise ValueError("Native source snapshot disagrees with input hashes")
    cases, imports, reads = observations(output, spec)
    if (cases, imports, reads) != (manifest.cases, manifest.imports, manifest.reads):
        raise ValueError("Native observer closure differs from manifest")
    return manifest


def reuse_witness(output: Path, code_root: Path) -> ReuseDecision:
    manifest = verify(output)
    code = code_root.resolve()
    differences = []
    if environment() != manifest.environment:
        differences.append("environment")
    previous_runner = {Path(name).name: sha for name, sha in manifest.source.runner.items()}
    if previous_runner != {Path(name).name: sha for name, sha in runner_pins().items()}:
        differences.append("runner")
    previous = {
        name: sha
        for name, sha in manifest.source.inputs.items()
        if name.startswith("src/") or name in {"pyproject.toml", "uv.lock"}
    }
    current = {
        name: sha
        for name, sha in inputs(code).items()
        if name.startswith("src/") or name in {"pyproject.toml", "uv.lock"}
    }
    if previous != current:
        differences.append("runtime-or-config")
    for relative in sorted(set(manifest.imports.values()) | manifest.reads.keys()):
        path = safe_path(code, relative)
        expected = manifest.reads.get(relative, manifest.source.inputs.get(relative))
        actual = digest(path.read_bytes()) if path.is_file() else None
        if actual != expected:
            differences.append("input:" + relative)
    return ReuseDecision(reusable=not differences, differences=tuple(differences))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("run")
    start.add_argument("--code", type=Path, required=True)
    start.add_argument("--output", type=Path, required=True)
    check = commands.add_parser("verify")
    check.add_argument("output", type=Path)
    reuse = commands.add_parser("reuse")
    reuse.add_argument("output", type=Path)
    reuse.add_argument("--code", type=Path, required=True)
    arguments = parser.parse_args()
    result: NativeRun | ReuseDecision
    if arguments.command == "run":
        result = run(arguments.code, arguments.output)
    elif arguments.command == "verify":
        result = verify(arguments.output)
    else:
        result = reuse_witness(arguments.output, arguments.code)
    print(
        json.dumps(
            {
                "command": arguments.command,
                "cases": sum(suite.count for suite in SUITES),
                "reusable": result.reusable if isinstance(result, ReuseDecision) else None,
            }
        )
    )

    if isinstance(result, ReuseDecision) and not result.reusable:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
