"""Reserve unseen source groups for human review without reading text or creating labels."""

import argparse
import html
import json
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from tools.corpus_annotations import initiative_groups
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_health import local_path
from tools.corpus_runs import verify
from tools.corpus_runtime import CorpusReads, code_version, source_fingerprints

NON_GOVERNMENT = {"DEPUTIES", "COMMITTEE", "PRESIDENT", "CITIZENS", "SENATE"}
MAX_ORIGINAL_BYTES = 32_000_000


def pinned(path: Path, expected: str) -> dict[str, Any]:
    data = read_artifact(path)
    if digest(data) != expected:
        raise ValueError(f"Pinned input SHA mismatch: {path.name}")
    result = json.loads(data)
    if not isinstance(result, dict):
        raise ValueError("Holdout inputs must be JSON objects")
    return result


def candidates(
    rows: dict[str, Any], consumed: dict[str, str], consumed_hashes: set[str]
) -> tuple[dict[str, Any], dict[str, list[str]]]:
    accepted: dict[str, Any] = {}
    rejected: dict[str, list[str]] = {}
    for identity, row in sorted(rows.items()):
        reasons = []
        metadata = row.get("metadata", [])
        downloads = row.get("downloads", [])
        if not identity.startswith("rpw:RPW/") or not metadata:
            reasons.append("unsupported_or_missing_source_identity")
        if identity in consumed:
            reasons.append("previously_consumed_initiative")
        if row.get("reserved_holdout") is not True:
            reasons.append("not_reserved_before_body_processing")
        if any(
            "rpw:" + str(item.get("number")) != identity
            or item.get("submissionType") != "BILL"
            or item.get("applicantType") not in NON_GOVERNMENT
            for item in metadata
        ):
            reasons.append("initiative_origin_or_identity_unresolved")
        if any(item.get("print") for item in metadata):
            reasons.append("assigned_print_requires_relation_review")
        if not downloads or any(item.get("state") != "downloaded" for item in downloads):
            reasons.append("original_unavailable")
        if any(item.get("sha256") in consumed_hashes for item in downloads):
            reasons.append("raw_bytes_already_consumed")
        if any(
            not isinstance(item.get("bytes"), int) or not 0 < item["bytes"] <= MAX_ORIGINAL_BYTES
            for item in downloads
        ):
            reasons.append("original_size_unverified_or_excluded")
        if reasons:
            rejected[identity] = sorted(set(reasons))
        else:
            accepted[identity] = row
    return accepted, rejected


def regression_download_hashes(rows: dict[str, Any]) -> set[str]:
    return {
        item["sha256"]
        for row in rows.values()
        if row.get("reserved_holdout") is not True
        for item in row.get("downloads", [])
        if item.get("state") == "downloaded"
        and isinstance(item.get("sha256"), str)
        and len(item["sha256"]) == 64
    }


def grouped_candidates(rows: dict[str, Any]) -> dict[str, str]:
    entities = {
        identity: {"documents": [item["sha256"] for item in row["downloads"]]}
        for identity, row in rows.items()
    }
    groups = initiative_groups({"entities": entities})
    return {identity: groups[identity] for identity in rows}


def viewer(cases: list[dict[str, Any]]) -> str:
    cards = []
    for case in cases:
        links = "".join(
            f'<li><a href="{html.escape(item["local_uri"], quote=True)}">'
            f"{html.escape(item['name'])}</a> · SHA {item['sha256']}</li>"
            for item in case["originals"]
        )
        cards.append(
            f"<article><h2>{html.escape(case['id'])}</h2>"
            f"<p>{html.escape(case['title'])}</p><p>Group: {html.escape(case['group'])}</p>"
            f"<ul>{links}</ul><p>Связи, роли, приложения и даты: unknown. "
            "Откройте оригиналы и укажите страницы и цитаты в JSON.</p></article>"
        )
    return (
        '<!doctype html><html lang="ru"><meta charset="utf-8">'
        "<title>Human holdout preparation</title>"
        "<style>body{font:16px system-ui;max-width:1000px;margin:auto;padding:2rem}"
        "article{border-top:1px solid #999;margin-top:2rem}li{overflow-wrap:anywhere}</style>"
        "<h1>Новая очередь человеческой проверки</h1>"
        "<p>Это pending holdout, не готовый эталон. Текст источников агентом не прочитан. "
        "Сначала подтвердите независимость инициатив и связи с другими редакциями. "
        "Метаданные и совпадение байтов не заменяют это прочтение.</p>" + "".join(cards) + "</html>"
    )


