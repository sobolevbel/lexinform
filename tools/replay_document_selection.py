"""Offline before/after replay of document packages and metadata; inputs remain read-only."""

import argparse
import hashlib
import json
import logging
import re
import zipfile
from pathlib import Path
from typing import Any

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.adapters.sejm_api import parse_print
from lexinform.law_digest import law_digest
from lexinform.models import RclProject
from lexinform.sections import document_kind, trim_print


def replay(root: Path, *, all_word: bool = False) -> dict[str, Any]:
    projects: dict[str, Any] = {}
    prints: dict[str, Any] = {}
    packages: dict[str, Any] = {}
    extractor = DocumentTextExtractor(
        PypdfTextExtractor(),
        DocxTextExtractor(),
        DocTextExtractor(),
        max_member_bytes=32_000_000,
        max_part_chars=400_000,
    )
    for path in sorted((root / "corpus").glob("1*.json")):
        project = RclProject.model_validate_json(path.read_bytes())
        for stage in project.reached_stages:
            snapshot = project.model_copy(
                update={
                    "stages": tuple(st for st in project.stages if st.number <= stage.number),
                }
            )
            projects[f"{project.id}/{stage.number}"] = {
                role: {"id": doc.id, "name": doc.name, "created": str(doc.created)}
                for role, doc in snapshot.text_documents().items()
            }
    for term in (8, 9, 10):
        for raw in json.loads((root / f"sejm/term{term}/prints.json").read_bytes()):
            info = parse_print(raw, term=term)
            prints[f"{term}/{info.number}"] = info.main_pdf.name if info.main_pdf else None
    for path in sorted((root / "documents/files").glob("*")):
        try:
            if all_word:
                if path.suffix not in (".docx", ".docm", ".xml"):
                    continue
                nested = []
            elif path.suffix == ".zip":
                with zipfile.ZipFile(path) as archive:
                    nested = [n for n in archive.namelist() if n.lower().endswith(".zip")]
            else:
                continue
            if (
                not all_word
                and len(nested) < 2
                and path.stem
                not in {
                    "771111",
                    "718474",
                    "719233",
                    "709230",
                    "711435",
                    "711610",
                    "618466",
                    "712314",
                    "736714",
                    "778150",
                    "785848",
                }
            ):
                continue
            data = path.read_bytes()
            text = extractor.extract(data)
            trimmed = trim_print(text).text
            digest = law_digest(trimmed)
            mutations: dict[str, bool] = {}
            if digest is not None:
                for label, mutation in (
                    ("date", "Termin: 17 października 2027 r."),
                    ("amount", "Kwota wynosi 500 zł."),
                    ("negation", "Nie przysługuje prawo do świadczenia."),
                    ("scope", "Przepisy stosuje się także do cudzoziemców."),
                    ("reference", "Stosuje się art. 15 ust. 3 ustawy."),
                ):
                    article = re.search(r"\bArt\.\s*1\s*[.)]", trimmed)
                    if article is not None:
                        changed = (
                            trimmed[: article.end()]
                            + " "
                            + mutation
                            + " "
                            + trimmed[article.end() :]
                        )
                        mutations[label] = law_digest(changed) != digest
            packages[path.name] = {
                "sha256": hashlib.sha256(data).hexdigest(),
                "chars": len(text),
                "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "kind": document_kind(text),
                "law_digest": digest,
                "opening": text[:250],
                "mutations": mutations,
            }
        except Exception as exc:
            packages[path.name] = {"error": f"{type(exc).__name__}: {exc}"}
    return {"projects": projects, "prints": prints, "packages": packages}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--all-word", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    result = replay(args.corpus, all_word=args.all_word)
    with args.output.open("x") as stream:
        json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2)
    print(json.dumps({key: len(value) for key, value in result.items()}))


if __name__ == "__main__":
    main()
