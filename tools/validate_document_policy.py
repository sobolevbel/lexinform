"""Replay labelled draft dates and container invariance on frozen real corpus documents."""

import argparse
import datetime as dt
import gzip
import hashlib
import io
import json
import logging
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.document_versions import comparable_versions, draft_version
from lexinform.models import TextDocument
from lexinform.services.documents import TextLoader


def word(text: str) -> bytes:
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    root = ET.Element(f"{{{ns}}}document")
    body = ET.SubElement(root, f"{{{ns}}}body")
    for line in text.splitlines():
        paragraph = ET.SubElement(body, f"{{{ns}}}p")
        run = ET.SubElement(paragraph, f"{{{ns}}}r")
        ET.SubElement(run, f"{{{ns}}}t").text = line
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("word/document.xml", ET.tostring(root))
    return stream.getvalue()


def package(entries: list[tuple[str, bytes]]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return stream.getvalue()


class ReplayFiles:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    def __call__(self, url: str, *, max_bytes: int | None = None) -> bytes:
        return self.files[url]


def replay(corpus: Path, inventory: Path) -> dict[str, Any]:
    extractor = DocumentTextExtractor(PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor())
    groups = json.loads(gzip.decompress(inventory.read_bytes()))["groups"]
    labelled = [g for g in groups if g["category"] == "different_header_dates_parser_gap"]
    pairs = [[doc["id"] for doc in group["documents"]] for group in labelled]
    pairs.append(["280566", "272563"])
    pairs.append(["705153", "688101"])
    update_dates = {"705153": "2025-02-14", "705164": "2025-02-14", "688101": "2024-10-18"}
    texts: dict[str, str] = {}
    hashes: dict[str, str] = {}
    dates: dict[str, dict[str, str | None]] = {}
    identities_to_read = dict.fromkeys(
        [*(identity for pair in pairs for identity in pair), "705164"]
    )
    for identity in identities_to_read:
        paths = list((corpus / "documents/files").glob(f"{identity}.*"))
        assert len(paths) == 1, (identity, paths)
        raw = paths[0].read_bytes()
        text = extractor.extract(raw)
        texts[identity] = text
        hashes[identity] = hashlib.sha256(raw).hexdigest()
        actual = draft_version(text)
        expected = next(
            (
                doc["header"]
                for group in labelled
                for doc in group["documents"]
                if doc["id"] == identity
            ),
            update_dates.get(identity, str(actual) if actual else None),
        )
        assert str(actual) == expected, (identity, actual, expected)
        dates[identity] = {"expected": expected, "actual": str(actual) if actual else None}
    results: list[dict[str, Any]] = []
    mutations = 0
    for identities in pairs:
        expected = max(
            identities, key=lambda identity: draft_version(texts[identity]) or dt.date.min
        )
        expected_text = texts[expected]
        for reverse in (False, True):
            ordered = list(reversed(identities)) if reverse else identities
            # Names deliberately oppose the internal version and change with order.
            entries = [
                (f"{index}-Projekt.docx", word(texts[identity]))
                for index, identity in enumerate(ordered)
            ]
            files = {f"https://replay.test/{name}": raw for name, raw in entries}
            files["https://replay.test/archive"] = package(entries)

            loader = TextLoader(
                {"replay.test": ReplayFiles(files)}, extractor, max_bytes=32_000_000
            )
            candidates = [
                TextDocument(url=url, kind="rcl", version_basis=("rcl/replay/stage/1",))
                for url in files
                if not url.endswith("archive")
            ]
            first = candidates[0].model_copy(
                update={"compare_versions": True, "alternatives": tuple(candidates[1:])}
            )
            _, separate = loader.select_document(first)
            _, archived = loader.select_document(
                TextDocument(
                    url="https://replay.test/archive",
                    kind="rcl",
                    version_basis=("rcl/replay/stage/1",),
                )
            )
            # Compare against the same controlled Word representation, not original file bytes.
            control = extractor.extract(word(expected_text))
            assert separate.text == archived.text, (identities, reverse)
            assert separate.selection.state == archived.selection.state, identities
            if identities == ["705153", "688101"]:
                assert separate.text is not None, "The explicit update must resolve h34"
            if separate.text is not None:
                assert separate.text == control, identities
            else:
                assert separate.selection.state == "ambiguous", identities
            results.append(
                {
                    "documents": ordered,
                    "latest_header_candidate": expected,
                    "reverse": reverse,
                    "state": archived.selection.state,
                    "text_sha256": hashlib.sha256((archived.text or "").encode()).hexdigest(),
                }
            )
        article = re.search(r"\bArt\.\s*1\s*[.)]", expected_text)
        assert article is not None, identities
        for change in (
            "Termin: 17 października 2027 r.",
            "Kwota wynosi 500 zł.",
            "Nie przysługuje prawo do świadczenia.",
            "Przepisy stosuje się także do cudzoziemców.",
            "Stosuje się art. 15 ust. 3 ustawy.",
        ):
            changed = (
                expected_text[: article.end()] + " " + change + " " + expected_text[article.end() :]
            )
            assert not comparable_versions([expected_text, changed]), (identities, change)
            mutations += 1
    return {
        "source_sha256": hashes,
        "dates": dates,
        "container_cases": results,
        "semantic_conflict_controls": mutations,
        "limitations": [
            "controlled repackaging, not original byte equivalence",
            "diagnostic cases, not an independent population holdout",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--inventory",
        type=Path,
        default=Path("docs/assets/audits/2026-10-17-document-evidence/residual-inventory.json.gz"),
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    result = replay(args.corpus, args.inventory)
    with args.output.open("x") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(
        json.dumps(
            {
                "documents": len(result["dates"]),
                "container_cases": len(result["container_cases"]),
                "semantic_conflict_controls": result["semantic_conflict_controls"],
            }
        )
    )


if __name__ == "__main__":
    main()