def run(
    corpus: Path,
    baseline_index: Path,
    baseline_pin: str,
    inventory: Path,
    inventory_pin: str,
    source_run: Path,
    source_pin: str,
    output: Path,
    *,
    limit: int = 20,
) -> None:
    root, target = corpus.resolve(strict=True), output.resolve()
    html_path = target.with_suffix(".html")
    if (
        limit < 1
        or target.exists()
        or output.is_symlink()
        or html_path.exists()
        or html_path.is_symlink()
        or target.is_relative_to(root)
        or target.is_relative_to(source_run.resolve())
    ):
        raise ValueError("Holdout output must be new, outside all inputs, with a positive limit")
    old = pinned(baseline_index, baseline_pin)
    raw_inventory = pinned(inventory, inventory_pin)
    if raw_inventory.get("index_sha256") != baseline_pin:
        raise ValueError("Raw inventory must describe the pinned consumed index")
    source = source_run.resolve(strict=True)
    pinned(source / "manifest.json", source_pin)
    manifest = verify(source)
    if manifest["status"] != "recorded" or manifest["suite"] != "sejm-sources":
        raise ValueError("Only a sealed completed source collection can reserve a holdout")
    report = json.loads(read_artifact(source / "result.json"))
    consumed = initiative_groups(old)
    reads, code = CorpusReads(root), source_fingerprints()
    rows = report["observations"]
    sizes = {
        item["bytes"]
        for row in rows.values()
        if row.get("reserved_holdout") is True
        for item in row.get("downloads", [])
        if isinstance(item.get("bytes"), int) and 0 < item["bytes"] <= MAX_ORIGINAL_BYTES
    }
    aliases = regression_download_hashes(rows)
    archive_checks: dict[str, Any] = {}
    with reads.guard():
        for row in raw_inventory["files"]:
            path = local_path(root, row["path"])
            reads.require(path)
            if reads.initial_sha256[row["path"]] != row["sha256"]:
                raise ValueError(f"Consumed raw source changed: {row['path']}")
            aliases.add(row["sha256"])
            if path.suffix.lower() not in (".zip", ".docx", ".xlsx", ".odt"):
                continue
            try:
                with zipfile.ZipFile(path) as archive:
                    matches = [
                        item
                        for item in archive.infolist()
                        if not item.is_dir() and item.file_size in sizes
                    ]
                    for member in matches:
                        with archive.open(member) as stream:
                            data = stream.read(MAX_ORIGINAL_BYTES + 1)
                        if len(data) != member.file_size:
                            raise ValueError("Archive member size mismatch")
                        aliases.add(digest(data))
                    archive_checks[row["path"]] = {
                        "state": "checked_size_matching_direct_members",
                        "members": len(matches),
                    }
            except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
                archive_checks[row["path"]] = {
                    "state": "unavailable",
                    "error": f"{type(exc).__name__}: {exc}",
                }
    accepted, rejected = candidates(rows, consumed, aliases)
    groups = grouped_candidates(accepted)
    selected_groups = sorted({groups[identity] for identity in accepted})[:limit]
    cases = []
    for identity, row in accepted.items():
        if groups[identity] not in selected_groups:
            continue
        originals = []
        for item in row["downloads"]:
            path = local_path(source, item["path"])
            data = read_artifact(path)
            if digest(data) != item["sha256"] or len(data) != item["bytes"]:
                raise ValueError("Reserved source bytes changed")
            originals.append(
                {
                    **{key: item[key] for key in ("path", "name", "url", "sha256", "bytes")},
                    "local_uri": path.as_uri(),
                }
            )
        cases.append(
            {
                "id": identity,
                "group": groups[identity],
                "title": row["metadata"][0].get("title", identity),
                "metadata": row["metadata"],
                "originals": originals,
                "status": "pending_human_review",
                "split": "holdout_pending",
                "oracle": None,
                "basis": "source_metadata_and_byte_alias_exclusion_only",
                "reviewer": None,
                "reviewer_kind": None,
                "read_scope": "not_read",
                "read_ranges": [],
                "evidence": [],
                "questions": {
                    key: "unknown"
                    for key in (
                        "independent_initiative",
                        "belongs_to",
                        "role",
                        "required_annexes",
                        "draft_date",
                        "scan_norm_pages",
                    )
                },
                "used_for_tuning": False,
            }
        )
    if (
        not cases
        or reads.changed_inputs()
        or code != source_fingerprints()
        or digest(read_artifact(baseline_index)) != baseline_pin
        or digest(read_artifact(inventory)) != inventory_pin
        or digest(read_artifact(source / "manifest.json")) != source_pin
    ):
        raise ValueError("No eligible cohort or holdout inputs/code changed")
    verify(source)
    with html_path.open("x") as stream:
        stream.write(viewer(cases))
    write_json(
        target,
        {
            "version": 1,
            "status": "recorded",
            "scope": "New pending human cohort; no human labels or independent accuracy claim",
            "code_sha": code_version(),
            "source_sha256": code,
            "consumption_policy": (
                "All initiatives and originals of the pinned historical index are consumed"
            ),
            "pinned_inputs": {
                str(baseline_index.resolve()): baseline_pin,
                str(inventory.resolve()): inventory_pin,
                str(source / "manifest.json"): source_pin,
            },
            "read_inputs": reads.initial_sha256,
            "archive_alias_checks": archive_checks,
            "consumed_entities": len(consumed),
            "consumed_raw_identities": len(aliases),
            "group_count": len(selected_groups),
            "cases": cases,
            "rejected": rejected,
            "rejection_counts": dict(
                Counter(reason for row in rejected.values() for reason in row)
            ),
            "exclusions": [
                "All historical indexed initiatives are excluded, including incomplete old traces.",
                "Aliases of nonreserved downloads in the new source run are also consumed.",
                "Government or already printed submissions need relation review and are excluded.",
                "Raw and direct ZIP-member aliases are excluded; nested ZIPs are not traversed.",
                "Preparation never extracts, displays, scores or assigns roles to source text.",
                "A human must confirm independence and read scope before promotion to holdout.",
            ],
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--baseline-index", type=Path, required=True)
    parser.add_argument("--baseline-sha256", required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--inventory-sha256", required=True)
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    run(
        args.corpus,
        args.baseline_index,
        args.baseline_sha256,
        args.inventory,
        args.inventory_sha256,
        args.source_run,
        args.source_sha256,
        args.output,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
