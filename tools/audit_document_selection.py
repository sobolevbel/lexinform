"""Replay document selectors against local corpus metadata without network or model calls."""

import argparse
import gzip
import hashlib
import json
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from lexinform.adapters.sejm_api import parse_print, parse_process_detail
from lexinform.models import RclProject, RclStage, flatten_stages, latest_text_document
from lexinform.sections import document_kind


def read_json(path: Path) -> Any:
    data = path.read_bytes()
    return json.loads(gzip.decompress(data) if path.suffix == ".gz" else data)


def reverse_documents(stage: RclStage) -> RclStage:
    return stage.model_copy(
        update={
            "folders": tuple(
                folder.model_copy(update={"documents": tuple(reversed(folder.documents))})
                for folder in reversed(stage.folders)
            )
        }
    )


def audit(root: Path) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    findings: dict[str, list[Any]] = {}
    inputs: dict[str, str] = {}

    def record(kind: str, value: Any) -> None:
        findings.setdefault(kind, []).append(value)

    def source(path: Path) -> Any:
        inputs[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
        return read_json(path)

    for term in (8, 9, 10):
        directory = root / f"sejm/term{term}"
        bill_numbers: set[str] = set()
        for number, raw in source(directory / "process_details.json.gz").items():
            if raw.get("documentTypeEnum") != "BILL":
                counts["excluded_non_bill_processes"] += 1
                continue
            counts[f"bill_processes_term{term}"] += 1
            bill_numbers.add(number)
            detail = parse_process_detail(raw)
            stages = list(flatten_stages(detail.stages))
            after = [s for s in stages if s.text_after3]
            if len({s.text_after3 for s in after}) > 1:
                record(
                    "multiple_adopted_texts",
                    {
                        "bill": f"{term}/{number}",
                        "stages": [s.model_dump(mode="json") for s in after],
                    },
                )
            for stage in stages:
                if stage.stage_type == "CommitteeReport":
                    counts[f"report/{term}/{stage.carries_bill_text}/{stage.proposal}"] += 1
                    if stage.report_file and stage.proposal is None:
                        record(
                            "report_without_proposal",
                            {"bill": f"{term}/{number}", "stage": stage.model_dump(mode="json")},
                        )
                if stage.text_after3:
                    counts[f"adopted_text/{term}/{stage.stage_type}"] += 1
            selected = latest_text_document(detail.stages)
            if selected:
                counts[f"selected/{term}/{selected.kind}"] += 1
            for stage in detail.stages:
                if (
                    stage.stage_type == "SenatePositionConsideration"
                    and stage.decision
                    and not stage.text_after3
                ):
                    counts[f"senate_consideration_without_text/{term}/{stage.decision}"] += 1
            for index in range(len(detail.stages)):
                prefix = detail.stages[: index + 1]
                reports = [s for s in flatten_stages(prefix) if s.carries_bill_text]
                dated = [s for s in reports if s.date]
                if dated and reports and dated[-1].date != max(s.date for s in dated if s.date):
                    record(
                        "report_tree_order_differs_from_dates",
                        {"bill": f"{term}/{number}", "at": index},
                    )
        for raw in source(directory / "prints.json"):
            counts[f"print_metadata_term{term}"] += 1
            info = parse_print(raw, term=term)
            selected_pdf = info.main_pdf
            reversed_pdf = info.model_copy(
                update={"attachments": tuple(reversed(info.attachments))}
            ).main_pdf
            if selected_pdf != reversed_pdf:
                record(
                    "print_order_sensitive",
                    {
                        "print": f"{term}/{info.number}",
                        "bill_process": info.number in bill_numbers,
                        "title": info.title,
                        "files": [a.name for a in info.attachments],
                        "selected": selected_pdf.name if selected_pdf else None,
                        "reversed": reversed_pdf.name if reversed_pdf else None,
                    },
                )
            if selected_pdf is None:
                counts[f"prints_without_pdf_term{term}"] += 1

    documents = source(root / "documents/index.json")
    for path in sorted((root / "corpus").glob("1*.json")):
        project = RclProject.model_validate(source(path))
        counts["rcl_projects"] += 1
        picked = project.text_documents()
        reversed_project = project.model_copy(
            update={"stages": tuple(reverse_documents(stage) for stage in project.stages)}
        )
        reversed_picked = reversed_project.text_documents()
        if picked != reversed_picked:
            pairs: list[dict[str, Any]] = []
            for role, document in picked.items():
                other = reversed_picked.get(role)
                if other is None or document.id == other.id:
                    continue
                first = root / f"documents/text/{document.id}.txt.gz"
                second = root / f"documents/text/{other.id}.txt.gz"
                equal = (
                    gzip.decompress(first.read_bytes()) == gzip.decompress(second.read_bytes())
                    if first.exists() and second.exists()
                    else None
                )
                pairs.append(
                    {
                        "role": role,
                        "selected": document.id,
                        "reversed": other.id,
                        "cached_text_equal": equal,
                    }
                )
            record(
                "rcl_latest_order_sensitive",
                {
                    "project": project.id,
                    "selected": {r: d.id for r, d in picked.items()},
                    "reversed": {r: d.id for r, d in reversed_picked.items()},
                    "pairs": pairs,
                },
            )
        if "bill" not in picked:
            record("rcl_without_bill", {"project": project.id, "roles": list(picked)})
        for role, doc in picked.items():
            cached = root / f"documents/text/{doc.id}.txt.gz"
            if cached.exists():
                text = gzip.decompress(cached.read_bytes()).decode(errors="replace")
                kind = document_kind(text)
                counts[f"rcl_selected/{role}/{kind}"] += 1
                if role == "bill" and kind not in {"bill", "unknown", "letter"}:
                    record(
                        "rcl_bill_content_mismatch",
                        {
                            "project": project.id,
                            "id": doc.id,
                            "name": doc.name,
                            "kind": kind,
                            "opening": text[:700],
                        },
                    )
            else:
                counts[f"rcl_selected_missing_text/{role}"] += 1
        for rcl_stage in project.reached_stages:
            kinds = Counter(folder.kind for folder in rcl_stage.folders)
            for folder_kind in ("project", "letters", "response", "positions"):
                if kinds[folder_kind] > 1:
                    counts[f"duplicate_folder_kind/{folder_kind}"] += 1
            stage_project = project.model_copy(update={"stages": (rcl_stage,)})
            current = stage_project.text_documents()
            reversed_stage = reverse_documents(rcl_stage)
            reversed_project = project.model_copy(update={"stages": (reversed_stage,)})
            reverse = reversed_project.text_documents()
            counts["rcl_reached_stages"] += 1
            if current != reverse:
                record(
                    "rcl_order_sensitive",
                    {
                        "project": project.id,
                        "stage": rcl_stage.number,
                        "selected": {r: d.model_dump(mode="json") for r, d in current.items()},
                        "reversed": {r: d.model_dump(mode="json") for r, d in reverse.items()},
                    },
                )
            readable = [d for d in rcl_stage.documents("project") if d.readable]
            if readable and "bill" not in current:
                record(
                    "rcl_readable_stage_without_bill",
                    {
                        "project": project.id,
                        "stage": rcl_stage.number,
                        "files": [d.model_dump(mode="json") for d in readable],
                    },
                )
    counts["rcl_document_index"] = len(documents)
    for path in sorted((root / "documents/files").glob("*.zip")):
        counts["zip_files"] += 1
        try:
            with zipfile.ZipFile(path) as archive:
                nested = [name for name in archive.namelist() if name.lower().endswith(".zip")]
        except zipfile.BadZipFile:
            counts["zip_extension_without_zip"] += 1
            continue
        if nested:
            counts["zip_with_nested"] += 1
        if len(nested) > 1:
            record("multiple_nested_zip", {"document": path.stem, "names": nested})
    return {
        "counts": dict(sorted(counts.items())),
        "finding_counts": {k: len(v) for k, v in findings.items()},
        "findings": findings,
        "input_sha256": inputs,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.corpus)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {"finding_counts": result["finding_counts"], "output": str(args.output)},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
