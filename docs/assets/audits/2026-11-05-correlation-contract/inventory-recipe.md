# Reproduce the finite boundary inventory

Use an isolated checkout at commit `7b1963122dd33e49b25a1718b68bc388ae344fce` as the base,
and this stage's pinned candidate checkout. Copy the Python block into a temporary script and
run it with three arguments: base directory, candidate directory and output JSON path. It reads
Python source only and does not execute SQL or open a state database. The assertions verify the
precise logging-only normalization and every existing SQL call before emitting the inventory.

```python
import argparse
import ast
import json
import re
from pathlib import Path

parser = argparse.ArgumentParser(
    description="Compare frozen repository correlation boundaries without executing SQL."
)
parser.add_argument("base", type=Path)
parser.add_argument("candidate", type=Path)
parser.add_argument("output", type=Path)
args = parser.parse_args()
BASE = args.base
ROOT = args.candidate
path = Path("src/lexinform/adapters/sqlite_repo.py")
a = ast.parse((BASE / path).read_text())
b = ast.parse((ROOT / path).read_text())


class StripDebug(ast.NodeTransformer):
    def __init__(self):
        self.method = None
        self.cursor_captures = []
        self.log_guards = []

    def visit_FunctionDef(self, node):
        before = self.method
        self.method = node.name
        result = self.generic_visit(node)
        self.method = before
        return result

    def visit_Assign(self, node):
        if (
            self.method == "save_batch_intent"
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "inserted"
        ):
            expected = (
                ast.parse('self._conn.execute("INSERT OR IGNORE INTO llm_batch_intents")')
                .body[0]
                .value.func
            )
            assert isinstance(node.value, ast.Call) and ast.dump(node.value.func) == ast.dump(
                expected
            )
            assert isinstance(node.value.args[0], ast.Constant) and node.value.args[
                0
            ].value.startswith("INSERT OR IGNORE INTO llm_batch_intents")
            self.cursor_captures.append(self.method)
            return ast.Expr(value=node.value)
        return self.generic_visit(node)

    def visit_If(self, node):
        expected = {
            "save_batch_intent": "inserted.rowcount == 1",
            "mark_publication": "original is not None",
        }.get(self.method)
        if (
            expected
            and ast.dump(node.test) == ast.dump(ast.parse(expected, mode="eval").body)
            and len(node.body) == 1
            and not node.orelse
        ):
            expr = node.body[0]
            assert isinstance(expr, ast.Expr) and self.is_debug(expr.value)
            self.log_guards.append(self.method)
            return None
        return self.generic_visit(node)

    def is_debug(self, call):
        return (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "log"
            and call.func.attr == "debug"
        )

    def visit_Expr(self, node):
        return None if self.is_debug(node.value) else self.generic_visit(node)


old = next(n for n in a.body if isinstance(n, ast.ClassDef) and n.name == "SqliteBillRepository")
new = next(n for n in b.body if isinstance(n, ast.ClassDef) and n.name == "SqliteBillRepository")
left = StripDebug()
right = StripDebug()
assert ast.dump(left.visit(old)) == ast.dump(right.visit(new))
assert left.cursor_captures == left.log_guards == []
assert right.cursor_captures == ["save_batch_intent"]
assert right.log_guards == ["save_batch_intent", "mark_publication"]


def sqlcalls(tree):
    return [
        ast.dump(n)
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr in ("execute", "executemany", "executescript")
    ]


assert sqlcalls(a) == sqlcalls(b)
b = ast.parse((ROOT / path).read_text())
identity = {
    "save_artifact_metadata": "artifact_sha256",
    "save_artifact_read": "artifact_sha256,artifact_read_id",
    "save_extraction_result": "extraction_id,artifact_sha256",
    "save_selection_extraction": "selection_id,extraction_id,artifact_sha256",
    "save_selection_artifact": "selection_id,artifact_sha256",
    "save_source_observation": "observation_id,selection_id,bill key",
    "save_applicability_observation": "observation_id,computation_id,bill key",
    "save_computation_result": "computation_id,batch_id,custom_id,request_sha256,bill key",
    "save_result_application": "computation_id,bill key,generation,phase,target kind/ref (actual composite identity)",
    "save_content_version": "content_id,root publication_id",
    "bind_publication_content": "content_id,publication_id,bill key",
    "save_batch_intent": "newly inserted only: custom_id,bill key,actual frozen request SHA; supplied receipt artifact/extraction/selection parents",
    "_save_llm_batch_rows": "batch_id,custom_id,bill key,saved request SHA; supplied receipt artifact/extraction/selection parents",
    "create_publication": "actual retained publication_id,updated delivery_status; supplied/inherited bill key masked on possible upsert collision",
    "mark_publication": "publication_id,delivery_status,already-loaded actual bill key; missing physical row emits no event",
}
support = {
    "save_selection_edition",
    "save_candidate_disposition_trace",
    "save_content_snapshot",
    "save_source_catalog_snapshot",
    "save_source_catalog_attempt",
    "save_source_metadata_omission",
    "save_physical_inventory",
    "save_senate_inspection",
    "save_rcl_letter_use",
}
maintenance = {
    "restore",
    "apply_legacy_maintenance",
    "import_legacy_results",
    "prune_runs",
    "prune_history",
    "delete_publication",
}
rows = []
dynamic = []
for node in ast.walk(b):
    if not isinstance(node, ast.FunctionDef):
        continue
    writes = []
    delegates = []
    for call in ast.walk(node):
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
            continue
        if isinstance(call.func.value, ast.Name) and call.func.value.id == "self":
            delegates.append(call.func.attr)
        if call.func.attr not in ("execute", "executemany", "executescript") or not call.args:
            continue
        arg = call.args[0]
        if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
            dynamic.append(
                {"method": node.name, "line": call.lineno, "expression": ast.unparse(arg)}
            )
            continue
        sql = arg.value.strip()
        match = re.match(
            r"(INSERT(?: OR \w+)? INTO|UPDATE|DELETE FROM|REPLACE INTO|CREATE TABLE|DROP TABLE|ALTER TABLE)\s+([\w]+)",
            sql,
            re.I,
        )
        if match:
            writes.append({"line": call.lineno, "operation": match[1].upper(), "table": match[2]})
    if not writes:
        continue
    events = []
    for call in ast.walk(node):
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "log"
            and call.func.attr == "debug"
        ):
            events.append(ast.unparse(call))
    category = (
        "native required-identity boundary"
        if node.name in identity
        else "supporting metadata/edge"
        if node.name in support
        else "maintenance/rehydration"
        if node.name in maintenance
        else "operational projection/work/cache/control"
    )
    rows.append(
        {
            "method": node.name,
            "line": node.lineno,
            "category": category,
            "writes": writes,
            "structured_fields": identity.get(node.name),
            "debug_events": events,
            "delegates": sorted(set(delegates) & set(identity)),
        }
    )
extra_write_methods = {
    "migrate": ("schema/version", "maintenance/rehydration"),
    "restore": (
        "restored schema/typed rows and legacy bill projections",
        "maintenance/rehydration",
    ),
    "save_source_catalog_snapshot": ("source_catalog_snapshots", "supporting metadata/edge"),
    "save_document_review": (
        "bills review projection",
        "operational projection/work/cache/control",
    ),
    "discontinue_unfinished": ("bills", "operational projection/work/cache/control"),
}
for name, (table, category) in extra_write_methods.items():
    if any(row["method"] == name for row in rows):
        continue
    node = next(n for n in ast.walk(b) if isinstance(n, ast.FunctionDef) and n.name == name)
    rows.append(
        {
            "method": name,
            "line": node.lineno,
            "category": category,
            "writes": [{"operation": "reviewed dynamic SQL", "table": table}],
            "structured_fields": None,
            "debug_events": [],
            "delegates": [],
        }
    )
wrappers = {
    "save_physical_metadata": "save_physical_inventory",
    "save_physical_capture": "save_physical_inventory",
    "save_llm_batch": "_save_llm_batch_rows",
}
for row in dynamic:
    method = row["method"]
    expression = row["expression"]
    row["reviewed_effect"] = (
        "schema migration/restore"
        if method in ("migrate", "restore")
        else "finite existing projection compaction"
        if method == "apply_legacy_maintenance"
        else "known catalog/omission metadata insert"
        if method
        in (
            "save_source_catalog_snapshot",
            "save_source_catalog_attempt",
            "save_source_metadata_omission",
        )
        else "existing bill projection/update"
        if method in ("save_document_review", "move_government_rows", "discontinue_unfinished")
        else "read-only query/PRAGMA; callsites inspected"
    )
# Dynamic calls are reviewed explicitly rather than omitted from the boundary inventory.
report = {
    "base": str(BASE),
    "all_repository_methods_identical_after_explicit_log_normalization": True,
    "normalized_cursor_capture": right.cursor_captures,
    "normalized_log_only_guards": right.log_guards,
    "all_sql_call_asts_exact": True,
    "sql_calls": len(sqlcalls(b)),
    "direct_sql_write_methods": len(rows),
    "boundaries": rows,
    "dynamic_sql_sites": dynamic,
    "delegated_write_wrappers": wrappers,
    "not_a_sql_journal": True,
    "unsupported_or_unknown": {
        "prepared_input_id": "No new persisted PreparedInput identity; exact request SHA is the actual frozen input identity.",
        "legacy": "Missing actual hashes/receipts/parents remain absent.",
        "application_row_id": "Existing typed API carries composite identity; numeric row IDs are not fetched/invented for logs.",
        "supporting_record_ids": "Catalog/omission/physical/edition/snapshot-specific IDs remain in their typed SQL rows; required core endpoint IDs are traced at native creation/binding boundaries.",
        "commit": "Local validation/write traces do not attest commit/checkpoint/provider or remote delivery.",
        "history": "No retroactive traces are generated during restore/legacy import/cleanup.",
    },
}
args.output.write_text(json.dumps(report, indent=2) + "\n")
print(
    "literal write methods", len(rows), "dynamic sites", len(dynamic), "SQL calls", len(sqlcalls(b))
)
```
