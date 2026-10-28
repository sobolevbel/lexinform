"""Read pinned artifacts and publish new JSON reports without replacing prior results."""

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_artifact(path: Path) -> bytes:
    if path.name == ".env" or path.resolve().name == ".env":
        raise ValueError("A credential file is not a replay artifact")
    return path.read_bytes()


def create_only(path: Path, data: bytes) -> None:
    if path.exists():
        if path.is_symlink() or path.read_bytes() != data:
            raise ValueError(f"Refusing to replace existing corpus bytes: {path.name}")
    else:
        with path.open("xb") as stream:
            stream.write(data)


def write_json(path: Path, value: Any) -> None:
    if path.is_symlink():
        raise ValueError("Artifact output must not be a symlink")
    with path.open("x") as stream:
        json.dump(value, stream, sort_keys=True, ensure_ascii=False, indent=2)
        stream.write("\n")


def observation_changes(previous: Mapping[str, object], current: Mapping[str, object]) -> list[str]:
    return [
        name
        for name in sorted(previous.keys() | current.keys())
        if name not in previous or name not in current or previous[name] != current[name]
    ]
