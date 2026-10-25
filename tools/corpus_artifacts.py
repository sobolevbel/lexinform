"""Read pinned artifacts and publish new JSON reports without replacing prior results."""

import hashlib
import json
from pathlib import Path
from typing import Any


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_artifact(path: Path) -> bytes:
    if path.name == ".env" or path.resolve().name == ".env":
        raise ValueError("A credential file is not a replay artifact")
    return path.read_bytes()


def write_json(path: Path, value: Any) -> None:
    if path.is_symlink():
        raise ValueError("Artifact output must not be a symlink")
    with path.open("x") as stream:
        json.dump(value, stream, sort_keys=True, ensure_ascii=False, indent=2)
        stream.write("\n")
