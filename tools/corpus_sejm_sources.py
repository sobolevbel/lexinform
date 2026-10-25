"""Collect explicitly required Sejm filings and RPW files from pinned metadata snapshots."""

import argparse
import fcntl
import gzip
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from lexinform.adapters.browser_identity import BROWSER_HEADERS
from lexinform.models import supplement_kind
from lexinform.models.sejm import submission_pdf_url
from lexinform.sections import PAGE_BREAK, carries_the_document, document_kind, has_cover_letter
from lexinform.services.documents import MIN_TEXT_CHARS
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_collect import create_only
from tools.corpus_extraction import OPTIONS, bounded_reader
from tools.corpus_health import local_path
from tools.corpus_http import attachment_extension, fetch
from tools.corpus_runs import finish, new_run, verify, write_started
from tools.corpus_runtime import CorpusReads, code_version, source_fingerprints
from tools.corpus_snapshot import grouped, supplementary


def requirement(value: str) -> tuple[str, str]:
    identity, separator, reason = value.partition("=")
    if not separator or not reason.strip():
        raise ValueError("Each requirement needs ID=benchmark purpose")
    return identity, reason.strip()


def unchanged_inputs(corpus: Path, inputs: dict[str, str | None]) -> list[str]:
    changed = []
    for relative, sha256 in inputs.items():
        source = local_path(corpus, relative)
        actual = digest(read_artifact(source)) if source.is_file() else None
        if actual != sha256:
            changed.append(relative)
    return sorted(changed)


