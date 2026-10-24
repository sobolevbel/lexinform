"""Measure available corpus files without creating an archive or inferring production demand."""

import argparse
import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

RAW_ROOTS = {
    "rcl": "documents/files",
    "sejm_print": "sejm/term10/files",
    "sejm_filing": "sejm/term10/supplements/files",
    "sejm_self_amendment": "sejm/term10/autopoprawki/files",
    "rpw": "rpw/term10/files",
}
EXCLUSIONS = (
    "An available-file inventory is not a record of production consumption or daily growth.",
    "PDF/Word/ZIP names do not prove format validity, legal role or that a scan has text.",
    "ZIP members are not expanded; physical containers are measured as downloaded.",
    "Research copies, derived files, text caches and the first-collection archive are excluded.",
    "Senate HTML and ELI JSON metadata do not establish available raw document bytes.",
    "Retention, backup replication, gzip sizes and backend cost are not measured or selected.",
)


def digest_file(path: Path) -> tuple[str, int]:
    before = path.stat()
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_ino,
    ) or size != after.st_size:
        raise ValueError(f"Corpus input changed during measurement: {path}")
    return digest.hexdigest(), size


def totals(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    records = list(rows)
    unique: dict[str, int] = {}
    for row in records:
        size = unique.setdefault(row["sha256"], row["bytes"])
        if size != row["bytes"]:
            raise ValueError("One content hash has inconsistent sizes")
    total = sum(row["bytes"] for row in records)
    distinct = sum(unique.values())
    return {
        "files": len(records),
        "bytes": total,
        "unique_contents": len(unique),
        "unique_bytes": distinct,
        "duplicate_files": len(records) - len(unique),
        "duplicate_bytes": total - distinct,
    }


def measure(corpus: Path) -> dict[str, Any]:
    root = corpus.resolve(strict=True)
    index = root / "INDEX.json"
    if not index.is_file() or index.is_symlink():
        raise ValueError("Corpus requires a regular INDEX.json")
    index_sha, _ = digest_file(index)
    metadata = json.loads(index.read_bytes())
    records: list[dict[str, Any]] = []
    inventory: list[dict[str, Any]] = []
    for family, relative in RAW_ROOTS.items():
        directory = root / relative
        if not directory.exists():
            inventory.append({"family": family, "path": relative, "state": "not_available"})
            continue
        if not directory.is_dir() or directory.resolve() != directory:
            raise ValueError(f"Raw corpus root must be a directory without symlinks: {relative}")
        paths = sorted(directory.rglob("*"))
        for path in paths:
            if path.is_symlink():
                raise ValueError(f"Raw corpus symlink is not an input: {path}")
            if path.is_dir():
                continue
            if not path.is_file() or path.name.startswith("."):
                raise ValueError(f"Unexpected raw corpus input: {path}")
            digest, size = digest_file(path)
            records.append(
                {
                    "family": family,
                    "path": str(path.relative_to(root)),
                    "extension": path.suffix.lower() or "(none)",
                    "bytes": size,
                    "sha256": digest,
                }
            )
        inventory.append(
            {
                "family": family,
                "path": relative,
                "state": "measured" if any(row["family"] == family for row in records) else "empty",
            }
        )
    if not records:
        raise ValueError("No raw corpus files are available in the declared roots")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        grouped[row["sha256"]].append(row)
    if digest_file(index)[0] != index_sha:
        raise ValueError("Corpus index changed during measurement")
    return {
        "version": 1,
        "scope": "Available corpus files in declared raw roots; no production forecast",
        "corpus": str(root),
        "index_sha256": index_sha,
        "index_generated": metadata.get("generated"),
        "index_counts": metadata.get("counts"),
        "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "exclusions": EXCLUSIONS,
        "inventory": inventory,
        "totals": totals(records),
        "by_family": {
            family: totals(row for row in records if row["family"] == family)
            for family in RAW_ROOTS
            if any(row["family"] == family for row in records)
        },
        "by_extension": {
            extension: totals(row for row in records if row["extension"] == extension)
            for extension in sorted({row["extension"] for row in records})
        },
        "cross_family_duplicates": [
            {"sha256": digest, "bytes": rows[0]["bytes"], "paths": [row["path"] for row in rows]}
            for digest, rows in sorted(grouped.items())
            if len({row["family"] for row in rows}) > 1
        ],
        "files": records,
    }


def run(corpus: Path, output: Path) -> None:
    root = corpus.resolve(strict=True)
    target = output.resolve()
    if target.is_relative_to(root):
        raise ValueError("Output must stay outside the read-only corpus")
    if output.is_symlink() or target.exists():
        raise FileExistsError(f"Output already exists: {output}")
    result = measure(root)
    with output.open("x") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    run(arguments.corpus, arguments.output)
    print(arguments.output)


if __name__ == "__main__":
    main()
