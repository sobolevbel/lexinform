"""Triage pinned empty selections by physical leaves while preserving unknown legal roles."""

import argparse
import html
import io
import json
import logging
import subprocess
import sys
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.sections import document_kind
from tools.corpus_annotations import initiative_groups
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_extraction import MAX_BYTES, bounded_reader
from tools.corpus_health import local_path
from tools.corpus_runtime import CorpusReads, code_version, source_fingerprints

MAX_MEMBERS = 2048
MAX_UNPACKED = 128_000_000
MAX_DEPTH = 4


def physical_leaves(data: bytes) -> list[dict[str, Any]]:
    reader = bounded_reader()
    leaves: list[dict[str, Any]] = []
    usage = [0, 0]

    def visit(payload: bytes, members: tuple[str, ...]) -> None:
        base: dict[str, Any] = {
            "members": list(members),
            "sha256": digest(payload),
            "bytes": len(payload),
            "role": "unknown",
            "belongs_to": "unknown",
            "required_for_bill": "unknown",
        }
        if payload.startswith(b"PK"):
            try:
                with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                    names = set(archive.namelist())
                    if not ({"word/document.xml", "content.xml"} & names):
                        if len(members) >= MAX_DEPTH:
                            leaves.append({**base, "state": "limit", "reason": "archive_depth"})
                            return
                        for info in archive.infolist():
                            if info.is_dir():
                                continue
                            usage[0] += 1
                            usage[1] += info.file_size
                            member = {
                                "members": [*members, info.filename],
                                "declared_bytes": info.file_size,
                                "role": "unknown",
                                "belongs_to": "unknown",
                                "required_for_bill": "unknown",
                            }
                            if (
                                usage[0] > MAX_MEMBERS
                                or usage[1] > MAX_UNPACKED
                                or info.file_size > MAX_BYTES
                            ):
                                leaves.append({**member, "state": "limit", "reason": "unpacking"})
                                continue
                            if info.flag_bits & 1:
                                leaves.append(
                                    {**member, "state": "unavailable", "reason": "encrypted"}
                                )
                                continue
                            try:
                                with archive.open(info) as stream:
                                    child = stream.read(MAX_BYTES + 1)
                                if len(child) > MAX_BYTES:
                                    raise ValueError("Member exceeds byte limit")
                                visit(child, (*members, info.filename))
                            except Exception as exc:
                                leaves.append(
                                    {**member, "state": "error", "error": type(exc).__name__}
                                )
                        return
            except (zipfile.BadZipFile, OSError, ValueError) as exc:
                leaves.append({**base, "state": "error", "error": type(exc).__name__})
                return
        if b"%PDF-" in payload[:1024]:
            result = PypdfTextExtractor().extract_pages(payload)
            pages = [
                {
                    "number": page.number,
                    "chars": len(page.text.strip()),
                    "text_sha256": digest(page.text.encode()),
                    "error": page.error_type,
                }
                for page in result.pages
            ]
            text = result.legacy_text
            base.update(
                format="pdf",
                pages=pages,
                state="error"
                if result.error_type or any(page["error"] for page in pages)
                else "inspected",
                error=result.error_type,
                pages_without_text=[page["number"] for page in pages if not page["chars"]],
            )
        else:
            known = payload.startswith((b"PK", bytes.fromhex("d0cf11e0a1b11ae1"))) or (
                b"wordDocument" in payload[:1024]
            )
            if not known:
                leaves.append({**base, "state": "unsupported", "format": "unknown"})
                return
            try:
                text = reader.extract(payload)
                base.update(format="word_or_odf", state="inspected", pages=None)
            except Exception as exc:
                leaves.append({**base, "state": "error", "error": type(exc).__name__})
                return
        leaves.append(
            {
                **base,
                "chars": len(text),
                "text_sha256": digest(text.encode()),
                "machine_kind": document_kind(text),
                "excerpt": {"start": 0, "end": min(600, len(text)), "text": text[:600]},
            }
        )

    visit(data, ())
    return leaves


def repair_actions(leaves: list[dict[str, Any]]) -> list[str]:
    actions = set()
    if any(row.get("pages_without_text") for row in leaves):
        actions.add("review_original_pages_before_ocr")
    if any(row.get("chars", 0) for row in leaves):
        actions.add("review_readable_unselected_parts")
    if any(row["state"] == "unsupported" for row in leaves):
        actions.add("review_unsupported_members")
    if any(row["state"] in ("error", "limit", "unavailable") for row in leaves):
        actions.add("review_physical_error_or_limit")
    if not actions:
        actions.add("review_empty_original")
    return sorted(actions)


def preview(data: bytes, members: list[str], output: Path, *, expected_sha256: str) -> None:
    for name in members:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo(name)
            if info.file_size > MAX_BYTES:
                raise ValueError("Preview member exceeds byte limit")
            with archive.open(info) as stream:
                data = stream.read(MAX_BYTES + 1)
    if digest(data) != expected_sha256:
        raise ValueError("Preview source SHA mismatch; archive member names may be ambiguous")
    if len(data) > MAX_BYTES or b"%PDF-" not in data[:1024]:
        raise ValueError("Preview needs a bounded PDF source")
    command = ["pdftoppm", "-f", "1", "-singlefile", "-scale-to", "900", "-png", "-", str(output)]
    subprocess.run(command, input=data, capture_output=True, check=True, timeout=45)


