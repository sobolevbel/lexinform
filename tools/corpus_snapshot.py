"""Collect dated Sejm listings and compare them with a frozen snapshot without replacing it."""

import argparse
import json
from pathlib import Path
from typing import Any

import httpx2 as httpx

from lexinform.adapters.browser_identity import BROWSER_HEADERS
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_health import local_path
from tools.corpus_http import fetch
from tools.corpus_runs import finish, new_run, verify, write_started
from tools.corpus_runtime import source_fingerprints

LISTINGS = ("prints", "processes", "bills")


def listing(client: httpx.Client, term: int, family: str) -> tuple[bytes, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    pages: set[str] = set()
    for _ in range(1000):
        suffix = "" if family == "prints" else f"?limit=500&offset={len(rows)}"
        response = fetch(client, f"https://api.sejm.gov.pl/sejm/term{term}/{family}{suffix}")
        page = json.loads(response.data)
        if not isinstance(page, list) or any(
            not isinstance(row, dict) or "number" not in row for row in page
        ):
            raise ValueError("Sejm listing must be an array of numbered records")
        requests.append(response.provenance)
        if page and digest(response.data) in pages:
            raise ValueError("Sejm pagination repeated a page")
        pages.add(digest(response.data))
        rows.extend(page)
        if not page or family == "prints":
            return json.dumps(rows, ensure_ascii=False).encode(), requests
    raise ValueError("Sejm pagination exceeded the bounded page count")


def grouped(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        result.setdefault(str(row["number"]), []).append(row)
    return {
        key: sorted(value, key=lambda row: json.dumps(row, sort_keys=True))
        for key, value in result.items()
    }


def supplementary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {**part, "parent": str(row["number"]), "parentTitle": row.get("title", "")}
        for row in rows
        for part in row.get("additionalPrints") or []
    ]


def snapshot(
    reference: Path, root: Path, identity: str, term: int, client: httpx.Client
) -> dict[str, Any]:
    if root.resolve().is_relative_to(reference.resolve()):
        raise ValueError("Snapshot run must be outside the reference directory")
    directory = new_run(root, identity, "sejm-snapshot")
    data_dir = directory / "data"
    data_dir.mkdir()
    started = {
        "term": term,
        "reference": str(reference.resolve()),
        "source_sha256": source_fingerprints(),
        "network": "explicit public Sejm metadata requests",
    }
    write_started(directory, started)
    rows: dict[str, Any] = {}
    inputs: dict[str, str] = {}
    for family in LISTINGS:
        row: dict[str, Any] = {"state": "unavailable"}
        try:
            old_path = local_path(reference.resolve(), f"{family}.json")
            old_bytes = read_artifact(old_path)
            inputs[family] = digest(old_bytes)
            old = grouped(json.loads(old_bytes))
            data, provenance = listing(client, term, family)
            current = json.loads(data)
            new = grouped(current)
            with (data_dir / f"{family}.json").open("xb") as stream:
                stream.write(data)
            row.update(
                state="collected",
                bytes=len(data),
                sha256=digest(data),
                records=len(current),
                requests=provenance,
                added=sorted(new.keys() - old.keys()),
                removed=sorted(old.keys() - new.keys()),
                changed=sorted(key for key in old.keys() & new.keys() if old[key] != new[key]),
            )
            if read_artifact(old_path) != old_bytes:
                raise ValueError("Reference changed during snapshot collection")
        except Exception as exc:
            row.update(state="unavailable", error=f"{type(exc).__name__}: {exc}")
        rows[family] = row
        print(f"Sejm {family}: {row['state']}", flush=True)
    failed = any(row["state"] != "collected" for row in rows.values())
    result = {
        "version": 1,
        "status": "failed" if failed else "recorded",
        "term": term,
        "scope": "Fresh listings and metadata diff; no process detail trees or documents",
        "observations": rows,
        "read_inputs": inputs,
        "source_sha256": started["source_sha256"],
        "exclusions": [
            "Live listings can change between pages; an atomic server snapshot is not claimed.",
            "No raw attachments, RPW bytes, model calls or production state changes.",
        ],
    }
    write_json(directory / "result.json", result)
    return finish(directory, "sejm-snapshot", returncode=int(failed), started=started)


def publish(source_run: Path, corpus: Path, name: str) -> Path:
    checked = verify(source_run)
    if checked["suite"] != "sejm-snapshot" or checked["status"] != "recorded":
        raise ValueError("Only a complete Sejm snapshot can be published")
    if not name or any(char not in "0123456789-abcdefghijklmnopqrstuvwxyz" for char in name):
        raise ValueError("Snapshot name must be a dated directory identifier")
    result = json.loads(read_artifact(source_run / "result.json"))
    destination = local_path(corpus.resolve(), f"sejm/snapshots/{name}")
    destination.mkdir()
    term_dir = destination / f"term{result['term']}"
    term_dir.mkdir()
    for family in LISTINGS:
        data = read_artifact(source_run / f"data/{family}.json")
        if digest(data) != result["observations"][family]["sha256"]:
            raise ValueError("Snapshot data SHA mismatch")
        with (term_dir / f"{family}.json").open("xb") as stream:
            stream.write(data)
    write_json(
        destination / "snapshot.json",
        {
            "source_manifest_sha256": digest(read_artifact(source_run / "manifest.json")),
            "term": result["term"],
        },
    )
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("download")
    collect.add_argument("--root", type=Path, required=True)
    collect.add_argument("--id", required=True)
    collect.add_argument("--reference", type=Path, required=True)
    collect.add_argument("--term", type=int, default=10)
    collect.add_argument("--allow-network", action="store_true", required=True)
    install = commands.add_parser("publish")
    install.add_argument("--source-run", type=Path, required=True)
    install.add_argument("--corpus", type=Path, required=True)
    install.add_argument("--name", required=True)
    args = parser.parse_args()
    if args.command == "download":
        with httpx.Client(headers=dict(BROWSER_HEADERS), trust_env=False, timeout=30) as client:
            result = snapshot(args.reference, args.root, args.id, args.term, client)
        print(json.dumps({key: result[key] for key in ("id", "status", "observations")}))
    else:
        print(publish(args.source_run.resolve(), args.corpus.resolve(strict=True), args.name))


if __name__ == "__main__":
    main()
