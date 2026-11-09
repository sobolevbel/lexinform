"""Read pinned artifacts and publish new JSON reports without replacing prior results."""

import errno
import hashlib
import json
import os
import stat
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_artifact(path: Path) -> bytes:
    if path.name == ".env" or path.resolve().name == ".env":
        raise ValueError("A credential file is not a replay artifact")
    return path.read_bytes()


@contextmanager
def _staged_artifact(path: Path) -> Iterator[Path]:
    with TemporaryDirectory(dir=path.parent, prefix=".corpus-stage-") as directory:
        yield Path(directory) / "artifact"


def create_only(path: Path, data: bytes) -> None:
    if not path.exists():
        with _staged_artifact(path) as staged:
            with staged.open("xb") as stream:
                stream.write(data)
            try:
                os.link(staged, path)
            except FileExistsError:
                pass
            else:
                return
    if path.is_symlink() or path.read_bytes() != data:
        raise ValueError(f"Refusing to replace existing corpus bytes: {path.name}")


def replace_if_unchanged(path: Path, before: bytes, data: bytes) -> None:
    with _staged_artifact(path) as staged:
        with staged.open("xb") as stream:
            os.fchmod(stream.fileno(), stat.S_IMODE(path.stat().st_mode))
            stream.write(data)
        if read_artifact(path) != before:
            raise ValueError(f"Index changed before publication: {path.name}")
        os.replace(staged, path)


def write_json(path: Path, value: Any) -> None:
    if path.is_symlink():
        raise ValueError("Artifact output must not be a symlink")
    if path.exists():
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), path)
    with _staged_artifact(path) as staged:
        with staged.open("x") as stream:
            json.dump(value, stream, sort_keys=True, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.link(staged, path)


def observation_changes(previous: Mapping[str, object], current: Mapping[str, object]) -> list[str]:
    return [
        name
        for name in sorted(previous.keys() | current.keys())
        if name not in previous or name not in current or previous[name] != current[name]
    ]
