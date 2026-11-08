"""Replay explicit RCL self-amendment targets against original Sejm print attachments."""

import argparse
import datetime as dt
import gzip
import json
from pathlib import Path
from typing import Any

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.adapters.sejm_api import parse_process_summary
from lexinform.document_editions import candidate_edition
from lexinform.models import DocumentPart, DocumentSelectionRecord, RclProject
from lexinform.rcl_amendments import (
    rcl_amendment_base_index,
    rcl_self_amendment_part,
    rcl_self_amendment_print_number,
)
from tools.audit_rcl_amendment_archives import AS_OF
from tools.corpus_artifacts import digest, write_json
from tools.corpus_rcl import document_id, stage_cases
from tools.corpus_runtime import CorpusReads, source_fingerprints
from tools.replay_proposal_edition import PinnedInput


def replay(root: Path, edition_report: bytes, member_report: bytes) -> dict[str, Any]:
    editions = json.loads(edition_report)
    if digest(member_report) != editions["previous_sha256"]:
        raise ValueError("Member report SHA-256 differs from the edition report")
    members = json.loads(member_report)["proposals"]
    reads, sources = CorpusReads(root), source_fingerprints()
    extractor = DocumentTextExtractor(PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor())
    rows = {}
    with reads.guard():
        for pair, row in editions["observations"].items():
            archive_id, owner = pair.split("/")
            evidence = members[pair]
            project_path = f"corpus/{owner}.json"
            project = RclProject.model_validate_json(
                PinnedInput(path=project_path, sha256=evidence["project_sha256"]).read(root)
            )
            archive = PinnedInput(
                path=f"documents/files/{archive_id}.zip", sha256=evidence["archive_sha256"]
            ).read(root)
            member = rcl_self_amendment_part(
                extractor.extract_parts(archive).parts,
                project_title=project.title,
                wykaz_number=project.wykaz_number,
            )
            if member is None or (member.sha256, member.text_sha256) != (
                evidence["member_sha256"],
                evidence["member_text_sha256"],
            ):
                raise ValueError(f"Amendment member differs from the pinned observation: {pair}")
            if rcl_self_amendment_print_number(member.text) != row["member_print_number"]:
                raise ValueError(f"Print reference differs from the pinned observation: {pair}")
            if not row["member_print_number"]:
                rows[pair] = {"status": "no_explicit_print"}
                continue
            if len(row["same_rm_prints"]) != 1:
                rows[pair] = {"status": "unresolved_print"}
                continue
            ((term, reference),) = row["same_rm_prints"].items()
            rows[pair] = result(root, extractor, project, member, archive_id, term, reference, row)
    if reads.changed_inputs() or sources != source_fingerprints():
        raise ValueError("Replay sources or corpus inputs changed during execution")
    return {
        "edition_report_sha256": digest(edition_report),
        "member_report_sha256": digest(member_report),
        "input_sha256": reads.initial_sha256,
        "source_sha256": sources,
        "observations": rows,
    }


def result(
    root: Path,
    extractor: DocumentTextExtractor,
    project: RclProject,
    member: DocumentPart,
    archive_id: str,
    term: str,
    reference: dict[str, Any],
    row: dict[str, Any],
) -> dict[str, Any]:
    term_path = f"sejm/term{term}"
    print_number = reference["print_number"]
    process_bytes = PinnedInput(
        path=f"{term_path}/process_details.json.gz", sha256=reference["processes_sha256"]
    ).read(root)
    process = parse_process_summary(json.loads(gzip.decompress(process_bytes))[print_number])
    if (process.term, process.number) != (int(term), print_number):
        raise ValueError("Print process differs from the pinned reference")
    identity = {"term": process.term, "number": process.number, "rm_number": process.rcl_num}
    if reference["primary_sha256"] is None:
        return {"status": "original_unavailable", "print": identity}

    name = f"{print_number}-ustawa.docx"
    print_path = root / term_path / "prints" / f"{print_number}.json"
    metadata = json.loads(print_path.read_bytes())
    if (metadata["term"], metadata["number"]) != (process.term, process.number) or sum(
        item["name"] == name and item.get("kind") == "bill" for item in metadata["attachments"]
    ) != 1:
        raise ValueError("Original attachment is absent from the pinned print")
    original = PinnedInput(
        path=f"{term_path}/files/{print_number}__{name}", sha256=reference["primary_sha256"]
    ).read(root)
    amendment_urls = {
        doc.url
        for stage in project.reached_stages
        for doc in project.stage_documents(stage)
        if str(doc.id) == archive_id
    }
    declared = {
        doc.url: doc
        for case in stage_cases(root / "corpus" / f"{project.id}.json", project, as_of=AS_OF)
        if case.document is not None
        for doc in (case.document, *case.document.alternatives)
        if amendment_urls.intersection(doc.amendment_urls)
    }
    if declared.keys() != row["base_candidates"].keys():
        raise ValueError("RCL candidate graph differs from the pinned edition report")
    selections = []
    urls = sorted(declared)
    for url in urls:
        doc = declared[url]
        if doc.official_link_conflict or doc.normative_urls or doc.required_urls:
            return {"status": "incomplete_base", "print": identity}
        file_sha = row["base_candidates"][url]["file_sha256"]
        if file_sha is None:
            return {"status": "incomplete_base", "print": identity}
        paths = sorted((root / "documents/files").glob(f"{document_id(url).decode()}.*"))
        if len(paths) != 1:
            raise ValueError("Expected one pinned RCL file")
        body = PinnedInput(path=str(paths[0].relative_to(root)), sha256=file_sha).read(root)
        selections.append(extractor.extract_document(body).selection)
    index = rcl_amendment_base_index(
        member,
        project_rm_number=project.rm_number,
        print_process=process,
        original=extractor.extract_document(original).selection,
        candidates=tuple(selections),
    )
    if index is None:
        return {"status": "no_unique_exact_base", "print": identity, "candidates": urls}
    edition = candidate_edition(
        DocumentSelectionRecord.from_selection(selections[index]),
        recorded_at=dt.datetime(1970, 1, 1, tzinfo=dt.UTC),
    )
    if edition is None:
        raise ValueError("Matching body has no addressable candidate edition")
    return {
        "status": "exact_base",
        "print": identity,
        "base_url": urls[index],
        "edition_id": edition.edition_id,
        "member_sha256": member.sha256,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("corpus", "edition_report", "member_report", "output"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    root = args.corpus.resolve(strict=True)
    output = args.output.resolve()
    if output.is_relative_to(root) or args.output.is_symlink():
        parser.error("Output must be a new file outside the corpus")
    write_json(
        output, replay(root, args.edition_report.read_bytes(), args.member_report.read_bytes())
    )


if __name__ == "__main__":
    main()