def run(corpus: Path, generation: Path, pin: str, output: Path, *, previews: bool = False) -> None:
    root, target = corpus.resolve(strict=True), output.resolve()
    if target.exists() or output.is_symlink() or target.is_relative_to(root):
        raise ValueError("Output must be new and outside the corpus")
    if target.with_suffix(".html").exists() or target.with_suffix(".html").is_symlink():
        raise ValueError("Review viewer must be new")
    raw = read_artifact(generation)
    if digest(raw) != pin:
        raise ValueError("Generation SHA mismatch")
    baseline = json.loads(raw)
    if baseline.get("status") != "recorded":
        raise ValueError("A completed generation is required")
    cohort = {
        key: value
        for key, value in baseline["observations"].items()
        if value["state"] == "still_empty"
    }
    if not cohort:
        raise ValueError("Still-empty population must not be empty")
    reads, code = CorpusReads(root), source_fingerprints()
    with reads.guard():
        index = json.loads(local_path(root, "INDEX.json").read_bytes())
        groups = initiative_groups(index)
        metadata = json.loads(local_path(root, "documents/index.json").read_bytes())
    assets = target.with_suffix("")
    if previews:
        assets.mkdir()
    records: dict[str, Any] = {}
    rendered: dict[str, str] = {}
    for number, (identity, old) in enumerate(sorted(cohort.items()), 1):
        with reads.guard():
            path = local_path(root, old["raw_path"])
            if path.stat().st_size > MAX_BYTES:
                raise ValueError("Raw review source exceeds byte limit")
            data = path.read_bytes()
            if digest(data) != old["raw_sha256"]:
                raise ValueError(f"Pinned raw source changed: {identity}")
            leaves = physical_leaves(data)
        for leaf in leaves:
            if previews and leaf.get("format") == "pdf" and leaf.get("pages"):
                sha = leaf["sha256"]
                if sha not in rendered:
                    path = assets / sha
                    try:
                        preview(data, leaf["members"], path, expected_sha256=sha)
                        rendered[sha] = str(path.with_suffix(".png").relative_to(target.parent))
                    except (OSError, ValueError, subprocess.SubprocessError) as exc:
                        leaf["preview_error"] = type(exc).__name__
                leaf["preview_page"] = 1
                leaf["preview"] = rendered.get(sha)
        records[identity] = {
            "raw_path": old["raw_path"],
            "raw_sha256": old["raw_sha256"],
            "source_url": old.get("source_url"),
            "name": metadata[identity].get("name"),
            "group": groups.get(f"doc:{identity}", f"doc:{identity}"),
            "status": "pending_review",
            "basis": "machine_physical_observation",
            "legal_role": "unknown",
            "required_annexes": "unknown",
            "relation_conflicts": "unknown",
            "actions": repair_actions(leaves),
            "leaves": leaves,
        }
        if number % 10 == 0 or number == len(cohort):
            print(f"empty original review {number}/{len(cohort)}", file=sys.stderr)
    changed = reads.changed_inputs()
    if changed or code != source_fingerprints():
        raise ValueError("Review inputs or code changed during inspection")
    result = {
        "version": 1,
        "status": "recorded",
        "scope": "Physical triage of every pinned still-empty original; pending legal review",
        "code_sha": code_version(),
        "source_sha256": code,
        "generation_sha256": pin,
        "records": records,
        "action_counts": dict(
            Counter(action for row in records.values() for action in row["actions"])
        ),
        "leaf_states": dict(
            Counter(leaf["state"] for row in records.values() for leaf in row["leaves"])
        ),
        "read_inputs": reads.initial_sha256,
        "changed_inputs": changed,
        "preview_count": len(rendered),
        "exclusions": [
            "Machine kinds, names and textless pages do not establish legal role or OCR need.",
            "Only preview page 1 is rendered; text hashes cover the other PDF pages.",
            "Unsupported members, errors and limits remain explicit; no OCR or model calls.",
            "Required annexes and disputed relations remain unknown until source evidence is read.",
            "This consumed diagnostic cohort cannot become independent human holdout.",
        ],
    }
    write_json(target, result)
    cards = []
    for identity, row in records.items():
        parts = []
        for leaf in row["leaves"]:
            image = (
                f'<img loading="lazy" src="{html.escape(leaf["preview"], quote=True)}" '
                'alt="Unreviewed first PDF page" style="max-width:100%">'
                if leaf.get("preview")
                else ""
            )
            parts.append(
                "<details><summary>"
                + html.escape(" / ".join(leaf["members"]) or "PDF original")
                + "</summary><pre>"
                + html.escape(json.dumps(leaf, ensure_ascii=False, indent=2))
                + "</pre>"
                + image
                + "</details>"
            )
        cards.append(
            f"<article><h2>{html.escape(identity)}</h2><p>"
            + html.escape(", ".join(row["actions"]))
            + "</p>"
            + "".join(parts)
            + "</article>"
        )
    with target.with_suffix(".html").open("x") as stream:
        stream.write(
            '<!doctype html><meta charset="utf-8"><title>Empty original triage</title>'
            "<style>body{font:16px system-ui;max-width:1000px;margin:auto}"
            "pre{white-space:pre-wrap}</style>"
            "<h1>Пустые результаты выбора</h1><p>Физическая опись; "
            "правовые роли и связи ещё не проверены.</p>" + "".join(cards)
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--generation", type=Path, required=True)
    parser.add_argument("--generation-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--previews", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    run(args.corpus, args.generation, args.generation_sha256, args.output, previews=args.previews)


if __name__ == "__main__":
    main()
