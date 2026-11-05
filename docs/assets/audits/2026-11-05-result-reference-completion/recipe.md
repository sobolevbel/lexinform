# DA6.3 metadata verification recipe

Save this code as recipe.py beside audit.json and source-pins.json, then run it with --root <checkout>. It validates metadata and existing test declarations; the full-gate execution proof is separate.

```python
"""Verify pinned audit inputs, named test symbols and historical storage-range extraction."""

import argparse
import ast
import hashlib
import json
from pathlib import Path


def verify(root: Path) -> dict[str, object]:
    packet = Path(__file__).resolve().parent
    pins = json.loads((packet / "source-pins.json").read_text())
    for relative, expected in pins.items():
        path = packet / relative if relative == "recipe.py" else root / relative
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("audit source pin mismatch: " + relative)
    audit = json.loads((packet / "audit.json").read_text())
    if len({row["purpose"] for row in audit["all10purpose_rows"]}) != 10:
        raise ValueError("audit must name all ten distinct purposes")
    checked = set()
    for row in audit["all10purpose_rows"]:
        for target in [row["collection_test"], row["projection_test"], *row["ownership_tests"]]:
            path, name = target.split("::")
            tree = ast.parse((root / path).read_text())
            if name not in {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}:
                raise ValueError("missing named test: " + target)
            checked.add(target)
    for measured in audit["historical_storage_measurements"]:
        source = json.loads((root / measured["source"]).read_text())
        key = "secondary_profile_diff" if measured["stage"] == "J066" else "review_flow_diff"
        fields = ("sql_bytes_delta", "gzip_bytes_delta", "sqlite_bytes_delta") if measured["stage"] == "J066" else ("sql_bytes", "gzip_bytes", "sqlite_bytes")
        cases = source[key]["cases"]
        ranges = {phase: {label: [min(row[phase][field] for row in cases.values()), max(row[phase][field] for row in cases.values())] for label, field in zip(("SQL_delta_bytes", "gzip_delta_bytes", "SQLite_delta_bytes"), fields, strict=True)} for phase in ("collected", "cleaned")}
        if len(cases) != measured["case_count"] or ranges != measured["historical_fixture_delta_ranges"]:
            raise ValueError("historical storage metadata differs")
    return {"source_pins_exact": True, "historical_range_extraction_exact": True, "purpose_count": 10, "named_existing_test_symbols": len(checked), "tests_executed": False, "semantic_conclusion": "manual source/test audit pending root acceptance review", "checkbox_changed": False}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(verify(arguments.root), indent=2))


if __name__ == "__main__":
    main()
```
