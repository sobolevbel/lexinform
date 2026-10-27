"""Measure recorded physical references against a pinned inventory without inferring downloads."""

import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_runtime import source_fingerprints
from tools.saved_records import (
    BILL_SNAPSHOT_FIELDS,
    CONTEXT_RESULT_TABLES,
    SavedRecordIssue,
    SavedTable,
    is_saved_answer,
    saved_columns,
    saved_records,
)
from tools.sqlite_snapshot import open_snapshot

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_CATALOGUE = {
    **CONTEXT_RESULT_TABLES,
    "computation_results": SavedTable(("result_id",), ("result_json",)),
}


def inventory_contents(value: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if (
        not isinstance(value, dict)
        or value.get("version") != 1
        or not isinstance(value.get("files"), list)
        or not isinstance(value.get("corpus"), str)
        or not Path(value["corpus"]).is_absolute()
    ):
        raise ValueError("A version-1 raw inventory with an absolute corpus root is required")
    contents: dict[str, dict[str, Any]] = {}
    paths: set[str] = set()
    for row in value["files"]:
        if not isinstance(row, dict):
            raise ValueError("Inventory files must be objects")
        sha, size, path = row["sha256"], row["bytes"], row["path"]
        if not isinstance(sha, str) or not _SHA256.fullmatch(sha):
            raise ValueError("Inventory requires raw SHA-256 identities")
        if (
            type(size) is not int
            or size < 0
            or not isinstance(path, str)
            or path in paths
            or not isinstance(row["family"], str)
            or not row["family"]
        ):
            raise ValueError("Inventory requires nonnegative sizes and distinct paths")
        paths.add(path)
        content = contents.setdefault(sha, {"bytes": size, "paths": [], "families": set()})
        if content["bytes"] != size:
            raise ValueError("Inventory gives one content hash different sizes")
        content["paths"].append(path)
        content["families"].add(row["family"])
    return contents


def audit(database: Path, inventory: Path) -> dict[str, Any]:
    source = source_fingerprints()
    encoded = read_artifact(inventory)
    manifest = json.loads(encoded)
    contents = inventory_contents(manifest)
    counts: Counter[str] = Counter()
    refs: dict[str, set[tuple[str, str]]] = defaultdict(set)
    input_sizes: dict[str, int] = {}
    findings: list[dict[str, Any]] = []

    def reference(value: object, kind: str, ref: str, size: object = None) -> None:
        if not isinstance(value, str) or not _SHA256.fullmatch(value):
            counts["unknown_raw_reference"] += 1
            return
        counts["raw_reference_occurrences"] += 1
        counts[kind] += 1
        refs[value].add((kind, ref))
        if value in contents and type(size) is int and size != contents[value]["bytes"]:
            findings.append({"code": "recorded_size_mismatch", "ref": ref})

    with open_snapshot(database) as snapshot:
        tables = {
            row["name"]
            for row in snapshot.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for column in saved_columns(snapshot.connection, tables, catalogue=_CATALOGUE):
            counts["saved_columns"] += 1
            try:
                value = json.loads(column.raw)
            except ValueError, TypeError, RecursionError:
                findings.append({"code": "invalid_json", "ref": column.ref})
                continue
            for item in saved_records(value, column.ref, encoded_fields=BILL_SNAPSHOT_FIELDS):
                if isinstance(item, SavedRecordIssue):
                    findings.append({"code": item.code, "ref": item.ref})
                    continue
                node = item.value
                if is_saved_answer(node):
                    counts["saved_answer_occurrences"] += 1
                    selection = node.get("selection")
                    if node.get("text_source") == "scan" and not (
                        isinstance(selection, dict) and selection.get("scan_input")
                    ):
                        counts["legacy_scan_without_source_manifest"] += 1
                if {"question", "state", "parts"} <= node.keys():
                    counts["selections"] += 1
                    for field in ("parts", "observed_parts"):
                        parts = node.get(field, [])
                        if not isinstance(parts, list):
                            findings.append(
                                {"code": "invalid_parts", "ref": item.ref + "/" + field}
                            )
                            continue
                        for index, part in enumerate(parts):
                            if not isinstance(part, dict):
                                findings.append({"code": "invalid_part", "ref": item.ref})
                                continue
                            archive = bool(part.get("members"))
                            reference(
                                part.get("container_sha256" if archive else "sha256"),
                                field + ("_container" if archive else "_file"),
                                f"{item.ref}/{field}/{index}",
                            )
                if {"source_sha256", "source_bytes", "input_sha256", "input_bytes"} <= node.keys():
                    counts["scan_manifests"] += 1
                    reference(node["source_sha256"], "scan_source", item.ref, node["source_bytes"])
                    sha, size = node["input_sha256"], node["input_bytes"]
                    if (
                        isinstance(sha, str)
                        and _SHA256.fullmatch(sha)
                        and type(size) is int
                        and size > 0
                    ):
                        if input_sizes.setdefault(sha, size) != size:
                            findings.append({"code": "input_size_conflict", "ref": item.ref})
                    else:
                        findings.append({"code": "invalid_input_manifest", "ref": item.ref})
                if {"identity", "url", "role", "normalized_sha256", "file_sha256"} <= node.keys():
                    counts["review_documents"] += 1
                    reference(node["file_sha256"], "review_file", item.ref)
        if not snapshot.unchanged():
            raise ValueError("SQLite snapshot changed during measurement")
    if read_artifact(inventory) != encoded:
        raise ValueError("Raw inventory changed during measurement")
    if source != source_fingerprints():
        raise ValueError("Code changed during measurement")
    matched = set(refs) & contents.keys()
    observed = sum(
        counts[key] for key in ("selections", "scan_manifests", "saved_answer_occurrences")
    )
    return {
        "version": 1,
        "status": "failed" if findings else "recorded" if observed else "not_run",
        "database_sha256": snapshot.sha256,
        "inventory_sha256": digest(encoded),
        "source_sha256": source,
        "index_sha256": manifest.get("index_sha256"),
        "summary": dict(sorted(counts.items())),
        "unique_recorded_raw_hashes": len(refs),
        "matched_inventory_hashes": len(matched),
        "unmatched_recorded_hashes": len(set(refs) - matched),
        "matched_inventory_unique_bytes": sum(contents[sha]["bytes"] for sha in matched),
        "scan_input_manifest_unique_bytes": sum(input_sizes.values()),
        "scan_input_manifest_unique_hashes": len(input_sizes),
        "references": [
            {
                "sha256": sha,
                "availability": "inventory_match" if sha in contents else "not_in_inventory",
                "bytes": contents[sha]["bytes"] if sha in contents else None,
                "families": sorted(contents[sha]["families"]) if sha in contents else [],
                "refs": [{"kind": kind, "ref": ref} for kind, ref in sorted(locations)],
            }
            for sha, locations in sorted(refs.items())
        ],
        "findings": findings,
        "scope": "Physical fingerprints in saved selection/scan/review metadata, including copies.",
        "exclusions": [
            "A referenced hash is not a complete historical download or billing log.",
            "Text/law hashes and URLs are not physical file identities.",
            "Archive members use the recorded outer container; no separate member bytes.",
            "Inventory matches apply at measurement time; production retention is unknown.",
            "Scan input manifests describe bytes but do not prove they remain stored.",
            "No original-file reconstruction, archive write, source download or model call.",
        ],
    }


def run(database: Path, inventory: Path, output: Path) -> int:
    manifest = json.loads(read_artifact(inventory))
    inventory_contents(manifest)
    target = output.resolve()
    if (
        target in (database.resolve(), inventory.resolve())
        or output.exists()
        or output.is_symlink()
    ):
        raise ValueError("Output must be new and separate from inputs")
    if target.is_relative_to(Path(manifest["corpus"]).resolve()):
        raise ValueError("Output must stay outside the corpus")
    result = audit(database, inventory)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, result)
    return 1 if result["status"] == "failed" else 2 if result["status"] == "not_run" else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        code = run(args.database, args.inventory, args.output)
    except (OSError, sqlite3.Error, ValueError, KeyError) as exc:
        parser.error(str(exc))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
