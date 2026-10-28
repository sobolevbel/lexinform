import builtins
import gzip
import io
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from tools import native_replay as native
from tools.corpus_artifacts import digest, write_json
from tools.corpus_runtime import capture_code


@pytest.fixture
def recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    code, output = tmp_path / "code", tmp_path / "run"
    for relative in (
        "src/lexinform/__init__.py",
        "src/lexinform/module.py",
        "tests/__init__.py",
        "tests/conftest.py",
        "tests/helper.py",
        "tests/fixtures/input.txt",
        "uv.lock",
        "pyproject.toml",
    ):
        path = code / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("" if path.suffix == ".py" else "input")
    suite = native.Suite(
        "normal",
        "projection_profile.py",
        "DA_PROJECTION_PROFILE_OUTPUT",
        1,
        native.SUITES[0].source_sha256,
        digest(json.dumps(("one",)).encode()),
    )
    monkeypatch.setattr(native, "SUITES", (suite,))
    output.mkdir()
    pins = native.inputs(code)
    spec = native.Spec(code=str(code), output=str(output), inputs=pins, runner=native.runner_pins())
    write_json(output / "spec.json", spec.model_dump(mode="json"))
    read = "tests/fixtures/input.txt"
    guard = native.Guard(
        exitstatus=0,
        spec_sha256=digest((output / "spec.json").read_bytes()),
        imports={
            "lexinform": "src/lexinform/__init__.py",
            "tests.conftest": "tests/conftest.py",
            "tests.helper": "tests/helper.py",
        },
        reads={read: pins[read], "tests/fixtures/missing.txt": None},
        violations=(),
        source_unchanged=True,
    )
    write_json(output / "normal-guard.json", guard.model_dump(mode="json"))
    (output / "normal.xml").write_text(
        '<testsuites><testsuite><testcase name="one"/></testsuite></testsuites>'
    )
    (output / "normal").mkdir()
    (output / "normal/one.sql").write_text(
        "CREATE TABLE example(id INTEGER PRIMARY KEY); INSERT INTO example VALUES(1);"
    )
    raw = (output / "normal/one.sql").read_bytes()
    database = sqlite3.connect(":memory:")
    database.executescript(raw.decode())
    sqlite_bytes = len(database.serialize())
    database.close()
    data = {"example": digest(json.dumps([json.dumps((1,), ensure_ascii=False)]).encode())}
    write_json(
        output / "normal/profiles.json",
        {
            "one": {
                "data": data,
                "sql_bytes": len(raw),
                "gzip_bytes": len(gzip.compress(raw, mtime=0)),
                "sqlite_bytes": sqlite_bytes,
            }
        },
    )
    capture_code(output, pins, root=code)
    manifest = native.NativeRun(
        version=1,
        source=spec,
        environment=native.environment(),
        cases={"normal": ("one",)},
        imports=guard.imports,
        reads=guard.reads,
        artifacts=native.files_at(output),
    )
    write_json(output / "manifest.json", manifest.model_dump(mode="json"))
    return code, output


def rewrite(path: Path, **changes: Any) -> None:
    value = json.loads(path.read_bytes())
    value.update(changes)
    path.write_text(json.dumps(value))


def reseal(output: Path) -> None:
    rewrite(
        output / "manifest.json",
        artifacts={
            name: row.model_dump(mode="json") for name, row in native.files_at(output).items()
        },
    )


def test_verified_native_run_reuses_only_matching_inputs(recorded: tuple[Path, Path]) -> None:
    code, output = recorded
    checked = native.verify(output)
    assert checked.cases == {"normal": ("one",)}
    assert native.reuse_witness(output, code).reusable
    (code / "tests/unimported.py").write_text("changed = True")
    assert native.reuse_witness(output, code).reusable
    (code / "tests/helper.py").write_text("changed = True")
    result = native.reuse_witness(output, code)
    assert not result.reusable and result.differences == ("input:tests/helper.py",)


