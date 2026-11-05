# J246 read-only qualification recipe

Run with the repository Python environment and the pinned state path from the evidence. The script prints metadata only and verifies the expected evidence leaves. Save this code as recipe.py beside downloaded evidence.json and source-pins.json before running it. Use PYTHONPATH=<checkout>/src:<checkout> and pass --source-root <checkout> --database <pinned-db-copy>.

```python
"""Reproduce J246 collision and normalization metadata without printing document bodies."""

import argparse
import hashlib
import inspect
import json
import re
import sqlite3
from pathlib import Path

from lexinform.document_fingerprints import text_digest
from lexinform.law_technical import DECORATED_PAGE
from lexinform.models import ReviewContext, ReviewDocument, ReviewInputReference
from lexinform.models.computations import ReviewSourceReference
from tests.scenario.test_commission_reviews import context


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def measure(source_root: Path, database: Path) -> dict[str, object]:
    packet = Path(__file__).resolve().parent
    pins = json.loads((packet / "source-pins.json").read_text())
    for relative, expected in pins.items():
        path = packet / relative if relative == "recipe.py" else source_root / relative
        if sha256(path) != expected:
            raise ValueError("qualification source pin mismatch: " + relative)
    fixture_file = inspect.getsourcefile(context)
    if fixture_file is None or Path(fixture_file).resolve() != (source_root / "tests/scenario/test_commission_reviews.py").resolve():
        raise ValueError("PYTHONPATH must name the pinned source checkout")
    original = context()
    reference = ReviewInputReference.from_context(original, term=10)
    first = original.documents[0]
    spaced = first.text + " "
    callers = {
        "URL_only": original.model_copy(update={"documents": (first.model_copy(update={"url": first.url + "?new=1"}), *original.documents[1:])}),
        "document_order": original.model_copy(update={"documents": tuple(reversed(original.documents))}),
        "trailing_whitespace": original.model_copy(update={"documents": (first.model_copy(update={"text": spaced, "sha256": hashlib.sha256(spaced.encode()).hexdigest()}), *original.documents[1:])}),
    }
    collisions: dict[str, object] = {"kind": "existing typed four-document commission context; technical controls, not legal gold"}
    for name, caller in callers.items():
        texts = {hashlib.sha256(doc.text.encode()).hexdigest(): doc.text for doc in caller.documents if hashlib.sha256(doc.text.encode()).hexdigest() == doc.sha256}
        restored = tuple(ReviewDocument.model_validate({**doc.model_dump(), "text": texts[doc.sha256]}) for doc in reference.documents if doc.sha256 in texts)
        missing = len(reference.documents) - len(restored)
        exact = False
        if not missing:
            rebuilt = ReviewContext.model_validate({**reference.model_dump(exclude={"key", "term", "documents"}), "documents": restored})
            exact = rebuilt == original
        observed = {"same_memo_key": caller.memo_key(10) == original.memo_key(10), "exact_input_reference_equal": ReviewInputReference.from_context(caller, term=10) == reference, "original_texts_recovered_by_raw_SHA": len(restored), "original_context_reconstructed_exactly": exact}
        if missing:
            observed["missing_original_texts"] = missing
        collisions[name] = observed
    if any(Path(str(database) + suffix).exists() and Path(str(database) + suffix).stat().st_size > 0 for suffix in ("-wal", "-journal")):
        raise ValueError("qualification requires a checkpointed database without nonempty WAL/journal")
    before = sha256(database)
    if before != "b9f86131e34d8a7e9c983d20d690bd2bd062836b0c86a50f69a1a978f27fc485":
        raise ValueError("qualification requires exact pinned b9 database")
    population = {"contexts": 0, "ordered_documents": 0, "known_text_v2_documents": 0, "text_utf8_bytes": 0, "text_characters": 0, "canonical_text_utf8_bytes": 0, "normalization_net_bytes_lost": 0, "noncanonical_whitespace_runs": 0, "decorated_numeric_lines": 0, "decorated_numeric_line_utf8_bytes": 0, "already_canonical_original_documents": 0, "all_originals_canonical_contexts": 0, "retained_computation_parents": 0, "natural_eligible_compaction": 0}
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro&immutable=1", uri=True) as connection:
        for (raw,) in connection.execute("SELECT record_json FROM analysis_memo"):
            value = json.loads(raw)
            if not isinstance(value, dict) or not isinstance(value.get("review"), dict) or not isinstance(value.get("context"), dict):
                continue
            population["contexts"] += 1
            all_canonical = True
            for document in value["context"]["documents"]:
                text = document["text"]
                normalized = re.sub(r"\s+", " ", DECORATED_PAGE.sub("", text)).strip()
                if text_digest(text) != "text-v2:" + hashlib.sha256(normalized.encode()).hexdigest():
                    raise ValueError("canonical recipe differs from pinned normalization function")
                canonical = normalized == text
                all_canonical = all_canonical and canonical
                pages = tuple(DECORATED_PAGE.finditer(text))
                population["ordered_documents"] += 1
                population["known_text_v2_documents"] += int(document["normalized_sha256"] == text_digest(text))
                population["text_utf8_bytes"] += len(text.encode())
                population["text_characters"] += len(text)
                population["canonical_text_utf8_bytes"] += len(normalized.encode())
                population["normalization_net_bytes_lost"] += len(text.encode()) - len(normalized.encode())
                population["noncanonical_whitespace_runs"] += sum(match.group() != " " for match in re.finditer(r"\s+", text))
                population["decorated_numeric_lines"] += len(pages)
                population["decorated_numeric_line_utf8_bytes"] += sum(len(match.group().encode()) for match in pages)
                population["already_canonical_original_documents"] += int(canonical)
            population["all_originals_canonical_contexts"] += int(all_canonical)
            parent = connection.execute("SELECT result_json FROM computation_results WHERE result_id=?", (value.get("computation_id"),)).fetchone()
            population["retained_computation_parents"] += int(parent is not None)
            population["natural_eligible_compaction"] += int(all_canonical and parent is not None)
    connection.close()
    if sha256(database) != before:
        raise ValueError("source database changed")
    return {"source_db_sha256": before, "source_DB_unchanged": True, "model_inventory": {"ReviewDocument_fields": len(ReviewDocument.model_fields), "ReviewSourceReference_fields": len(ReviewSourceReference.model_fields), "missing_document_fields": sorted(set(ReviewDocument.model_fields) - set(ReviewSourceReference.model_fields)), "missing_ReviewContext_fields": sorted(set(ReviewContext.model_fields) - set(ReviewInputReference.model_fields))}, "coarse_collision_controls": collisions, "pinned_legacy_review_population": population}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    arguments = parser.parse_args()
    observed = measure(arguments.source_root, arguments.database)
    saved = json.loads((Path(__file__).resolve().parent / "evidence.json").read_text())
    if any(saved[key] != value for key, value in observed.items()):
        raise ValueError("qualification metadata differs from recorded evidence")
    print(json.dumps({"relevant_evidence_leaves_exact": True, "measured": observed}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
```