def catalogue(
    corpus: Path,
    current: Path,
    reference: Path,
    root: Path,
    identity: str,
    term: int,
    required_rpw: dict[str, str],
    required_supplements: dict[str, str],
    reserved_rpw: dict[str, str] | None = None,
) -> dict[str, Any]:
    if root.resolve().is_relative_to(corpus.resolve()):
        raise ValueError("Catalogue output must be outside the corpus")
    reserved = reserved_rpw or {}
    if required_rpw.keys() & reserved.keys():
        raise ValueError("A reserved holdout cannot also be an extraction requirement")
    if term < 1 or not required_rpw and not required_supplements and not reserved:
        raise ValueError("A positive term and explicit required cases are mandatory")
    reads = CorpusReads(corpus)
    with reads.guard():
        prints = json.loads(read_artifact(local_path(corpus, str(current / "prints.json"))))
        bills = json.loads(read_artifact(local_path(corpus, str(current / "bills.json"))))
        old_prints = json.loads(read_artifact(local_path(corpus, str(reference / "prints.json"))))
        rpw_index = json.loads(read_artifact(local_path(corpus, f"rpw/term{term}/index.json")))
    filings = supplementary(prints)
    old, new = grouped(supplementary(old_prints)), grouped(filings)
    rpw = grouped([row for row in bills if str(row["number"]).startswith("RPW/")])
    wanted: dict[str, Any] = {}
    for family, required in (
        ("rpw", required_rpw | reserved),
        ("supplement", required_supplements),
    ):
        for number, reason in sorted(required.items()):
            if not reason.strip():
                raise ValueError("Every required source needs a benchmark purpose")
            if family == "rpw":
                if not re.fullmatch(r"RPW/[0-9]+/[0-9]{4}", number) or number not in rpw:
                    raise ValueError("Required RPW must exist in the pinned bills snapshot")
                rows = rpw[number]
                attachments = [
                    (f"{number.replace('/', '-')}.pdf", submission_pdf_url(term, number))
                ]
                existing = rpw_index.get(number.replace("/", "-"))
                if number in reserved and (
                    existing is not None
                    or any(
                        row.get("print")
                        or row.get("applicantType")
                        not in {"DEPUTIES", "COMMITTEE", "CITIZENS", "PRESIDENT", "SENATE"}
                        for row in rows
                    )
                ):
                    raise ValueError(
                        "Reserved RPW must be unindexed, nongovernment and without a print"
                    )
            else:
                if not re.fullmatch(r"[0-9]+(?:-[A-Za-z0-9]+)*", number) or number not in new:
                    raise ValueError("Required supplement must exist in the pinned prints snapshot")
                rows = new[number]
                names = sorted({name for row in rows for name in row.get("attachments") or []})
                if any(
                    Path(name).name != name or "\\" in name or name in ("", ".", "..")
                    for name in names
                ):
                    raise ValueError("Attachment names must be plain filenames")
                attachments = [
                    (
                        name,
                        f"https://api.sejm.gov.pl/sejm/term{term}/prints/"
                        f"{number}/{quote(name, safe='')}",
                    )
                    for name in names
                ]
                record = local_path(corpus, f"sejm/term{term}/supplements/records/{number}.json")
                with reads.guard():
                    reads.require(record)
                    existing = json.loads(read_artifact(record)) if record.exists() else None
            wanted[f"{family}:{number}"] = {
                "family": family,
                "number": number,
                "reason": reason,
                "metadata": rows,
                "attachments": [{"name": name, "url": url} for name, url in attachments],
                "existing_record": existing,
                "reserved_holdout": number in reserved,
            }
    directory = new_run(root, identity, "sejm-catalogue")
    started = {
        "source_sha256": source_fingerprints(),
        "code_sha": code_version(),
        "network": "prohibited",
        "command": [
            sys.executable,
            "-m",
            "tools.corpus_sejm_sources",
            "catalogue",
            "--corpus",
            str(corpus.resolve()),
            "--root",
            str(root.resolve()),
            "--id",
            identity,
            "--current",
            str(current),
            "--reference",
            str(reference),
            "--term",
            str(term),
            *[
                token
                for number, reason in sorted(required_rpw.items())
                for token in ("--rpw", f"{number}={reason}")
            ],
            *[
                token
                for number, reason in sorted(required_supplements.items())
                for token in ("--supplement", f"{number}={reason}")
            ],
            *[
                token
                for number, reason in sorted(reserved.items())
                for token in ("--reserve-rpw", f"{number}={reason}")
            ],
        ],
    }
    write_started(directory, started)
    write_json(directory / "catalogue.json", filings)
    changed = reads.changed_inputs()
    result = {
        "version": 1,
        "status": "failed" if changed else "recorded",
        "term": term,
        "scope": "Complete supplementary metadata and explicit benchmark source requirements",
        "observations": wanted,
        "catalogue": {"rows": len(filings), "unique_ids": len(new)},
        "diff": {
            "added": sorted(new.keys() - old.keys()),
            "removed": sorted(old.keys() - new.keys()),
            "changed": sorted(key for key in old.keys() & new.keys() if old[key] != new[key]),
        },
        "missing_rpw_records": sorted(n for n in rpw if n.replace("/", "-") not in rpw_index),
        "read_inputs": reads.initial_sha256,
        "changed_inputs": changed,
        "source_sha256": started["source_sha256"],
        "exclusions": [
            "Listing absence does not prove server deletion; catalogues are not process trees.",
            "Requirements select downloads, not legal roles or a complete source population.",
        ],
    }
    write_json(directory / "result.json", result)
    return finish(directory, "sejm-catalogue", returncode=int(bool(changed)), started=started)


