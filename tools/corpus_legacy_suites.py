"""Expose process and Senate replays through the shared guarded run contract."""

import argparse
from pathlib import Path
from typing import Any

from tools.corpus_artifacts import write_json
from tools.corpus_runtime import CorpusReads, code_version, source_fingerprints
from tools.replay_process_evidence import report as process_report
from tools.replay_senate_documents import replay as senate_report


def run(corpus: Path, output: Path, *, kind: str, supplement: Path | None = None) -> int:
    root, target = corpus.resolve(), output.resolve()
    if output.is_symlink() or target.exists() or target.is_relative_to(root):
        raise ValueError("Output must be new and outside the corpus")
    reads, before = CorpusReads(root), source_fingerprints()
    observations: dict[str, Any] = {}
    summary: dict[str, Any] = {}
    status, error = "recorded", ""
    try:
        if kind not in ("process", "senate"):
            raise ValueError("Unknown legacy suite")
        if supplement is not None and not supplement.resolve().is_relative_to(root):
            raise ValueError("Senate supplement must be inside the declared corpus")
        with reads.guard():
            payload = (
                process_report(root)
                if kind == "process"
                else senate_report(
                    supplement or root / "sejm/supplements/2026-10-16-senate-resolutions", root
                )
            )
        for row in payload["rows"]:
            identity = row["key"] if kind == "senate" else f"{row['term']}/{row['number']}"
            if identity in observations:
                raise ValueError("Legacy suite returned duplicate observations")
            observations[identity] = row
        summary = {key: value for key, value in payload.items() if key != "rows"}
        if not observations:
            status, error = "not_run", "Requested suite has no observations"
    except FileNotFoundError as exc:
        status, error = "not_run", str(exc)
    except Exception as exc:
        status, error = "failed", f"{type(exc).__name__}: {exc}"
    changed = reads.changed_inputs()
    if changed or before != source_fingerprints():
        status, error = "failed", "Inputs or code changed during replay"
    write_json(
        target,
        {
            "status": status,
            "scope": f"Guarded legacy {kind} replay; original semantic observations",
            "code_sha": code_version(),
            "source_sha256": before,
            "observations": observations,
            "summary": summary,
            "error": error,
            "read_inputs": reads.initial_sha256,
            "changed_inputs": changed,
            "missing_inputs": sorted(reads.missing),
            "exclusions": [
                "No fresh acts, raw downloads, OCR, model calls or production state.",
                "Senate scans are unverified; process metadata remains a historical snapshot.",
            ],
        },
    )
    return 0 if status == "recorded" else 2 if status == "not_run" else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--kind", choices=("process", "senate"), required=True)
    parser.add_argument("--supplement", type=Path)
    args = parser.parse_args()
    raise SystemExit(run(args.corpus, args.output, kind=args.kind, supplement=args.supplement))


if __name__ == "__main__":
    main()
