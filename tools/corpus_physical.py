"""Inspect physical files, saved text caches and recorded gaps across declared corpus families."""

import argparse
import io
import json
import logging
import sys
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import quote
from xml.etree import ElementTree as ET

import olefile
from pypdf import PdfReader

from tools.corpus_artifacts import digest, write_json
from tools.corpus_extraction import MAX_BYTES
from tools.corpus_health import local_path, text_state
from tools.corpus_runtime import CorpusReads, source_fingerprints
from tools.measure_corpus_files import RAW_ROOTS, digest_file, measure, totals

TEXT_ROOTS = {family: raw.removesuffix("files") + "text" for family, raw in RAW_ROOTS.items()}
SENATE_ROOT = "sejm/supplements/2026-10-16-senate-resolutions"
TEXT_ROOTS["senate_resolution"] = f"{SENATE_ROOT}/texts"
MAX_MEMBERS = 2048
MAX_EXPANDED = 128_000_000


def container_state(data: bytes) -> dict[str, Any]:
    if not data:
        return {"state": "empty", "format": "unknown"}
    head = data[:4096].lstrip().lower()
    if head.startswith((b"<!doctype html", b"<html")):
        return {"state": "html_response", "format": "html"}
    kind = "unknown"
    try:
        if data.startswith(b"%PDF-"):
            kind = "pdf"
            reader = PdfReader(io.BytesIO(data), strict=True)
            if reader.is_encrypted:
                return {"state": "encrypted", "format": kind}
            return {"state": "valid_container", "format": kind, "pages": len(reader.pages)}
        if data.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
            kind = "zip"
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                members = archive.infolist()
                expanded = sum(item.file_size for item in members)
                names = {item.filename for item in members}
                if "word/document.xml" in names:
                    kind = "docx"
                elif "content.xml" in names:
                    kind = "odf"
                if len(members) > MAX_MEMBERS or expanded > MAX_EXPANDED:
                    return {"state": "limit_exceeded", "format": kind, "members": len(members)}
                if any(item.flag_bits & 1 for item in members):
                    return {"state": "encrypted", "format": kind}
                for item in members:
                    if item.is_dir():
                        continue
                    with archive.open(item) as stream:
                        consumed = 0
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            consumed += len(chunk)
                            if consumed > item.file_size or consumed > MAX_EXPANDED:
                                raise ValueError("Expanded member exceeds declared size")
                return {
                    "state": "valid_container",
                    "format": kind,
                    "members": len(members),
                    "expanded_bytes": expanded,
                }
        if data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            kind = "ole"
            with olefile.OleFileIO(
                io.BytesIO(data), raise_defects=olefile.DEFECT_INCORRECT
            ) as archive:
                return {
                    "state": "valid_container",
                    "format": kind,
                    "streams": len(archive.listdir()),
                }
        if head.startswith(b"{\\rtf"):
            return {"state": "recognized_unchecked", "format": "rtf"}
        if head.startswith((b"<?xml", b"<w:")):
            kind = "xml"
            if b"<!entity" in data.lower() or b"<!doctype" in data.lower():
                return {"state": "declaration_refused", "format": kind}
            ET.fromstring(data)
            return {"state": "valid_container", "format": kind}
        return {"state": "unrecognized", "format": kind}
    except NotImplementedError as exc:
        return {"state": "parser_unsupported", "format": kind, "error": type(exc).__name__}
    except Exception as exc:
        state = "parse_failed" if kind in {"pdf", "ole"} else "invalid_container"
        return {"state": state, "format": kind, "error": type(exc).__name__}


