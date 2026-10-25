"""Collect RCL gaps and install new originals while preserving prior corpus records."""

import argparse
import fcntl
import gzip
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx2 as httpx

from lexinform.adapters.browser_identity import BROWSER_HEADERS
from lexinform.models.rcl import text_role
from lexinform.sections import document_kind
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_extraction import bounded_reader
from tools.corpus_health import local_path
from tools.corpus_http import attachment_extension, fetch
from tools.corpus_rcl import document_id, projects
from tools.corpus_runs import finish, new_run, verify, write_started
from tools.corpus_runtime import CorpusReads, source_fingerprints


def collect(
    corpus: Path,
    health: Path,
    pin: str,
    root: Path,
    identity: str,
    client: httpx.Client,
    *,
    consultations: Path | None = None,
) -> dict[str, Any]:
    data = read_artifact(health)
    if digest(data) != pin:
        raise ValueError("Health report SHA mismatch")
    baseline = json.loads(data)
    if baseline.get("status") != "recorded":
        raise ValueError("Source collection requires a completed health report")
    wanted = {
        row["id"]: {"url": row["urls"][0], "purpose": "project"}
        for row in baseline["actions"]
        if row["action"] == "fetch_source" and row["urls"]
    }
    consultation_sha = None
    if consultations is not None:
        consultation_bytes = read_artifact(consultations)
        consultation_sha = digest(consultation_bytes)
        for refusal in json.loads(consultation_bytes)["errors"]:
            wanted[str(refusal["id"])] = {"url": refusal["url"], "purpose": "response"}
    if not wanted or any(
        not key.isdecimal() or document_id(row["url"]).decode() != key
        for key, row in wanted.items()
    ):
        raise ValueError("Source plan needs nonempty numeric IDs matching their URLs")
    reads = CorpusReads(corpus)
    metadata: dict[str, Any] = {}
    with reads.guard():
        original_index = local_path(corpus, "documents/index.json").read_bytes()
        if digest(original_index) != baseline["read_inputs"]["documents/index.json"]:
            raise ValueError("Document index changed since the source plan")
        for path, project in projects(corpus):
            for stage in project.stages:
                for folder in stage.folders:
                    for document in folder.documents:
                        key = str(document.id)
                        if key in wanted:
                            metadata[key] = {
                                **document.model_dump(mode="json"),
                                "project": project.id,
                                "folder": wanted[key]["purpose"],
                                "catalog_path": str(path.relative_to(corpus)),
                            }
    if root.resolve().is_relative_to(corpus.resolve()):
        raise ValueError("Collection output must be outside the corpus")
    directory = new_run(root, identity, "sources")
    downloads = directory / "downloads"
    downloads.mkdir()
    started = {
        "health_sha256": pin,
        "consultations_sha256": consultation_sha,
        "source_sha256": source_fingerprints(),
        "network": "explicit public source downloads",
    }
    write_started(directory, started)
    rows: dict[str, Any] = {}
    for key, source in sorted(wanted.items()):
        row: dict[str, Any] = {
            "state": "unavailable",
            "url": source["url"],
            "metadata": metadata.get(key),
        }
        try:
            response = fetch(client, source["url"])
            row["response"] = response.provenance
            declared = Path(urlsplit(source["url"]).path).suffix.removeprefix(".").lower()
            extension = attachment_extension(response.data, declared)
            target = downloads / f"{key}.{extension}"
            with target.open("xb") as stream:
                stream.write(response.data)
            row.update(
                state="downloaded",
                path=str(target.relative_to(directory)),
                sha256=digest(response.data),
                extension=extension,
                bytes=len(response.data),
            )
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        rows[key] = row
        print(f"source {key}: {row['state']}", flush=True)
    changed = reads.changed_inputs()
    result = {
        "version": 1,
        "status": "failed" if changed else "recorded",
        "scope": "Explicit source gaps; refusals stay unavailable",
        "observations": rows,
        "health_sha256": pin,
        "index_sha256": digest(original_index),
        "read_inputs": reads.initial_sha256,
        "changed_inputs": changed,
        "source_sha256": started["source_sha256"],
        "exclusions": [
            "No corpus writes, OCR or model calls; downloads require extraction and review."
        ],
    }
    write_json(directory / "result.json", result)
    return finish(directory, "sources", returncode=int(bool(changed)), started=started)


def create_only(path: Path, data: bytes) -> None:
    if path.exists():
        if path.is_symlink() or path.read_bytes() != data:
            raise ValueError(f"Refusing to replace existing corpus bytes: {path.name}")
    else:
        with path.open("xb") as stream:
            stream.write(data)


