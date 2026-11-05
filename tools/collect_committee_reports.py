"""Freeze official committee-report URLs before bounded append-only public collection."""

import argparse
import datetime as dt
import gzip
import json
import time
from pathlib import Path
from typing import Any

import httpx2 as httpx

from lexinform.adapters.sejm_api import parse_process_detail
from lexinform.models import amendments_stage
from lexinform.models.sejm import flatten_stages
from tools.corpus_artifacts import create_only, digest, read_artifact, write_json
from tools.corpus_http import attachment_extension, fetch
from tools.corpus_runs import finish, new_run, write_started
from tools.corpus_runtime import CorpusReads, source_fingerprints

COUNT = 12
FILE_BYTES = 8 * 1024 * 1024
TOTAL_BYTES = 64 * 1024 * 1024
CHUNK_BYTES = 8192
TOTAL_SECONDS = 180
DETAILS = "sejm/term10/process_details.json.gz"


def outside(corpus: Path, output: Path) -> None:
    if output.resolve().is_relative_to(corpus.resolve()):
        raise ValueError("Collection artifacts must be outside the corpus")
    if output.absolute() != output.resolve():
        raise ValueError("Collection output must not contain symlinks")


def build_plan(corpus: Path, index_sha256: str) -> dict[str, Any]:
    reads = CorpusReads(corpus)
    reports: dict[str, dict[str, Any]] = {}
    with reads.guard():
        if digest(read_artifact(corpus / "INDEX.json")) != index_sha256:
            raise ValueError("Corpus INDEX SHA mismatch")
        details = json.loads(gzip.decompress(read_artifact(corpus / DETAILS)))
        for owner, record in sorted(details.items()):
            detail = parse_process_detail(record)
            if detail.term != 10 or detail.number != owner:
                raise ValueError("Saved process identity differs from its catalog key")
            for ordinal, stage in enumerate(flatten_stages(detail.stages)):
                if stage.stage_type != "CommitteeReport" or amendments_stage([stage]) is None:
                    continue
                url = stage.report_file
                if url is None:
                    continue
                key = digest(url.encode())
                row = reports.setdefault(key, {"id": key, "url": url, "stage_observations": []})
                row["stage_observations"].append(
                    {
                        "owner_print": owner,
                        "term": 10,
                        "flattened_stage_ordinal": ordinal,
                        "stage": stage.model_dump(mode="json"),
                    }
                )
    if reads.changed_inputs() or len(reports) < COUNT:
        raise ValueError("Stable official metadata with twelve unique reports is required")
    return {
        "version": 1,
        "kind": "committee-report-cohort",
        "index_sha256": index_sha256,
        "read_inputs": reads.initial_sha256,
        "selection": "first12_unique_URL_SHA256_ascending",
        "eligible_unique_urls": len(reports),
        "reports": [reports[key] for key in sorted(reports)[:COUNT]],
        "limits": {
            "complete_body_bytes": FILE_BYTES,
            "observed_total_body_bytes": TOTAL_BYTES,
            "chunk_bytes": CHUNK_BYTES,
            "start_sources_within_seconds": TOTAL_SECONDS,
            "per_fetch_seconds": 60,
            "socket_timeout_seconds": 10,
            "automatic_retries": 0,
        },
    }


def freeze_plan(corpus: Path, output: Path, *, index_sha256: str) -> dict[str, Any]:
    outside(corpus, output)
    plan = build_plan(corpus, index_sha256)
    write_json(output, plan)
    return plan


def collect(
    corpus: Path,
    plan_path: Path,
    plan_sha256: str,
    output: Path,
    identity: str,
    client: httpx.Client,
) -> dict[str, Any]:
    outside(corpus, output)
    outside(corpus, plan_path)
    raw = read_artifact(plan_path)
    if digest(raw) != plan_sha256:
        raise ValueError("Frozen plan SHA mismatch")
    plan = json.loads(raw)
    expected = build_plan(corpus, plan["index_sha256"])
    if plan != expected:
        raise ValueError("Frozen plan differs from official metadata or limits")
    directory = new_run(output, identity, "sejm-sources")
    downloads = directory / "downloads"
    downloads.mkdir()
    write_json(directory / "plan.json", plan)
    sources = source_fingerprints()
    started = {
        "kind": "committee-report-collection",
        "source_sha256": sources,
        "plan_sha256": plan_sha256,
        "read_inputs": plan["read_inputs"],
        "started_at": dt.datetime.now(dt.UTC).isoformat(),
        "network": "sequential public GET, no retry, no replacement of planned failures",
    }
    write_started(directory, started)
    clock = time.monotonic()
    rows: list[dict[str, Any]] = []
    spent = retained = 0
    old_timeout = client.timeout
    try:
        client.timeout = httpx.Timeout(10)
        for report in plan["reports"]:
            row: dict[str, Any] = {"id": report["id"], "state": "not_attempted"}
            rows.append(row)
            available = TOTAL_BYTES - spent - CHUNK_BYTES
            if time.monotonic() - clock >= TOTAL_SECONDS:
                row["reason"] = "collection_time_limit"
                continue
            if available <= 0:
                row["reason"] = "collection_byte_limit"
                continue
            receipt: dict[str, Any] = {}
            row["http_receipt"] = receipt
            try:
                response = fetch(
                    client,
                    report["url"],
                    strict=True,
                    max_bytes=min(FILE_BYTES, available),
                    chunk_size=CHUNK_BYTES,
                    receipt=receipt,
                )
                if attachment_extension(response.data, "pdf") != "pdf":
                    raise ValueError("Committee-report cohort requires a PDF attachment")
                path = f"downloads/{report['id']}.pdf"
                create_only(directory / path, response.data)
                retained += len(response.data)
                row.update(
                    state="downloaded",
                    path=path,
                    bytes=len(response.data),
                    sha256=digest(response.data),
                    response=response.provenance,
                )
            except (OSError, ValueError, TimeoutError, httpx.HTTPError) as exc:
                row.update(state="error", exception_type=type(exc).__name__)
            finally:
                spent += receipt.get("bytes_read", 0)
    finally:
        client.timeout = old_timeout
    stable_inputs = all(
        digest(read_artifact(corpus / path)) == pin for path, pin in plan["read_inputs"].items()
    )
    stable_code = sources == source_fingerprints()
    result = {
        "status": "recorded"
        if stable_inputs and stable_code and spent <= TOTAL_BYTES
        else "failed",
        "kind": "committee-report-collection",
        "observations": {row["id"]: row for row in rows},
        "source_sha256": sources,
        "read_inputs_unchanged": stable_inputs,
        "code_unchanged": stable_code,
        "downloaded_bytes": retained,
        "observed_body_bytes": spent,
        "scope": "Freshly fetched public bytes, not the historical snapshot bytes or legal labels",
        "exclusions": "Planned failures remain excluded; corpus originals and INDEX untouched",
    }
    write_json(directory / "result.json", result)
    finish(
        directory,
        "sejm-sources",
        returncode=0 if result["status"] == "recorded" else 1,
        started=started,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--index-sha256", required=True)
    plan.add_argument("--output", type=Path, required=True)
    run = sub.add_parser("collect")
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--plan-sha256", required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--id", required=True)
    args = parser.parse_args()
    if args.command == "plan":
        freeze_plan(args.corpus, args.output, index_sha256=args.index_sha256)
    else:
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            result = collect(args.corpus, args.plan, args.plan_sha256, args.output, args.id, client)
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "downloaded_bytes": result["downloaded_bytes"],
                    "states": [row["state"] for row in result["observations"].values()],
                }
            )
        )


if __name__ == "__main__":
    main()
