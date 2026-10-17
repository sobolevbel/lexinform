"""Inspect corpus selectors and extracted document roles without changing application state."""

import argparse
import gzip
import hashlib
import json
import logging
import subprocess
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any
from unittest.mock import patch

from lexinform.adapters import document_text
from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.law_digest import law_digest
from lexinform.models import RclProject
from lexinform.models.rcl import text_role
from lexinform.sections import document_kind, trim_print


def json_file(path: Path) -> Any:
    return json.loads(path.read_bytes())


def cached_text(root: Path, identity: int) -> str | None:
    path = root / f"documents/text/{identity}.txt.gz"
    return gzip.decompress(path.read_bytes()).decode(errors="replace") if path.exists() else None


def inspect(root: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    extractor = DocumentTextExtractor(
        PypdfTextExtractor(),
        DocxTextExtractor(),
        DocTextExtractor(),
        max_member_bytes=200 * 1024**2,
        max_part_chars=300_000,
    )
    counts: Counter[str] = Counter()
    findings: dict[str, list[Any]] = {}
    questions: dict[str, Any] = {}

    def add(kind: str, value: Any) -> None:
        findings.setdefault(kind, []).append(value)

    for path in sorted((root / "corpus").glob("1*.json")):
        project = RclProject.model_validate_json(path.read_bytes())
        latest = project.text_documents().get("bill")
        for stage in project.reached_stages:
            selected = project.model_copy(update={"stages": (stage,)}).text_documents()
            bill = selected.get("bill")
            documents = stage.documents("project")
            for document in documents:
                text = cached_text(root, document.id)
                if text is None:
                    continue
                kind = document_kind(text)
                role = text_role(document.name)
                if kind == "bill" and (bill is None or bill.id != document.id) and role != "bill":
                    add(
                        "bill_content_not_bill_name",
                        {
                            "project": project.id,
                            "stage": stage.number,
                            "id": document.id,
                            "name": document.name,
                            "named_role": role,
                            "selected": bill.id if bill else None,
                            "opening": text[:800],
                        },
                    )
                if (
                    bill is not None
                    and bill.id == document.id
                    and kind not in {"bill", "unknown", "letter"}
                ):
                    add(
                        "selected_wrong_content",
                        {
                            "project": project.id,
                            "stage": stage.number,
                            "id": document.id,
                            "name": document.name,
                            "kind": kind,
                            "latest": latest is not None and latest.id == document.id,
                            "opening": text[:800],
                        },
                    )
                if (
                    bill is not None
                    and bill.id == document.id
                    and kind == "unknown"
                    and latest is not None
                    and latest.id == document.id
                ):
                    questions[f"role-{document.id}"] = {
                        "task": "role",
                        "project": project.id,
                        "title": project.title,
                        "selected_id": document.id,
                        "documents": [
                            {
                                "id": document.id,
                                "name": document.name,
                                "excerpt": text[:6500],
                                "chars": len(text),
                            }
                        ],
                    }
        reversed_project = project.model_copy(
            update={
                "stages": tuple(
                    stage.model_copy(
                        update={
                            "folders": tuple(
                                folder.model_copy(
                                    update={"documents": tuple(reversed(folder.documents))}
                                )
                                for folder in stage.folders
                            )
                        }
                    )
                    for stage in project.stages
                )
            }
        )
        other = reversed_project.text_documents().get("bill")
        if latest is not None and other is not None and latest.id != other.id:
            questions[f"version-{project.id}"] = {
                "task": "version",
                "project": project.id,
                "title": project.title,
                "selected_id": latest.id,
                "documents": [
                    {
                        "id": doc.id,
                        "name": doc.name,
                        "uploaded": str(doc.created),
                        "excerpt": (cached_text(root, doc.id) or "")[:6500],
                        "chars": len(cached_text(root, doc.id) or ""),
                    }
                    for doc in (latest, other)
                ],
            }
    records = json_file(root / "documents/index.json")
    for path in sorted((root / "documents/files").glob("*.zip")):
        try:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                nested = [name for name in names if name.lower().endswith(".zip")]
                annexes = [name for name in names if "zał" in name.lower() or "zal" in name.lower()]
                if len(nested) > 1:
                    children: list[dict[str, Any]] = []
                    for name in nested:
                        data = archive.read(name)
                        body = extractor.extract(data)
                        children.append(
                            {
                                "name": name,
                                "chars": len(body),
                                "kind": document_kind(body),
                                "opening": body[:500],
                                "sha256": hashlib.sha256(data).hexdigest(),
                            }
                        )
                    counts["multiple_nested_checked"] += 1
                    nonempty = [child for child in children if child["chars"]]
                    if (
                        nonempty
                        and nonempty[0]["kind"] != "bill"
                        and any(child["kind"] == "bill" for child in nonempty[1:])
                    ):
                        add(
                            "nested_nonbill_before_bill",
                            {"id": int(path.stem), "children": children},
                        )
                    (output / f"package-{path.stem}.json").write_text(
                        json.dumps(children, ensure_ascii=False, indent=2)
                    )
                if annexes:
                    add(
                        "zip_named_annexes",
                        {
                            "id": int(path.stem),
                            "names": annexes,
                            "project": records.get(path.stem, {}).get("project"),
                        },
                    )
        except (zipfile.BadZipFile, RuntimeError, ValueError) as exc:
            add("zip_error", {"id": path.stem, "error": type(exc).__name__})
    for identity in (
        418603,
        418609,
        712314,
        730977,
        730985,
        779502,
        779503,
        791866,
        791867,
        791873,
    ):
        matches = list((root / "documents/files").glob(f"{identity}.*"))
        if not matches:
            continue
        path = matches[0]
        data = path.read_bytes()
        text = extractor.extract(data)
        (output / f"text-{identity}.txt").write_text(text)
        add(
            "reextracted",
            {
                "id": identity,
                "bytes": len(data),
                "chars": len(text),
                "kind": document_kind(text),
                "sha256": hashlib.sha256(data).hexdigest(),
            },
        )
    for identity in (711435, 711610):
        path = root / f"documents/files/{identity}.zip"
        if not path.exists():
            continue
        data = path.read_bytes()
        baseline = extractor.extract(data)
        with patch.object(document_text, "_MAX_ARCHIVE_DEPTH", 3):
            deeper = extractor.extract(data)
        add(
            "depth_mutation",
            {
                "id": identity,
                "baseline_kind": document_kind(baseline),
                "baseline_chars": len(baseline),
                "depth3_kind": document_kind(deeper),
                "depth3_chars": len(deeper),
            },
        )
    for identity in (771111, 718474, 719233, 709230):
        path = root / f"documents/files/{identity}.zip"
        if not path.exists():
            continue
        with zipfile.ZipFile(path) as outer:
            for name in outer.namelist():
                if not name.endswith(".zip") or "projekt" not in name.lower():
                    continue
                nested_path = output / f"deflate64-{identity}.zip"
                nested_path.write_bytes(outer.read(name))
                with zipfile.ZipFile(nested_path) as inner:
                    bills = [
                        info
                        for info in inner.infolist()
                        if "projekt" in info.filename.lower()
                        and info.file_size
                        and info.filename.endswith((".docx", ".xml"))
                    ]
                    for member in bills:
                        if member.file_size > 1_000_000 or member.compress_type != 9:
                            continue
                        recovered = subprocess.run(
                            ["/usr/bin/unzip", "-p", str(nested_path), member.filename],
                            capture_output=True,
                            check=True,
                        ).stdout
                        if len(recovered) != member.file_size:
                            raise ValueError("Recovered member size differs from the archive index")
                        text = extractor.extract(recovered)
                        add(
                            "deflate64_recovery",
                            {
                                "id": identity,
                                "member": member.filename,
                                "sha256": hashlib.sha256(recovered).hexdigest(),
                                "chars": len(text),
                                "kind": document_kind(text),
                            },
                        )
    archive_path = root / "documents/files/618466.zip"
    if archive_path.exists():
        with zipfile.ZipFile(archive_path) as archive:
            texts = {
                name: extractor.extract(archive.read(name))
                for name in archive.namelist()
                if name.endswith(".docx")
            }
        bill_text = next(text for name, text in texts.items() if name.startswith("Projekt ustawy"))
        annex = next(text for name, text in texts.items() if name.startswith("Zalacznik do ustawy"))
        justification = next(
            text for name, text in texts.items() if name.startswith("Uzasadnienie")
        )
        osr = next(text for name, text in texts.items() if "OSR" in name)
        for order, parts in (
            ("before_osr", (bill_text, annex, justification, osr)),
            ("after_osr", (bill_text, justification, osr, annex)),
        ):
            package_text = "\f".join(parts)
            mutated = package_text.replace(
                "Budowa linii 400 kV Ełk Bis", "Budowa linii 500 kV Ełk Bis"
            )
            add(
                "annex_placement",
                {
                    "order": order,
                    "annex_in_trimmed": "Budowa linii 400 kV Ełk Bis"
                    in trim_print(package_text).text,
                    "mutation_changes_law_digest": law_digest(package_text) != law_digest(mutated),
                },
            )
    for finding_kind, rows in findings.items():
        counts[finding_kind] = len(rows)
    result = {"counts": dict(counts), "findings": findings}
    (output / "findings.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    (output / "questions.json").write_text(json.dumps(questions, ensure_ascii=False, indent=2))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new output directory; prior research inputs remain immutable")
    logging.basicConfig(level=logging.ERROR)
    print(json.dumps(inspect(args.corpus, args.output)["counts"]))


if __name__ == "__main__":
    main()
