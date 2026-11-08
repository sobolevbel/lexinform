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
from lexinform.document_composition import BundleAssembly, RequiredPartFailure, amendment_part
from lexinform.models.document_reads import LoadedFile
from lexinform.models.documents import ExtractedDocument
from lexinform.rcl_amendments import (
    is_rcl_amendment_transmittal,
    rcl_self_amendment_part,
    rcl_self_amendment_print_number,
)
from lexinform.sections import document_kind
from lexinform.services.documents import MIN_TEXT_CHARS
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
_SALUTATION = re.compile(r"\bszanown(?:y|a|i|e)\s+(?:panie|pani|państwo)\b", re.I)
_REPLY = re.compile(r"\b(?:w\s+odpowiedzi\s+na\s+uwagi|odnosząc\s+się\s+do\s+uwag)\b", re.I)
_ARTICLE_LABEL = re.compile(r"\bart\s*\.\s*\d+[a-z]?", re.I)
_LETTER_QUOTE = re.compile(
    r"(?:\bproponuję\s+nadać\s+(?P<proposed>art\s*\.\s*\d+[a-z]?)"
    r"[^:„”]{0,120}\bbrzmienie"
    r"|\b(?P<replacement>art\s*\.\s*\d+[a-z]?)[^:„”]{0,120}"
    r"\botrzymał[oa]?\s+brzmienie"
    r"|\b(?P<received>art\s*\.\s*\d+[a-z]?)[^:„”]{0,120}"
    r"\botrzymuj(?:e|ą)\s+brzmienie)\s*:\s*„",
    re.I,
)


def _closing_quote(text: str, start: int, stop: int) -> int | None:
    depth = 1
    for position in range(start, min(start + 16_000, stop)):
        if text[position] == "„":
            depth += 1
        elif text[position] == "”":
            depth -= 1
            if depth == 0:
                return position
    return None


def correspondence_quotes(text: str) -> list[dict[str, Any]]:
    """Record explicit quoted article replacements as offsets into one extracted letter."""
    opening = text[:1800]
    if _SALUTATION.search(opening) is None or _REPLY.search(opening) is None:
        return []
    rows: list[dict[str, Any]] = []
    matches = tuple(_LETTER_QUOTE.finditer(text))
    for index, match in enumerate(matches):
        start = match.end()
        stop = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        end = _closing_quote(text, start, stop)
        row: dict[str, Any] = {
            "reference": next(value for value in match.groups() if value is not None),
            "start": start,
        }
        if len(_ARTICLE_LABEL.findall(match[0])) != 1:
            row["status"] = "unresolved_reference"
        elif end is None or end == start:
            row["status"] = "unresolved_boundary"
        else:
            row.update(status="quoted", end=end, sha256=digest(text[start:end].encode()))
        rows.append(row)
    return rows


def _archive_admission(document: ExtractedDocument, sha256: str) -> dict[str, Any]:
    file = LoadedFile(
        document.text if len(document.text.strip()) >= MIN_TEXT_CHARS else None,
        sha256=sha256,
        selection=document.selection,
    )
    result = amendment_part(BundleAssembly(file, (), (), ()), "archive.zip", file, rcl=True)
    return {
        "selected_before_composition": [part.sha256 for part in document.selection.parts],
        "retained": []
        if isinstance(result, RequiredPartFailure)
        else [part.sha256 for part in result.evidence],
        "gap": result.reason if isinstance(result, RequiredPartFailure) else None,
    }


def archive_observation(
    reader: DocumentTextExtractor,
    raw: bytes,
    targets: dict[str, tuple[str, str | None]],
    *,
    archive_admission: bool = False,
) -> dict[str, Any]:
    observed: dict[str, Any] = {"sha256": digest(raw)}
    try:
        extracted = reader.extract_parts(raw)
        document = select_document_parts(extracted, max_part_chars=None)
    except (OSError, ValueError) as exc:
        return {**observed, "status": "read_error", "error_type": type(exc).__name__}
    if archive_admission:
        observed["archive_admission"] = _archive_admission(document, observed["sha256"])
        correspondence = []
        for part in extracted.parts:
            forwarding = is_rcl_amendment_transmittal(part.text)
            quotes = correspondence_quotes(part.text)
            if forwarding or quotes:
                correspondence.append(
                    {
                        "path": part.members,
                        "sha256": part.sha256,
                        "text_sha256": part.text_sha256,
                        "forwarding_only": forwarding,
                        "quoted_replacements": quotes,
                    }
                )
        observed["correspondence"] = correspondence
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
        "selection": document.selection.state,
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


def inspect(
    root: Path, *, project_targets: bool = False, archive_admission: bool = False
) -> dict[str, Any]:
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
                        archive_admission=archive_admission,
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
    parser.add_argument("--archive-admission", action="store_true")
    args = parser.parse_args()
    root = args.corpus.resolve(strict=True)
    output = args.output.resolve()
    if output.is_relative_to(root) or args.output.is_symlink():
        parser.error("Output must be a new file outside the corpus")
    write_json(
        output,
        inspect(
            root, project_targets=args.project_targets, archive_admission=args.archive_admission
        ),
    )


if __name__ == "__main__":
    main()