def install(corpus: Path, source_run: Path, root: Path, identity: str) -> dict[str, Any]:
    manifest = verify(source_run)
    if manifest["suite"] != "sources" or manifest["status"] != "recorded":
        raise ValueError("Installation requires a completed source collection")
    report = json.loads(read_artifact(source_run / "result.json"))
    downloads = {
        key: row for key, row in report["observations"].items() if row["state"] == "downloaded"
    }
    if not downloads:
        raise ValueError("No downloaded sources to install")
    if root.resolve().is_relative_to(corpus.resolve()):
        raise ValueError("Installation report must be outside the corpus")
    directory = new_run(root, identity, "install")
    started = {
        "source_manifest_sha256": digest(read_artifact(source_run / "manifest.json")),
        "source_sha256": source_fingerprints(),
        "network": "prohibited",
    }
    write_started(directory, started)
    reader = bounded_reader()
    rows: dict[str, Any] = {}
    index_path = local_path(corpus, "documents/index.json")
    lock_path = local_path(corpus, "documents/.corpus-install.lock")
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        before_bytes = index_path.read_bytes()
        with (directory / "index-before.json").open("xb") as snapshot:
            snapshot.write(before_bytes)
        old = json.loads(before_bytes)
        updated = dict(old)
        if digest(before_bytes) != report["index_sha256"] and not all(
            old.get(key, {}).get("file_sha256") == row["sha256"] for key, row in downloads.items()
        ):
            raise ValueError("Index changed since collection; re-plan before installing")
        for key, row in downloads.items():
            if not key.isdecimal() or row["metadata"] is None:
                raise ValueError("Source is missing a numeric identity or catalog metadata")
            source_path = local_path(source_run, row["path"])
            if row["path"] not in manifest["artifacts"]:
                raise ValueError("Downloaded source is absent from the run manifest")
            raw = read_artifact(source_path)
            if digest(raw) != row["sha256"]:
                raise ValueError("Downloaded source hash mismatch")
            if key in old:
                create_only(local_path(corpus, f"documents/files/{key}.{row['extension']}"), raw)
                rows[key] = {"state": "already_installed", "file_sha256": row["sha256"]}
                continue
            reads = CorpusReads(corpus)
            with reads.guard():
                extracted = reader.extract_document(raw)
            text = extracted.text
            cached = gzip.compress(text.encode(), mtime=0)
            create_only(local_path(corpus, f"documents/files/{key}.{row['extension']}"), raw)
            create_only(local_path(corpus, f"documents/text/{key}.txt.gz"), cached)
            meta = row["metadata"]
            record = {
                field: meta.get(field)
                for field in ("id", "project", "folder", "name", "url", "created", "author")
            }
            record.update(
                extension=row["extension"],
                bytes=len(raw),
                chars=len(text),
                text_role=text_role(meta["name"]),
                document_kind=document_kind(text) if text.strip() else None,
                opening=text[:800].strip(),
                extract_error=None,
                file_sha256=row["sha256"],
                extraction_sha256=digest(text.encode()),
            )
            updated[key] = record
            rows[key] = {
                "state": "installed",
                "file_sha256": row["sha256"],
                "text_sha256": digest(text.encode()),
                "chars": len(text),
            }
        if any(updated[key] != value for key, value in old.items()):
            raise ValueError("Installation must preserve every old index record")
        if updated != old:
            staged = local_path(corpus, "documents/.corpus-index-new.json")
            with staged.open("xb") as stream:
                stream.write(json.dumps(updated, ensure_ascii=False, indent=1).encode())
            if index_path.read_bytes() != before_bytes:
                staged.unlink()
                raise ValueError("Index changed before publication")
            os.replace(staged, index_path)
    result = {
        "status": "recorded",
        "scope": "Append-only new originals and caches",
        "observations": rows,
        "index_before_sha256": digest(before_bytes),
        "index_after_sha256": digest(index_path.read_bytes()),
        "old_records_unchanged": True,
        "exclusions": [
            "No replacement of old originals/caches; no model or production state changes."
        ],
    }
    write_json(directory / "result.json", result)
    return finish(directory, "install", returncode=0, started=started)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("download", "install"):
        command = commands.add_parser(name)
        command.add_argument("--corpus", type=Path, required=True)
        command.add_argument("--root", type=Path, required=True)
        command.add_argument("--id", required=True)
        if name == "download":
            command.add_argument("--health", type=Path, required=True)
            command.add_argument("--health-sha256", required=True)
            command.add_argument("--consultations", type=Path)
            command.add_argument("--allow-network", action="store_true", required=True)
        else:
            command.add_argument("--source-run", type=Path, required=True)
    args = parser.parse_args()
    corpus = args.corpus.resolve(strict=True)
    if args.command == "download":
        with httpx.Client(headers=dict(BROWSER_HEADERS), trust_env=False, timeout=15) as client:
            result = collect(
                corpus,
                args.health,
                args.health_sha256,
                args.root,
                args.id,
                client,
                consultations=args.consultations,
            )
    else:
        result = install(corpus, args.source_run.resolve(), args.root, args.id)
    print(json.dumps({key: result[key] for key in ("id", "suite", "status", "observations")}))


if __name__ == "__main__":
    main()