def collect(
    corpus: Path, plan: Path, root: Path, identity: str, client: httpx.Client
) -> dict[str, Any]:
    checked = verify(plan)
    if checked["suite"] != "sejm-catalogue" or checked["status"] != "recorded":
        raise ValueError("Downloads require a completed source catalogue")
    report = json.loads(read_artifact(plan / "result.json"))
    if unchanged_inputs(corpus, report["read_inputs"]):
        raise ValueError("Required source inputs changed since planning")
    if root.resolve().is_relative_to(corpus.resolve()):
        raise ValueError("Download output must be outside the corpus")
    directory = new_run(root, identity, "sejm-sources")
    (directory / "downloads").mkdir()
    started = {
        "source_sha256": source_fingerprints(),
        "code_sha": code_version(),
        "catalogue_manifest_sha256": digest(read_artifact(plan / "manifest.json")),
        "network": "explicit sequential public source requests",
        "command": [
            sys.executable,
            "-m",
            "tools.corpus_sejm_sources",
            "download",
            "--corpus",
            str(corpus.resolve()),
            "--root",
            str(root.resolve()),
            "--id",
            identity,
            "--plan",
            str(plan.resolve()),
            "--allow-network",
        ],
    }
    write_started(directory, started)
    rows: dict[str, Any] = {}
    for key, source in report["observations"].items():
        row = {**source, "state": "unavailable", "downloads": []}
        if source["existing_record"] is not None:
            row["state"] = "already_recorded"
        else:
            for attachment in source["attachments"]:
                part: dict[str, Any] = {**attachment, "state": "unavailable"}
                try:
                    response = fetch(client, attachment["url"])
                    part["response"] = response.provenance
                    extension = attachment_extension(
                        response.data, Path(attachment["name"]).suffix[1:].lower()
                    )
                    if extension != "pdf":
                        raise ValueError(
                            "This collector only installs the Sejm PDF source families"
                        )
                    filename = (
                        attachment["name"]
                        if source["family"] == "rpw"
                        else f"{source['number']}__{attachment['name']}"
                    )
                    destination = directory / "downloads" / filename
                    create_only(destination, response.data)
                    part.update(
                        state="downloaded",
                        path=str(destination.relative_to(directory)),
                        bytes=len(response.data),
                        sha256=digest(response.data),
                    )
                except Exception as exc:
                    part["error"] = f"{type(exc).__name__}: {exc}"
                row["downloads"].append(part)
            if row["downloads"] and all(p["state"] == "downloaded" for p in row["downloads"]):
                row["state"] = "downloaded"
        rows[key] = row
        print(f"Sejm source {key}: {row['state']}", flush=True)
    changed = unchanged_inputs(corpus, report["read_inputs"])
    write_json(
        directory / "result.json",
        {
            "version": 1,
            "status": "failed" if changed else "recorded",
            "term": report["term"],
            "scope": "Explicit required RPW and supplementary PDFs; refusals stay unavailable",
            "observations": rows,
            "read_inputs": report["read_inputs"],
            "changed_inputs": changed,
            "catalogue_manifest_sha256": started["catalogue_manifest_sha256"],
            "source_sha256": started["source_sha256"],
            "exclusions": ["No corpus writes, OCR, model calls or inferred legal labels."],
        },
    )
    return finish(directory, "sejm-sources", returncode=int(bool(changed)), started=started)