def senate_sources(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    relative = f"{SENATE_ROOT}/catalog.json"
    path = local_path(root, relative)
    metadata = {"path": relative, "state": "present" if path.is_file() else "missing"}
    expected: dict[str, Any] = {}
    if not path.is_file():
        return expected, metadata
    for record in json.loads(path.read_bytes())["records"]:
        for name in record["attachments"]:
            term, number = record["term"], str(record["number"])
            extension = Path(name).suffix.lower()
            if extension not in {".pdf", ".docx", ".doc", ".docm", ".odt"}:
                raise ValueError("Senate catalog contains an unsupported source extension")
            url = (
                f"https://api.sejm.gov.pl/sejm/term{term}/prints/"
                f"{quote(number, safe='')}/{quote(name, safe='')}"
            )
            identity = digest(url.encode())
            raw_path = f"{SENATE_ROOT}/files/{identity}{extension}"
            expected[raw_path] = {
                "family": "senate_resolution",
                "metadata": relative,
                "url": url,
                "text_path": f"{SENATE_ROOT}/texts/{identity}.txt.gz",
            }
            status = local_path(root, f"{SENATE_ROOT}/status/{identity}.json")
            if status.is_file():
                saved = json.loads(status.read_bytes())
                expected[raw_path]["recorded_sha256"] = saved.get("sha256")
    return expected, metadata


def recorded_sources(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    expected: dict[str, Any] = {}
    metadata: dict[str, Any] = {}

    def add(family: str, filename: str, text_name: str, source: str) -> None:
        if Path(filename).name != filename or Path(text_name).name != text_name:
            raise ValueError("Recorded corpus filename must be a single local name")
        relative = f"{RAW_ROOTS[family]}/{filename}"
        expected[relative] = {
            "family": family,
            "metadata": source,
            "text_path": f"{TEXT_ROOTS[family]}/{text_name}.txt.gz",
        }

    for family, relative in (("rcl", "documents/index.json"), ("rpw", "rpw/term10/index.json")):
        path = local_path(root, relative)
        metadata[family] = {"path": relative, "state": "present" if path.is_file() else "missing"}
        if not path.is_file():
            continue
        for identity, row in json.loads(path.read_bytes()).items():
            extension = row.get("extension") if family == "rcl" else "pdf"
            if extension:
                add(family, f"{identity}.{extension}", identity, relative)

    for family, relative in (
        ("sejm_print", "sejm/term10/prints"),
        ("sejm_self_amendment", "sejm/term10/autopoprawki/records"),
    ):
        directory = local_path(root, relative)
        metadata[family] = {
            "path": relative,
            "state": "present" if directory.is_dir() else "missing",
        }
        for path in sorted(directory.glob("*.json")):
            local_path(root, str(path.relative_to(root)))
            row = json.loads(path.read_bytes())
            number = str(row.get("number", path.stem))
            for attachment in row.get("attachments", []):
                name = attachment["name"] if isinstance(attachment, dict) else attachment
                filename = f"{number}__{name}"
                text_name = filename if family == "sejm_self_amendment" else number
                add(family, filename, text_name, str(path.relative_to(root)))

    catalogue = local_path(root, "sejm/term10/supplements/catalogue.json")
    records = local_path(root, "sejm/term10/supplements/records")
    metadata["sejm_filing"] = {
        "path": str(catalogue.relative_to(root)),
        "state": "present" if catalogue.is_file() and records.is_dir() else "missing",
    }
    if catalogue.is_file():
        rows = {str(row["number"]): row for row in json.loads(catalogue.read_bytes())}
        for path in sorted(records.glob("*.json")):
            local_path(root, str(path.relative_to(root)))
            record = json.loads(path.read_bytes())
            number = str(record["number"])
            attachments = rows.get(number, {}).get("attachments", [])
            if attachments and record.get("kind") != "no-attachment":
                add(
                    "sejm_filing",
                    f"{number}__{attachments[0]}",
                    number,
                    str(path.relative_to(root)),
                )
    return expected, metadata


def inspect(corpus: Path) -> dict[str, Any]:
    root = corpus.resolve(strict=True)
    before = source_fingerprints()
    raw = measure(root)
    reads = CorpusReads(root)
    records: dict[str, Any] = {}
    texts: dict[str, Any] = {}
    inspected: dict[str, dict[str, Any]] = {}
    with reads.guard():
        expected, metadata = recorded_sources(root)
        senate, metadata["senate_resolution"] = senate_sources(root)
        expected.update(senate)
        for relative in senate:
            path = local_path(root, relative)
            if path.is_file():
                sha, size = digest_file(path)
                raw["files"].append(
                    {
                        "family": "senate_resolution",
                        "path": relative,
                        "extension": path.suffix,
                        "bytes": size,
                        "sha256": sha,
                    }
                )
        senate_available = sum(row["family"] == "senate_resolution" for row in raw["files"])
        raw["inventory"].append(
            {
                "family": "senate_resolution",
                "path": f"{SENATE_ROOT}/files",
                "state": "measured" if senate_available else "not_available",
                "catalog_only": True,
            }
        )
        for number, item in enumerate(raw["files"], 1):
            relative = item["path"]
            path = local_path(root, relative)
            row = {**item, **expected.pop(relative, {})}
            if item["bytes"] > MAX_BYTES:
                reads.require(path)
                row.update(
                    state="limit_exceeded", format="uninspected", reason="Raw input exceeds 32 MB"
                )
            else:
                data = path.read_bytes()
                if item["sha256"] not in inspected:
                    inspected[item["sha256"]] = container_state(data)
                row.update(inspected[item["sha256"]])
            if reads.initial_sha256[relative] != item["sha256"]:
                raise ValueError("Raw input changed after inventory")
            if row.get("recorded_sha256") and row["recorded_sha256"] != item["sha256"]:
                row["container_state"] = row["state"]
                row["state"] = "recorded_hash_mismatch"
            records[relative] = row
            if number % 1000 == 0:
                print(f"physical inventory {number}/{len(raw['files'])}", file=sys.stderr)
        for relative, item in expected.items():
            path = local_path(root, relative)
            reads.require(path)
            records[relative] = {
                **item,
                "path": relative,
                "state": "missing",
                "format": "unavailable",
            }
        text_paths = {row["text_path"] for row in records.values() if row.get("text_path")}
        for relative in TEXT_ROOTS.values():
            directory = local_path(root, relative)
            for path in directory.rglob("*"):
                if path.is_symlink():
                    raise ValueError("Text inventory must not contain symlinks")
                if path.is_file():
                    if not path.name.endswith(".txt.gz"):
                        raise ValueError(f"Unexpected saved text input: {path}")
                    text_paths.add(str(path.relative_to(root)))
        for relative in sorted(text_paths):
            path = local_path(root, relative)
            reads.require(path)
            texts[relative] = {
                "state": text_state(path, check_text=True),
                "sha256": reads.initial_sha256[relative],
            }
    changed = reads.changed_inputs()
    changed_code = before != source_fingerprints()
    if changed or changed_code:
        raise ValueError("Corpus inputs or code changed during physical inspection")
    return {
        "version": 1,
        "status": "recorded",
        "scope": "Physical files and text in six families; recorded acquisition gaps",
        "limits": {
            "raw_bytes": MAX_BYTES,
            "zip_members": MAX_MEMBERS,
            "zip_expanded_bytes": MAX_EXPANDED,
        },
        "source_sha256": before,
        "read_inputs": reads.initial_sha256,
        "changed_inputs": changed,
        "changed_code": changed_code,
        "raw_totals": totals(raw["files"]),
        "families": raw["inventory"],
        "metadata": metadata,
        "counts": dict(Counter(row["state"] for row in records.values())),
        "text_counts": dict(Counter(row["state"] for row in texts.values())),
        "by_family": {
            family: dict(
                Counter(row["state"] for row in records.values() if row["family"] == family)
            )
            for family in TEXT_ROOTS
        },
        "observations": records,
        "texts": texts,
        "exclusions": [
            "Only available files and recorded acquisitions; unsampled attachments excluded.",
            "Senate raw sources come only from the saved 2026-10-16 catalog; other files excluded.",
            "Valid containers do not prove full page content, extraction, legal role or relevance.",
            "ZIP CRC covers outer members within limits; nested archives remain unexpanded.",
            "PDF pages are not rendered; OLE streams and RTF content are not fully decoded.",
            "Readable caches do not prove original correspondence or extraction completeness.",
            "Missing roots and metadata stay explicit; no downloads, OCR, models or corpus writes.",
        ],
    }


def run(corpus: Path, output: Path) -> None:
    if output.is_symlink() or output.exists() or output.resolve().is_relative_to(corpus.resolve()):
        raise ValueError("Output must be new and outside the corpus")
    result = inspect(corpus)
    write_json(output, result)
    print(json.dumps({"counts": result["counts"], "text_counts": result["text_counts"]}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    run(args.corpus, args.output)


if __name__ == "__main__":
    main()
