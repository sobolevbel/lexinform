"""Replay Senate attachment selection using downloaded files and independent process stages."""

import argparse
import datetime as dt
import gzip
import hashlib
import json
import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from lexinform.models import Attachment, PrintInfo
from lexinform.senate_documents import inspect_senate_resolution, senate_subject_key
from lexinform.services.analysis import text_digest
from lexinform.services.documents import TextLoader
from lexinform.services.senate_documents import resolve_senate_document, verified_senate_text


class RecordedFile(BaseModel):
    term: int
    number: str
    name: str
    url: str
    id: str
    file: str
    sha256: str
    text: str = ""
    pages: int = 0
    extraction_error: str = ""


class RecordedStage(BaseModel):
    bill: str
    date: dt.date | None
    position: str
    title: str
    title_final: str = ""


class RecordedExtractor:
    def __init__(self, root: Path, records: list[RecordedFile]) -> None:
        self.root = root
        self.records = {record.sha256: record for record in records}

    def extract(self, data: bytes) -> str:
        record = self.records[hashlib.sha256(data).hexdigest()]
        if record.extraction_error:
            raise ValueError(record.extraction_error)
        return gzip.decompress((self.root / record.text).read_bytes()).decode()

    def pages(self, data: bytes) -> int:
        return self.records[hashlib.sha256(data).hexdigest()].pages

    def select_pages(self, data: bytes, *, first: int, count: int) -> bytes:
        raise AssertionError("Corpus selection must not request visual model analysis")


_MUTATIONS = (
    "w art. 1 termin określa się na dzień 1 grudnia 2027 r.;",
    "w art. 1 kwotę 200 zł zastępuje się kwotą 300 zł;",
    "w art. 1 wyrazy „przysługuje” zastępuje się wyrazami „nie przysługuje”;",
    "w art. 1 dodaje się osoby posiadające zezwolenie na pobyt stały;",
    "w art. 1 odesłanie do art. 15 ust. 2 zastępuje się odesłaniem do art. 15 ust. 3;",
    "w załączniku kwotę:\n200\nzastępuje się kwotą:\n300\n;",
    "w załączniku kwotę 1,25 zastępuje się kwotą 1,52;",
)


def mutation_probes(text: str, *, word_eof: bool = False) -> int:
    evidence = inspect_senate_resolution(text, word_eof=word_eof)
    assert evidence.complete and evidence.outcome == "amendments"
    offset = text.index(evidence.operative_text)
    if word_eof:
        for before, after in (("1–11", "1–12"), ("lit. b", "lit. c"), ("13–27", "13–28")):
            changed = text.replace(before, after)
            inspected = inspect_senate_resolution(changed, word_eof=True)
            assert inspected.complete and changed == inspected.text
            assert text_digest(changed) != text_digest(text)
        return 3
    for amendment in _MUTATIONS:
        changed = text[:offset] + "\n" + amendment + "\n" + text[offset:]
        inspected = inspect_senate_resolution(changed)
        assert inspected.complete and amendment in inspected.operative_text
        assert amendment in inspected.text and text_digest(text) != text_digest(inspected.text)
        assert changed == inspected.text
    for suffix in (" nie", " oraz cudzoziemców", " 2027", " art. 15 ust. 3"):
        assert senate_subject_key(evidence.subject) != senate_subject_key(evidence.subject + suffix)
    return len(_MUTATIONS)


def negative_controls(corpus: Path) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    matches: list[dict[str, str]] = []
    fingerprints: dict[str, str] = {}
    sources = (
        ("sejm", corpus / "sejm/term10/text"),
        ("rcl", corpus / "documents/text"),
    )
    for label, directory in sources:
        for path in sorted(directory.glob("*.txt.gz")):
            raw = path.read_bytes()
            fingerprints[str(path.relative_to(corpus))] = hashlib.sha256(raw).hexdigest()
            text = gzip.decompress(raw).decode()
            counts[label] += 1
            evidence = inspect_senate_resolution(text)
            if evidence.complete and evidence.outcome == "amendments":
                matches.append({"path": str(path), "subject": evidence.subject})
    return {"counts": counts, "matches": matches, "input_sha256": fingerprints}