def publish_catalogue(corpus: Path, source_run: Path, name: str) -> Path:
    manifest = verify(source_run)
    if manifest["suite"] != "sejm-catalogue" or manifest["status"] != "recorded":
        raise ValueError("Only a completed supplementary catalogue can be published")
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", name):
        raise ValueError("Catalogue destination needs a dated snapshot identifier")
    result = json.loads(read_artifact(source_run / "result.json"))
    data = read_artifact(source_run / "catalogue.json")
    if digest(data) != manifest["artifacts"]["catalogue.json"]["sha256"]:
        raise ValueError("Supplementary catalogue SHA mismatch")
    destination = local_path(corpus, f"sejm/snapshots/{name}/term{result['term']}")
    if not destination.is_dir():
        raise ValueError("Publish the Sejm metadata snapshot before its supplementary catalogue")
    create_only(destination / "supplements.json", data)
    provenance = {
        "source_manifest_sha256": digest(read_artifact(source_run / "manifest.json")),
        "read_inputs": result["read_inputs"],
        "scope": "Supplementary metadata derived from pinned listings; no process trees",
        "code_sha": code_version(),
        "publisher_source_sha256": source_fingerprints(),
        "command": [
            sys.executable,
            "-m",
            "tools.corpus_sejm_sources",
            "publish-catalogue",
            "--corpus",
            str(corpus.resolve()),
            "--source-run",
            str(source_run.resolve()),
            "--name",
            name,
        ],
    }
    create_only(
        destination / "supplements.snapshot.json",
        (json.dumps(provenance, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode(),
    )
    return destination / "supplements.json"


def install(corpus: Path, source_run: Path, root: Path, identity: str) -> dict[str, Any]:
    manifest = verify(source_run)
    if manifest["suite"] != "sejm-sources" or manifest["status"] != "recorded":
        raise ValueError("Installation needs a completed Sejm source collection")
    report = json.loads(read_artifact(source_run / "result.json"))
    wanted = {
        key: row
        for key, row in report["observations"].items()
        if row["state"] == "downloaded" and not row["reserved_holdout"]
    }
    if not wanted or root.resolve().is_relative_to(corpus.resolve()):
        raise ValueError("Installation needs downloaded sources and an external output root")
    directory = new_run(root, identity, "install")
    started = {
        "source_sha256": source_fingerprints(),
        "code_sha": code_version(),
        "source_manifest_sha256": digest(read_artifact(source_run / "manifest.json")),
        "network": "prohibited",
        "extraction_options": OPTIONS,
        "command": [
            sys.executable,
            "-m",
            "tools.corpus_sejm_sources",
            "install",
            "--corpus",
            str(corpus.resolve()),
            "--root",
            str(root.resolve()),
            "--id",
            identity,
            "--source-run",
            str(source_run.resolve()),
        ],
    }
    write_started(directory, started)
    term = report["term"]
    reader = bounded_reader()
    rows: dict[str, Any] = {}
    lock_path = local_path(corpus, "sejm/.corpus-source-install.lock")
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        index_path = local_path(corpus, f"rpw/term{term}/index.json")
        before = read_artifact(index_path)
        create_only(directory / "rpw-index-before.json", before)
        old = json.loads(before)
        updated = dict(old)
        expected_index = report["read_inputs"][f"rpw/term{term}/index.json"]
        if digest(before) != expected_index and not all(
            row["family"] != "rpw"
            or old.get(row["number"].replace("/", "-"), {}).get("source_hashes")
            == [part["sha256"] for part in row["downloads"]]
            for row in wanted.values()
        ):
            raise ValueError("RPW index changed since planning; re-plan before installing")
        for key, row in wanted.items():
            number = row["number"]
            slug = number.replace("/", "-")
            base = f"rpw/term{term}" if row["family"] == "rpw" else f"sejm/term{term}/supplements"
            cache = local_path(corpus, f"{base}/text/{slug}.txt.gz")
            record = old.get(slug) if row["family"] == "rpw" else None
            record_path = (
                local_path(corpus, f"{base}/records/{slug}.json")
                if row["family"] == "supplement"
                else None
            )
            if record_path is not None and record_path.exists():
                record = json.loads(read_artifact(record_path))
            extracted_parts = []
            raw_parts = []
            pages = 0
            for part in row["downloads"]:
                if part["path"] not in manifest["artifacts"]:
                    raise ValueError("Source is absent from the run manifest")
                raw = read_artifact(local_path(source_run, part["path"]))
                if digest(raw) != part["sha256"] or attachment_extension(raw, "pdf") != "pdf":
                    raise ValueError("Source PDF identity mismatch")
                filename = Path(part["path"]).name
                destination = local_path(corpus, f"{base}/files/{filename}")
                if record is not None and not destination.is_file():
                    raise ValueError(
                        "Existing record without its original needs a separate repair plan"
                    )
                create_only(destination, raw)
                raw_parts.append(part["sha256"])
                if record is None:
                    reads = CorpusReads(corpus)
                    with reads.guard():
                        extracted_parts.append(reader.extract_document(raw))
                        pages += reader.pages(raw)
            if record is not None:
                if (
                    not cache.is_file()
                    or record.get("source_hashes") != raw_parts
                    or digest(gzip.decompress(read_artifact(cache)))
                    != record.get("extraction_sha256")
                ):
                    raise ValueError("Existing source record differs; refuse replacement")
                rows[key] = {"state": "already_installed", "source_hashes": raw_parts}
                continue
            text = PAGE_BREAK.join(part.text for part in extracted_parts)
            create_only(cache, gzip.compress(text.encode(), mtime=0))
            metadata = row["metadata"][0]
            record = {
                **metadata,
                "number": number,
                "url": row["downloads"][0]["url"],
                "bytes": sum(part["bytes"] for part in row["downloads"]),
                "chars": len(text),
                "pages": pages,
                "chars_per_page": round(len(text) / max(pages, 1), 1),
                "cover_letter": has_cover_letter(text),
                "carries_document": carries_the_document(
                    text, min_chars=MIN_TEXT_CHARS, pages=pages
                ),
                "document_kind": document_kind(text) if text.strip() else None,
                "source_hashes": raw_parts,
                "extraction_sha256": digest(text.encode()),
                "source_manifest_sha256": digest(read_artifact(source_run / "manifest.json")),
                "extraction_options": OPTIONS,
                "part_selections": [
                    part.selection.model_dump(mode="json") for part in extracted_parts
                ],
                "benchmark_reason": row["reason"],
                "page_heads": [
                    " | ".join(page.strip().splitlines()[:3])[:160]
                    for page in text.split(PAGE_BREAK)[:40]
                ],
            }
            if row["family"] == "rpw":
                updated[slug] = record
            else:
                kind = supplement_kind(metadata.get("title", ""))
                record.update(
                    parents=sorted({m["parent"] for m in row["metadata"]}),
                    supplement_kind=str(kind) if kind else None,
                )
                assert record_path is not None, "Supplement records always have a destination path"
                write_json(record_path, record)
            rows[key] = {
                "state": "installed",
                "chars": len(text),
                "pages": pages,
                "source_hashes": raw_parts,
                "extraction_sha256": digest(text.encode()),
            }
        if any(updated[key] != value for key, value in old.items()):
            raise ValueError("Old RPW records must remain unchanged")
        if updated != old:
            staged = local_path(corpus, f"rpw/term{term}/.corpus-index-new.json")
            create_only(staged, json.dumps(updated, ensure_ascii=False, indent=1).encode())
            if read_artifact(index_path) != before:
                staged.unlink()
                raise ValueError("RPW index changed during installation")
            os.replace(staged, index_path)
    write_json(
        directory / "result.json",
        {
            "status": "recorded",
            "scope": "Append-only required Sejm source installation",
            "observations": rows,
            "rpw_index_before_sha256": digest(before),
            "rpw_index_after_sha256": digest(read_artifact(index_path)),
            "old_records_unchanged": True,
            "reserved_holdout_excluded": sorted(
                key for key, row in report["observations"].items() if row["reserved_holdout"]
            ),
            "exclusions": [
                "No replacement of historical inputs, process trees, OCR or legal adjudication."
            ],
        },
    )
    return finish(directory, "install", returncode=0, started=started)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("catalogue", "download", "install", "publish-catalogue"):
        command = commands.add_parser(name)
        command.add_argument("--corpus", type=Path, required=True)
        if name != "publish-catalogue":
            command.add_argument("--root", type=Path, required=True)
            command.add_argument("--id", required=True)
        if name == "catalogue":
            command.add_argument("--current", type=Path, required=True)
            command.add_argument("--reference", type=Path, required=True)
            command.add_argument("--term", type=int, default=10)
            command.add_argument("--rpw", action="append", default=[])
            command.add_argument("--reserve-rpw", action="append", default=[])
            command.add_argument("--supplement", action="append", default=[])
        elif name == "download":
            command.add_argument("--plan", type=Path, required=True)
            command.add_argument("--allow-network", action="store_true", required=True)
        else:
            command.add_argument("--source-run", type=Path, required=True)
            if name == "publish-catalogue":
                command.add_argument("--name", required=True)
    args = parser.parse_args()
    corpus = args.corpus.resolve(strict=True)
    if args.command == "catalogue":
        result = catalogue(
            corpus,
            args.current,
            args.reference,
            args.root,
            args.id,
            args.term,
            dict(requirement(v) for v in args.rpw),
            dict(requirement(v) for v in args.supplement),
            dict(requirement(v) for v in args.reserve_rpw),
        )
    elif args.command == "download":
        with httpx.Client(headers=dict(BROWSER_HEADERS), timeout=30, trust_env=False) as client:
            result = collect(corpus, args.plan.resolve(), args.root, args.id, client)
    elif args.command == "install":
        result = install(corpus, args.source_run.resolve(), args.root, args.id)
    else:
        print(publish_catalogue(corpus, args.source_run.resolve(), args.name))
        return
    print(json.dumps({key: result[key] for key in ("id", "status", "observations")}))


if __name__ == "__main__":
    main()
