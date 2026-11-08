"""Inventory physical members of RCL ZIPs declared as self-amendments."""

import argparse
import datetime as dt
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.document_candidates import select_document_parts
from lexinform.rcl_amendments import rcl_self_amendment_part, rcl_self_amendment_print_number
from lexinform.sections import document_kind
from tools.corpus_artifacts import digest, write_json
from tools.corpus_rcl import document_id, projects, stage_cases
from tools.corpus_runtime import CorpusReads, source_fingerprints

AS_OF = dt.datetime(2026, 10, 17, tzinfo=dt.UTC)
HEADING = re.compile(r"(?im)^\s*autopoprawka\s+(?:[IVX]+\s+)?do\b")
TRANSMITTAL = re.compile(r"(?i)przedkładam\s+(?:w\s+załączeniu\s+)?autopoprawkę")
ARTICLE_FORMULA = re.compile(
    r"(?i)\bart\s*\.\s*\d+[a-z]?[\s\w.(),–-]{0,95}"
    r"(?:otrzymuje\s+brzmienie|dodaje\s+się|uchyla\s+się)\b"
)


def archive_observation(
    reader: DocumentTextExtractor, raw: bytes, targets: dict[str, tuple[str, str | None]]
) -> dict[str, Any]:
    observed: dict[str, Any] = {"sha256": digest(raw)}
    try:
        extracted = reader.extract_parts(raw)
        selection = select_document_parts(extracted, max_part_chars=None).selection
    except (OSError, ValueError) as exc:
        return {**observed, "status": "read_error", "error_type": type(exc).__name__}
    if targets:
        observed["project_targets"] = {
            owner: {
                "path": member.members,
                "sha256": member.sha256,
                "text_sha256": member.text_sha256,
                "original_print_number": rcl_self_amendment_print_number(member.text),
            }
            if (
                member := rcl_self_amendment_part(
                    extracted.parts, project_title=title, wykaz_number=number
                )
            )
            is not None
            else None
            for owner, (title, number) in targets.items()
        }
    return {
        **observed,
        "status": "read",
        "selection": selection.state,
        "unreadable": len(extracted.unreadable_members),
        "rejected": len(extracted.rejected),
        "members": [
            {
                "path": part.members,
                "sha256": part.sha256,
                "text_sha256": part.text_sha256,
                "kind": document_kind(part.text),
                "chars": len(part.text),
                "amendment_heading": bool(HEADING.search(part.text[:1200])),
                "transmittal": bool(TRANSMITTAL.search(part.text[:1600])),
                "article_formula": bool(ARTICLE_FORMULA.search(part.text)),
                "member_named_amendment": bool(
                    part.members and "autopoprawka" in part.members[-1].casefold()
                ),
            }
            for part in extracted.parts
        ],
    }


def inspect(root: Path, *, project_targets: bool = False) -> dict[str, Any]:
    reads = CorpusReads(root)
    sources = source_fingerprints()
    reader = DocumentTextExtractor(PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor())
    catalog: dict[str, set[tuple[str, str]]] = defaultdict(set)
    relations: dict[str, set[str]] = defaultdict(set)
    rows: dict[str, Any] = {}
    counts: Counter[str] = Counter()
    owners: dict[str, tuple[str, str | None]] = {}
    with reads.guard():
        for path, project in projects(root):
            counts["projects"] += 1
            if project_targets:
                owners[str(project.id)] = project.title, project.wykaz_number
            for stage in project.reached_stages:
                for doc in project.stage_documents(stage):
                    if doc.extension == "zip" and "autopoprawka" in doc.name.casefold():
                        catalog[str(doc.id)].add((str(project.id), doc.name))
            for case in stage_cases(path, project, as_of=AS_OF):
                counts["stages"] += 1
                if case.document is None:
                    continue
                for candidate in (case.document, *case.document.alternatives):
                    for url in candidate.amendment_urls:
                        if url.casefold().endswith(".zip"):
                            relations[document_id(url).decode()].add(case.identity)
        for identity in sorted(catalog.keys() | relations.keys(), key=int):
            archive = root / "documents" / "files" / f"{identity}.zip"
            reads.require(archive)
            target_ids = {stage.split("/", 1)[0] for stage in relations[identity]}
            rows[identity] = {
                "catalog": sorted(catalog[identity]),
                "related_stages": sorted(relations[identity]),
                **(
                    archive_observation(
                        reader,
                        archive.read_bytes(),
                        {owner: owners[owner] for owner in sorted(target_ids)} if owners else {},
                    )
                    if archive.is_file()
                    else {"status": "missing"}
                ),
            }
    if reads.changed_inputs() or sources != source_fingerprints():
        raise ValueError("Inputs or extractor sources changed during archive inventory")
    return {
        "as_of": AS_OF.isoformat(),
        "counts": dict(counts),
        "source_sha256": sources,
        "input_sha256": reads.initial_sha256,
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--project-targets", action="store_true")
    args = parser.parse_args()
    root = args.corpus.resolve(strict=True)
    output = args.output.resolve()
    if output.is_relative_to(root) or args.output.is_symlink():
        parser.error("Output must be a new file outside the corpus")
    write_json(output, inspect(root, project_targets=args.project_targets))


if __name__ == "__main__":
    main()