def replay(root: Path, corpus: Path) -> dict[str, Any]:
    catalog = json.loads((root / "catalog.json").read_text())
    stages = {
        key: [RecordedStage.model_validate(row) for row in rows]
        for key, rows in json.loads((root / "stage-evidence.json").read_text()).items()
    }
    records = [
        RecordedFile.model_validate_json(path.read_text())
        for path in sorted((root / "extracted").glob("*.json"))
    ]
    by_url = {record.url: record for record in records}
    for record in records:
        assert hashlib.sha256((root / record.file).read_bytes()).hexdigest() == record.sha256
    extractor = RecordedExtractor(root, records)

    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        return (root / by_url[url].file).read_bytes()

    loader = TextLoader(
        {"api.sejm.gov.pl": download}, extractor, max_bytes=24 * 1024 * 1024, cache_size=2048
    )
    rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    classification: Counter[str] = Counter()
    file_rows: list[dict[str, Any]] = []
    for record in records:
        if record.extraction_error:
            classification[f"{record.term}/extraction_error"] += 1
            continue
        text = extractor.extract((root / record.file).read_bytes())
        evidence = inspect_senate_resolution(
            text, word_eof=record.pages == 0 and Path(record.name).suffix.casefold() != ".pdf"
        )
        reason = evidence.outcome if evidence.complete else evidence.limitation
        classification[f"{record.term}/{reason}"] += 1
        file_rows.append({"id": record.id, "reason": reason, "subject": evidence.subject})
    for row in catalog["records"]:
        key = f"{row['term']}/{row['number']}"
        available = [r for r in records if (r.term, r.number) == (row["term"], row["number"])]
        missing = set(row["attachments"]) - {record.name for record in available}
        assert not missing, (key, missing)
        info = PrintInfo(
            term=row["term"],
            number=row["number"],
            title=row["title"],
            document_date=dt.date.fromisoformat(row["documentDate"])
            if row.get("documentDate")
            else None,
            delivery_date=dt.date.fromisoformat(row["deliveryDate"])
            if row.get("deliveryDate")
            else None,
            attachments=tuple(
                Attachment(print_number=r.number, name=r.name, url=r.url) for r in available
            ),
        )
        links = stages.get(key, [])
        amended = [stage for stage in links if "popraw" in stage.position]
        dates = {stage.date for stage in links if stage.date}
        if len(dates) != 1:
            counts[f"{row['term']}/no_unique_stage_date"] += 1
            rows.append({"key": key, "selected": None, "reason": "no_unique_stage_date"})
            continue
        adopted_on = next(iter(dates))
        titles = tuple(title for stage in links for title in (stage.title, stage.title_final))
        document = resolve_senate_document(loader, info, adopted_on=adopted_on, titles=titles)
        renamed = info.model_copy(
            update={
                "attachments": tuple(
                    attachment.model_copy(
                        update={"name": f"random-{i}{Path(attachment.name).suffix}"}
                    )
                    for i, attachment in enumerate(reversed(info.attachments))
                )
            }
        )
        repeated = resolve_senate_document(loader, renamed, adopted_on=adopted_on, titles=titles)
        assert bool(document) == bool(repeated), key
        if document and repeated:
            assert document.senate and repeated.senate
            assert document.senate.scan == repeated.senate.scan
            if document.senate.scan:
                assert document.senate.sha256 == repeated.senate.sha256
            else:
                assert verified_senate_text(loader, document) == verified_senate_text(
                    loader, repeated
                )
        if not amended:
            assert document is None or (document.senate and document.senate.scan), key
            document = None
        old = next(
            (
                a
                for a in info.attachments
                if re.fullmatch(
                    rf"{re.escape(info.number)}[- ]+uchwa[łl]a\.docx", a.name.strip(), re.I
                )
            ),
            info.main_pdf,
        )
        old_evidence = inspect_senate_resolution(loader.load(old.url) or "") if old else None
        old_complete = bool(
            old_evidence and old_evidence.complete and old_evidence.outcome == "amendments"
        )
        selected = next((r for r in available if document and r.url == document.url), None)
        visual = bool(document and document.senate and document.senate.scan)
        probes = (
            mutation_probes(
                verified_senate_text(loader, document),
                word_eof=bool(document.senate and document.senate.word_eof),
            )
            if document and not visual
            else 0
        )
        if document:
            assert amended, (key, [stage.position for stage in links])
        outcome = (
            "scan_pending_verification"
            if visual
            else "selected"
            if document
            else "unresolved"
            if amended
            else "not_amendments"
        )
        counts[f"{row['term']}/{outcome}"] += 1
        counts["mutation_probes"] += probes
        rows.append(
            {
                "key": key,
                "stage_bills": [s.bill for s in links],
                "positions": [s.position for s in links],
                "date": str(adopted_on),
                "selected": selected.name if selected else None,
                "selected_url": document.url if document else None,
                "selection_mode": "scan" if visual else "text" if document else None,
                "old_selected": old.name if old else None,
                "old_structurally_complete": old_complete,
                "old_inspection": old_evidence.model_dump(
                    mode="json", exclude={"text", "operative_text"}
                )
                if old_evidence
                else None,
                "amendment_stage": bool(amended),
                "mutations": probes,
                "candidates": [
                    item for item in file_rows if item["id"] in {r.id for r in available}
                ],
            }
        )
    negatives = negative_controls(corpus)
    assert not negatives["matches"], negatives["matches"]
    return {
        "input_sha256": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in ("catalog.json", "stage-evidence.json")
        },
        "code_sha256": {
            name: hashlib.sha256(
                (Path(__file__).resolve().parents[1] / name).read_bytes()
            ).hexdigest()
            for name in (
                "src/lexinform/senate_documents.py",
                "src/lexinform/services/senate_documents.py",
                "src/lexinform/services/analysis.py",
            )
        },
        "catalog_sources": catalog["sources"],
        "counts": counts,
        "classification": classification,
        "files": len(records),
        "rows": rows,
        "negative_controls": negatives,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("supplement", type=Path)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.exists() or any(
        args.output.resolve().is_relative_to(path.resolve())
        for path in (args.supplement, args.corpus)
    ):
        parser.error("Output must be a new file outside both input corpora")
    logging.basicConfig(level=logging.ERROR)
    report = replay(args.supplement, args.corpus)
    with args.output.open("x") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {
                "counts": report["counts"],
                "negative_controls": {
                    "counts": report["negative_controls"]["counts"],
                    "matches": report["negative_controls"]["matches"],
                },
            }
        )
    )


if __name__ == "__main__":
    main()
