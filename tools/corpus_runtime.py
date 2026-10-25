"""Share read-only replay guards and code provenance without importing replay suites."""

import builtins
import gzip
import hashlib
import io
import os
import socket
import subprocess
import sys
import tarfile
from contextlib import ExitStack
from pathlib import Path
from typing import IO, Any
from unittest.mock import patch

from tools.corpus_artifacts import digest


class CorpusReads:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.paths: set[str] = set()
        self.active: set[str] = set()
        self.missing: set[str] = set()
        self.initial_sha256: dict[str, str | None] = {}
        self.allowed: tuple[Path, ...] = (
            Path(__file__).resolve().parents[1],
            Path(sys.prefix).resolve(),
            Path(sys.base_prefix).resolve(),
        )
        self._open = builtins.open
        self._io_open = io.open

    def require(self, path: Path) -> None:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError(f"Corpus input escapes root: {path}")
        relative = str(resolved.relative_to(self.root))
        self.paths.add(relative)
        self.active.add(relative)
        if relative not in self.initial_sha256:
            digest = None
            if resolved.is_file():
                with self._open(resolved, "rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
            self.initial_sha256[relative] = digest
        if not resolved.is_file():
            self.missing.add(relative)

    def _checked(self, file: Any, mode: str) -> None:
        if any(flag in mode for flag in "wax+"):
            raise PermissionError("External replay inputs are read-only; writes are prohibited")
        if isinstance(file, int):
            return
        lexical = Path(os.fsdecode(file)).absolute()
        path = lexical.resolve()
        if lexical.is_relative_to(self.root) and not path.is_relative_to(self.root):
            raise PermissionError(f"Corpus symlink escapes root: {lexical}")
        if path.name == ".env":
            raise PermissionError("External replay must not read .env")
        if path.is_relative_to(self.root):
            self.require(path)
        elif not any(path.is_relative_to(root) for root in self.allowed):
            raise PermissionError(f"External replay cannot read outside its corpus: {path}")

    def open(self, file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> IO[Any]:
        self._checked(file, mode)
        return self._open(file, mode, *args, **kwargs)

    def io_open(self, file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> IO[Any]:
        self._checked(file, mode)
        return self._io_open(file, mode, *args, **kwargs)

    def guard(self) -> ExitStack:
        stack = ExitStack()
        stack.enter_context(patch.object(builtins, "open", self.open))
        stack.enter_context(patch.object(io, "open", self.io_open))
        for owner, name in (
            (socket.socket, "connect"),
            (socket.socket, "connect_ex"),
            (socket.socket, "sendto"),
            (socket, "create_connection"),
            (socket, "getaddrinfo"),
        ):
            stack.enter_context(patch.object(owner, name, blocked_network))
        return stack

    def changed_inputs(self) -> list[str]:
        return sorted(
            relative
            for relative, initial in self.initial_sha256.items()
            if (
                hashlib.sha256((self.root / relative).read_bytes()).hexdigest()
                if (self.root / relative).is_file()
                else None
            )
            != initial
        )


def blocked_network(*args: Any, **kwargs: Any) -> None:
    raise RuntimeError("External corpus replay prohibits network/model access")


def code_version() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def source_fingerprints() -> dict[str, str]:
    root = Path(__file__).resolve().parents[1]
    paths = [root / "uv.lock"]
    for directory in ("src", "tests", "tools"):
        paths.extend(sorted((root / directory).rglob("*.py")))
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths
    }


def code_dirty() -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode != 0 or bool(result.stdout.strip())


def capture_code(directory: Path, fingerprints: dict[str, str]) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for relative, expected in sorted(fingerprints.items()):
            path = root / relative
            if (
                path.resolve() != path
                or not path.resolve().is_relative_to(root)
                or path.name == ".env"
            ):
                raise ValueError("Code snapshot path escapes the project")
            raw = path.read_bytes()
            if digest(raw) != expected:
                raise ValueError("Code changed before its snapshot was captured")
            entry = tarfile.TarInfo(relative)
            entry.size, entry.mode = len(raw), 0o644
            archive.addfile(entry, io.BytesIO(raw))
    data = gzip.compress(buffer.getvalue(), mtime=0)
    target = directory / "code.tar.gz"
    with target.open("xb") as stream:
        stream.write(data)
    return {"path": target.name, "sha256": digest(data), "files": len(fingerprints)}
