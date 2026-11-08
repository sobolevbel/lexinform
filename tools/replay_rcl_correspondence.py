"""Locate pinned correspondence quotes in RCL bill bodies."""

import argparse
import json
from pathlib import Path
from typing import Any

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.models import RclProject
from tools.audit_rcl_amendment_archives import AS_OF
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_rcl import document_id, stage_cases
from tools.corpus_runtime import CorpusReads, source_fingerprints
from tools.replay_proposal_edition import PinnedInput


def _candidate_editions(
    root: Path,
    project_path: Path,
    project: RclProject,
    reader: DocumentTextExtractor,
    quotes: dict[str, str],
    first_stage: int,
    archive_id: str,
) -> dict[str, Any]:
    stages: dict[str, set[int]] = {}
    for case in stage_cases(project_path, project, as_of=AS_OF):
        stage = int(case.identity.split("/")[1])
        if stage < first_stage or case.document is None:
            continue
        for candidate in (case.document, *case.document.alternatives):
            identity = document_id(candidate.url).decode()
            if identity != archive_id:
                stages.setdefault(identity, set()).add(stage)
    rows: dict[str, Any] = {}
    for identity, appearances in sorted(stages.items()):
        files = sorted((root / "documents/files").glob(f"{identity}.*"))
        row: dict[str, Any] = {"stages": sorted(appearances)}
        if len(files) != 1:
            rows[identity] = {**row, "status": "missing_or_ambiguous"}
            continue
        raw = files[0].read_bytes()
        selection = reader.extract_document(raw).selection
        bodies = [
            (part, " ".join(part.text.split())) for part in selection.parts if part.role == "bill"
        ]
        rows[identity] = {
            **row,
            "sha256": digest(raw),
            "selection": selection.state,
            "body_count": len(bodies),
            "matches": [
                {"quote_sha256": sha, "member": part.members, "member_sha256": part.sha256}
                for part, body in bodies
                for sha, quote in quotes.items()
                if quote in body
            ],
        }
    return rows


def replay(root: Path, archive_report: bytes) -> dict[str, Any]:
    report = json.loads(archive_report)
    if report["as_of"] != AS_OF.isoformat():
        raise ValueError("Archive report uses a different stage snapshot date")
    reads, sources = CorpusReads(root), source_fingerprints()
    reader = DocumentTextExtractor(PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor())
    results = {}
    with reads.guard():
        for identity, row in report["rows"].items():
            letters = [
                letter
                for letter in row.get("correspondence", [])
                if any(witness["status"] == "quoted" for witness in letter["quoted_replacements"])
            ]
            if not letters:
                continue
            raw = PinnedInput(path=f"documents/files/{identity}.zip", sha256=row["sha256"]).read(
                root
            )
            parts = reader.extract_parts(raw).parts
            quotes = {}
            for letter in letters:
                matches = [
                    part
                    for part in parts
                    if part.members == tuple(letter["path"])
                    and part.sha256 == letter["sha256"]
                    and part.text_sha256 == letter["text_sha256"]
                ]
                if len(matches) != 1:
                    raise ValueError(f"Correspondence member changed: {identity}")
                for witness in letter["quoted_replacements"]:
                    if witness["status"] != "quoted":
                        continue
                    quote = matches[0].text[witness["start"] : witness["end"]]
                    if not quote.strip() or digest(quote.encode()) != witness["sha256"]:
                        raise ValueError(f"Correspondence quote changed: {identity}")
                    quotes[witness["sha256"]] = " ".join(quote.split())
            first_stages: dict[str, int] = {}
            for related in row["related_stages"]:
                owner, stage = related.split("/")
                first_stages[owner] = min(first_stages.get(owner, int(stage)), int(stage))
            results[identity] = {}
            for owner, first_stage in sorted(first_stages.items()):
                project_path = f"corpus/{owner}.json"
                project = RclProject.model_validate_json(
                    PinnedInput(
                        path=project_path, sha256=report["input_sha256"][project_path]
                    ).read(root)
                )
                results[identity][owner] = _candidate_editions(
                    root, root / project_path, project, reader, quotes, first_stage, identity
                )
    if reads.changed_inputs() or sources != source_fingerprints():
        raise ValueError("Inputs or extractor sources changed during correspondence replay")
    return {
        "archive_report_sha256": digest(archive_report),
        "source_sha256": sources,
        "input_sha256": reads.initial_sha256,
        "observations": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("archive_report", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    root = args.corpus.resolve(strict=True)
    output = args.output.resolve()
    if output.is_relative_to(root) or args.output.is_symlink():
        parser.error("Output must be a new file outside the corpus")
    write_json(output, replay(root, read_artifact(args.archive_report)))


if __name__ == "__main__":
    main()