@pytest.mark.parametrize(
    "relative",
    [
        "src/lexinform/module.py",
        "pyproject.toml",
        "uv.lock",
        "tests/fixtures/input.txt",
        "tests/fixtures/missing.txt",
    ],
)
def test_reuse_rejects_runtime_config_fixture_and_absence_changes(
    recorded: tuple[Path, Path],
    relative: str,
) -> None:
    code, output = recorded
    (code / relative).write_text("changed")
    assert not native.reuse_witness(output, code).reusable


def test_reuse_rejects_environment_change(
    recorded: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    code, output = recorded
    original = native.environment()
    monkeypatch.setattr(
        native, "environment", lambda: original.model_copy(update={"sqlite": "other"})
    )
    assert native.reuse_witness(output, code).differences == ("environment",)


@pytest.mark.parametrize("operation", ["modify", "extra", "missing", "symlink", "nested-manifest"])
def test_artifact_inventory_fails_closed(recorded: tuple[Path, Path], operation: str) -> None:
    _, output = recorded
    if operation == "modify":
        (output / "normal/one.sql").write_text("changed")
    elif operation == "extra":
        (output / "extra.sql").write_text("changed")
    elif operation == "missing":
        (output / "normal/one.sql").unlink()
    elif operation == "symlink":
        (output / "alias").symlink_to(output / "normal", target_is_directory=True)
    else:
        (output / "normal/manifest.json").write_text("{}")
    with pytest.raises(ValueError):
        native.verify(output)


@pytest.mark.parametrize(
    "operation",
    [
        "extra-case",
        "rename-case",
        "missing-sql",
        "extra-sql",
        "bad-sql",
        "failed-xml",
        "bad-guard",
        "wrong-import",
        "manifest-closure",
    ],
)
def test_resealed_semantic_tampering_fails_closed(
    recorded: tuple[Path, Path], operation: str
) -> None:
    _, output = recorded
    if operation == "extra-case":
        rewrite(output / "normal/profiles.json", two={})
    elif operation == "rename-case":
        (output / "normal/profiles.json").write_text('{"two": {}}')
    elif operation == "missing-sql":
        (output / "normal/one.sql").unlink()
    elif operation == "extra-sql":
        (output / "normal/two.sql").write_text("")
    elif operation == "bad-sql":
        (output / "normal/one.sql").write_text("THIS IS NOT SQL")
    elif operation == "failed-xml":
        (output / "normal.xml").write_text(
            "<testsuites><testcase><failure/></testcase></testsuites>"
        )
    elif operation == "bad-guard":
        rewrite(output / "normal-guard.json", violations=["network"])
    elif operation == "wrong-import":
        rewrite(output / "normal-guard.json", imports={"lexinform": "../../outside.py"})
    else:
        rewrite(output / "manifest.json", imports={})
    reseal(output)
    with pytest.raises(ValueError):
        native.verify(output)


def test_verification_is_independent_of_live_source(recorded: tuple[Path, Path]) -> None:
    code, output = recorded
    (code / "src/lexinform/module.py").unlink()
    assert native.verify(output).version == 1


def test_native_sql_cannot_attach_a_database(tmp_path: Path) -> None:
    destination = tmp_path / "external.db"
    with pytest.raises(ValueError, match="SQL validation"):
        native.validate_sql(f"ATTACH DATABASE '{destination}' AS external;".encode())
    assert not destination.exists()


def guard_probe(
    tmp_path: Path,
    action: str,
    *,
    changed: bool = False,
    wrong_root: bool = False,
    before_guard: str = "",
) -> subprocess.CompletedProcess[bytes]:
    code, output = tmp_path / "code", tmp_path / "output"
    output.mkdir()
    for relative in (
        "src/lexinform/__init__.py",
        "src/lexinform/module.py",
        "tests/__init__.py",
        "tests/conftest.py",
    ):
        path = code / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    pins = {str(path.relative_to(code)): digest(path.read_bytes()) for path in code.rglob("*.py")}
    spec = native.Spec(
        code=str(code),
        output=str(output),
        inputs=pins,
        runner={
            str(native.RUNNER / "native_replay_guard.py"): digest(
                (native.RUNNER / "native_replay_guard.py").read_bytes()
            )
        },
    )
    write_json(output / "spec.json", spec.model_dump(mode="json"))
    if changed:
        (code / "src/lexinform/module.py").write_text("changed = True")
    env = os.environ.copy()
    env.update(
        {
            "LEXINFORM_NATIVE_SPEC": str(output / "spec.json"),
            "LEXINFORM_NATIVE_GUARD_OUTPUT": str(output / "guard.json"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPYCACHEPREFIX": str(output / "pycache"),
            "PYTHONPATH": os.pathsep.join((str(code / "src"), str(code), str(native.RUNNER))),
        }
    )
    script = (
        before_guard
        + "\nimport native_replay_guard as guard\nimport lexinform.module, tests.conftest\n"
    )
    if wrong_root:
        script += "lexinform.module.__file__ = '/outside/module.py'\n"
    script += (
        "try:\n " + action + "\nexcept Exception:\n pass\nguard.pytest_sessionfinish(None, 0)\n"
    )
    return subprocess.run(
        [sys.executable, "-c", script], cwd=code, env=env, capture_output=True, check=False
    )


@pytest.mark.parametrize(
    "action",
    [
        "__import__('socket').getaddrinfo('example.invalid', 443)",
        "__import__('socket').socket().connect(('127.0.0.1', 1))",
        "__import__('subprocess').run(['never-executed'])",
        "__import__('os').system('never-executed')",
        "__import__('sys').audit('os.fork')",
        "__import__('sys').audit('os.exec', 'unused', (), {})",
        "open('.env')",
        "open('src/lexinform/module.py', 'w')",
        "__import__('sqlite3').connect('external.db')",
    ],
)
def test_guard_rejects_even_swallowed_operations(tmp_path: Path, action: str) -> None:
    assert guard_probe(tmp_path, action).returncode != 0
    assert not (tmp_path / "output/guard.json").exists()
    assert (tmp_path / "code/src/lexinform/module.py").read_text() == ""
    assert not (tmp_path / "code/external.db").exists()


def test_guard_accepts_only_output_writes_and_captures_imports(tmp_path: Path) -> None:
    action = "open(guard.OUTPUT / 'allowed.txt', 'w').close()"
    result = guard_probe(tmp_path, action)
    assert result.returncode == 0
    report = native.Guard.model_validate_json((tmp_path / "output/guard.json").read_bytes())
    assert report.source_unchanged and not report.violations
    assert report.imports["lexinform.module"] == "src/lexinform/module.py"


@pytest.mark.parametrize("changed,wrong_root", [(True, False), (False, True)])
def test_guard_rejects_source_or_import_root_changes(
    tmp_path: Path, changed: bool, wrong_root: bool
) -> None:
    assert guard_probe(tmp_path, "pass", changed=changed, wrong_root=wrong_root).returncode != 0


def test_fixed_observer_population_and_hashes_are_preserved() -> None:
    assert sum(suite.count for suite in native.SUITES) == 168
    assert len(native.runner_pins()) == 10


def test_run_rejects_existing_output_and_source_output(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="under out"):
        native.run(tmp_path, tmp_path / "tests/output")


def test_resealed_derived_profile_hash_tampering_is_rejected(recorded: tuple[Path, Path]) -> None:
    _, output = recorded
    path = output / "normal/profiles.json"
    value = json.loads(path.read_bytes())
    value["one"]["data"]["example"] = "forged"
    path.write_text(json.dumps(value))
    reseal(output)
    with pytest.raises(ValueError, match="derived SQL"):
        native.verify(output)


def test_run_never_overwrites_an_existing_run(recorded: tuple[Path, Path]) -> None:
    code, output = recorded
    before = native.files_at(output)
    with pytest.raises(FileExistsError):
        native.run(code, output)
    assert native.files_at(output) == before


def test_cli_denied_reuse_has_a_nonzero_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["native_replay", "reuse", "old", "--code", "code"])
    decision = native.ReuseDecision(reusable=False, differences=("environment",))
    monkeypatch.setattr(native, "reuse_witness", lambda output, code: decision)
    with pytest.raises(SystemExit) as failure:
        native.main()
    assert failure.value.code == 1


def test_child_environment_does_not_inherit_settings_credentials_or_pytest_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code, output = tmp_path / "code", tmp_path / "output"
    expected = native.replay_environment(code, output)
    for name in ("OPENAI_API_KEY", "LEXINFORM_MIN_SCORE", "PYTEST_ADDOPTS", "HTTP_PROXY", "PATH"):
        monkeypatch.setenv(name, "untrusted")
    assert native.replay_environment(code, output) == expected
    assert expected["PYTHONHASHSEED"] == "0"


@pytest.mark.parametrize("operation", ["open", "remove", "mkdir", "rename", "alias-open"])
def test_descriptor_relative_operations_cannot_escape_output(
    tmp_path: Path, operation: str
) -> None:
    setup = "import os; fd = os.open(guard.CODE, os.O_RDONLY); os.chdir(guard.OUTPUT); "
    operations = {
        "open": "os.open('escape.txt', os.O_WRONLY | os.O_CREAT, dir_fd=fd)",
        "alias-open": "old_open('escape.txt', os.O_WRONLY | os.O_CREAT, dir_fd=fd)",
        "remove": "os.remove('src/lexinform/module.py', dir_fd=fd)",
        "mkdir": "os.mkdir('escape-dir', dir_fd=fd)",
        "rename": "os.rename('src/lexinform/module.py', 'moved.py', src_dir_fd=fd, dst_dir_fd=fd)",
    }
    result = guard_probe(
        tmp_path, setup + operations[operation], before_guard="from os import open as old_open"
    )
    assert result.returncode != 0
    assert (tmp_path / "code/src/lexinform/module.py").read_text() == ""
    assert not (tmp_path / "code/escape.txt").exists()
    assert not (tmp_path / "code/escape-dir").exists()
    assert not (tmp_path / "code/moved.py").exists()


def test_directory_descriptor_output_cleanup_remains_supported(tmp_path: Path) -> None:
    action = "import shutil; p = guard.OUTPUT / 'tree'; p.mkdir(); (p / 'inner').mkdir(); (p / 'inner/file').touch(); shutil.rmtree(p)"
    action = "assert shutil._use_fd_functions; " + action
    assert guard_probe(tmp_path, action, before_guard="import shutil").returncode == 0
    assert not (tmp_path / "output/tree").exists()


def test_relative_builtin_reads_remain_supported(tmp_path: Path) -> None:
    assert guard_probe(tmp_path, "open(file='src/lexinform/module.py').read()").returncode == 0


@pytest.mark.parametrize(
    "relative",
    [
        "src/alias.py",
        "tools/alias.py",
        "pyproject.toml",
        "uv.lock",
        "tests/fixtures/alias.bin",
        "src",
        "tests/fixtures",
    ],
)
def test_preflight_rejects_aliases_before_reading_their_content(
    recorded: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative: str,
) -> None:
    code, _ = recorded
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / ".env"
    secret.write_text("must-not-be-read")
    target = code / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_dir():
        target.rename(code / (target.name + "-original"))
        target.symlink_to(outside, target_is_directory=True)
    else:
        target.unlink(missing_ok=True)
        target.symlink_to(secret)
    original = Path.read_bytes
    forbidden_reads = []

    def checked(path: Path) -> bytes:
        if path.resolve().is_relative_to(outside):
            forbidden_reads.append(str(path))
            raise AssertionError("Aliased content reached the file reader")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", checked)
    with pytest.raises(ValueError):
        native.inputs(code)
    assert not forbidden_reads


def test_unknown_preexisting_directory_descriptor_is_rejected(tmp_path: Path) -> None:
    result = guard_probe(
        tmp_path,
        "os.chdir(guard.OUTPUT); os.open('escape.txt', os.O_WRONLY | os.O_CREAT, dir_fd=fd)",
        before_guard="import os; fd = os.open('.', os.O_RDONLY)",
    )
    assert result.returncode != 0
    assert not (tmp_path / "code/escape.txt").exists()


def test_child_rejects_new_same_content_source_alias_before_hashing(tmp_path: Path) -> None:
    setup = "from pathlib import Path; p = Path('src/lexinform/module.py'); p.unlink(); p.symlink_to(Path('tests/conftest.py').resolve())"
    result = guard_probe(tmp_path, "pass", before_guard=setup)
    assert result.returncode != 0
    assert b"aliased input path" in result.stderr


@pytest.mark.parametrize(
    "entry",
    [
        "manifest.json",
        "spec.json",
        "normal-guard.json",
        "normal.xml",
        "normal/profiles.json",
        "code.tar.gz",
    ],
)
def test_verify_rejects_artifact_aliases_before_any_external_read(
    recorded: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entry: str,
) -> None:
    _, output = recorded
    external = (tmp_path / "benign-external.json").resolve()
    external.write_text('{"benign": true}')
    alias = output / entry
    alias.unlink()
    alias.symlink_to(external)
    original = Path.read_bytes
    reads = []

    def spy(path: Path) -> bytes:
        if path.resolve() == external:
            reads.append(str(path))
            raise AssertionError("External artifact content was read")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", spy)
    with pytest.raises(ValueError):
        native.verify(output)
    assert not reads


@pytest.mark.parametrize(
    "entry", ["spec.json", "normal-guard.json", "normal.xml", "normal/profiles.json"]
)
def test_direct_observations_validate_entry_paths_before_reading(
    recorded: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entry: str,
) -> None:
    _, output = recorded
    spec = native.Spec.model_validate_json((output / "spec.json").read_bytes())
    external = (tmp_path / "benign-observation.json").resolve()
    external.write_text('{"benign": true}')
    alias = output / entry
    alias.unlink()
    alias.symlink_to(external)
    reads = []
    original_builtin, original_io = builtins.open, io.open

    def checked(file: Any) -> None:
        if (
            isinstance(file, (str, bytes, os.PathLike))
            and Path(os.fsdecode(file)).resolve() == external
        ):
            reads.append(True)
            raise AssertionError("External observation was read")

    def builtin_spy(file: Any, *args: Any, **kwargs: Any) -> Any:
        checked(file)
        return original_builtin(file, *args, **kwargs)

    def io_spy(file: Any, *args: Any, **kwargs: Any) -> Any:
        checked(file)
        return original_io(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", builtin_spy)
    monkeypatch.setattr(io, "open", io_spy)
    with pytest.raises(ValueError):
        native.observations(output, spec)
    assert not reads


def test_guard_rejects_an_aliased_spec_before_reading(tmp_path: Path) -> None:
    setup = "import os; from pathlib import Path; p = Path(os.environ['LEXINFORM_NATIVE_SPEC']); external = p.parent / 'benign-spec.json'; external.write_bytes(p.read_bytes()); p.unlink(); p.symlink_to(external); original = Path.read_bytes; Path.read_bytes = lambda path: (_ for _ in ()).throw(AssertionError('external spec read')) if path.resolve() == external.resolve() else original(path)"
    result = guard_probe(tmp_path, "pass", before_guard=setup)
    assert result.returncode != 0
    assert b"spec path is aliased" in result.stderr


def test_sql_profile_compression_uses_explicit_level_nine(
    recorded: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, output = recorded
    profile = json.loads((output / "normal/profiles.json").read_bytes())["one"]
    original = gzip.compress
    calls = []

    def compressed(data: bytes, *, compresslevel: int, mtime: int) -> bytes:
        calls.append((compresslevel, mtime))
        return original(data, compresslevel=compresslevel, mtime=mtime)

    monkeypatch.setattr(gzip, "compress", compressed)
    native.validate_sql((output / "normal/one.sql").read_bytes(), profile=profile, group="normal")
    assert calls == [(9, 0)]
