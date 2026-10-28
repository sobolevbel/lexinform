"""Guard the fixed native observers without importing the source under test."""

import builtins
import hashlib
import io
import json
import os
import stat
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Any, NoReturn
from unittest.mock import patch

SPEC_PATH = Path(os.environ["LEXINFORM_NATIVE_SPEC"]).absolute()


def spec_bytes() -> bytes:
    path = SPEC_PATH
    if path.resolve() != path or path.name == ".env":
        raise RuntimeError("Native replay spec path is aliased or reserved")
    return path.read_bytes()


SPEC_BYTES = spec_bytes()
SPEC = json.loads(SPEC_BYTES)
CODE = Path(SPEC["code"])
OUTPUT = Path(SPEC["output"])
READS: dict[str, str | None] = {}
VIOLATIONS: list[str] = []
CHECKING = False
DIRECTORIES: dict[int, tuple[Path, int, int]] = {}
OS_OPEN = os.open
BUILTIN_OPEN = builtins.open
IO_OPEN = io.open


def fingerprint(path: Path) -> str | None:
    global CHECKING
    previous, CHECKING = CHECKING, True
    try:
        if path.resolve() != path.absolute() or path.name == ".env":
            reject("aliased input path")
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    finally:
        CHECKING = previous


def frozen() -> bool:
    return all(fingerprint(CODE / name) == sha for name, sha in SPEC["inputs"].items()) and all(
        fingerprint(Path(name)) == sha for name, sha in SPEC["runner"].items()
    )


def reject(reason: str) -> NoReturn:
    VIOLATIONS.append(reason)
    raise RuntimeError("Native replay prohibited: " + reason)


def origin(raw: object, dir_fd: int | None = None) -> Path:
    if not isinstance(raw, (str, bytes, os.PathLike)):
        reject("unresolved file origin")
    path = Path(os.fsdecode(raw))
    if path.is_absolute() or dir_fd is None or dir_fd == -1:
        return path.absolute()
    known = DIRECTORIES.get(dir_fd)
    if known is None:
        reject("unknown directory descriptor")
    base, device, inode = known
    current = os.fstat(dir_fd)
    location = base.stat()
    if (
        (current.st_dev, current.st_ino) != (device, inode)
        or (location.st_dev, location.st_ino) != (device, inode)
        or base.resolve() != base
    ):
        reject("directory descriptor origin changed")
    return base / path


def writable(raw: object, dir_fd: int | None = None) -> None:
    if not origin(raw, dir_fd).resolve().is_relative_to(OUTPUT):
        reject("write outside replay output")


def guarded_os_open(path: Any, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
    path = origin(path, dir_fd)
    descriptor = OS_OPEN(path, flags, mode)
    identity = os.fstat(descriptor)
    if stat.S_ISDIR(identity.st_mode):
        DIRECTORIES[descriptor] = (path.resolve(), identity.st_dev, identity.st_ino)
    return descriptor


def absolute_file(raw: Any) -> Any:
    return origin(raw) if isinstance(raw, (str, bytes, os.PathLike)) else raw


def guarded_builtin_open(file: Any, *args: Any, **kwargs: Any) -> Any:
    return BUILTIN_OPEN(absolute_file(file), *args, **kwargs)


def guarded_io_open(file: Any, *args: Any, **kwargs: Any) -> Any:
    return IO_OPEN(absolute_file(file), *args, **kwargs)


def audit(event: str, args: tuple[Any, ...]) -> None:
    if CHECKING:
        return
    if event.startswith("socket.") and event in {
        "socket.connect",
        "socket.getaddrinfo",
        "socket.gethostbyname",
        "socket.gethostbyaddr",
        "socket.sendto",
        "socket.sendmsg",
        "socket.bind",
    }:
        reject("network")
    if event in {
        "subprocess.Popen",
        "os.system",
        "os.posix_spawn",
        "os.spawn",
        "os.exec",
        "os.fork",
        "os.forkpty",
    }:
        reject("subprocess")
    if event == "sqlite3.connect" and str(args[0]) != ":memory:":
        writable(args[0])
    if event in {"os.remove", "os.rmdir"}:
        writable(args[0], args[1])
    if event in {"os.mkdir", "os.chmod"}:
        writable(args[0], args[2])
    if event == "os.truncate":
        writable(args[0])
    if event in {"os.rename", "os.link"}:
        writable(args[0], args[2])
        writable(args[1], args[3])
    if event == "os.symlink":
        if args[2] != -1:
            reject("descriptor-relative symlink")
        writable(args[0])
        writable(args[1])
    if event != "open" or not isinstance(args[0], (str, bytes)):
        return
    lexical = Path(os.fsdecode(args[0]))
    if not lexical.is_absolute():
        reject("unresolved relative open origin")
    path = lexical.resolve()
    if lexical.name == ".env" or path.name == ".env":
        reject("secret file")
    if lexical.is_relative_to(CODE) and not path.is_relative_to(CODE):
        reject("source symlink escape")
    if args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
        writable(lexical)
    elif path.is_relative_to(CODE) and not path.is_relative_to(OUTPUT):
        relative = str(path.relative_to(CODE))
        current = fingerprint(path)
        if relative in READS and READS[relative] != current:
            reject("input changed while reading")
        READS[relative] = current


if not frozen():
    raise RuntimeError("Native replay inputs changed before collection")
sys.addaudithook(audit)
PATCHES = ExitStack()
PATCHES.enter_context(patch.object(os, "open", guarded_os_open))
PATCHES.enter_context(patch.object(builtins, "open", guarded_builtin_open))
PATCHES.enter_context(patch.object(io, "open", guarded_io_open))
if OS_OPEN in os.supports_dir_fd:
    os.supports_dir_fd.add(guarded_os_open)


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    if spec_bytes() != SPEC_BYTES or not frozen():
        reject("source freeze changed")
    if any(fingerprint(CODE / name) != sha for name, sha in READS.items()):
        reject("read input changed")
    imports = {}
    for name, module in sys.modules.items():
        filename = getattr(module, "__file__", None)
        if filename is not None and (
            name == "lexinform"
            or name.startswith("lexinform.")
            or name == "tests"
            or name.startswith("tests.")
        ):
            path = Path(filename).resolve()
            if not path.is_relative_to(CODE):
                reject("wrong source import root")
            imports[name] = str(path.relative_to(CODE))
    if not imports or not any(name.startswith("lexinform.") for name in imports):
        reject("missing source import closure")
    if VIOLATIONS:
        raise RuntimeError("Native replay recorded prohibited operations")
    report = {
        "exitstatus": exitstatus,
        "spec_sha256": hashlib.sha256(SPEC_BYTES).hexdigest(),
        "imports": imports,
        "reads": READS,
        "violations": VIOLATIONS,
        "source_unchanged": True,
    }
    Path(os.environ["LEXINFORM_NATIVE_GUARD_OUTPUT"]).write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n"
    )
