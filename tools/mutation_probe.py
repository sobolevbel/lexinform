"""Record per-test outcomes for explicitly enabled mutation and test-ablation audits."""

import ast
import hashlib
import importlib
import inspect
import json
import os
from pathlib import Path
from textwrap import dedent

import pytest

import lexinform


def pytest_sessionstart(session: pytest.Session) -> None:
    fault = os.environ.get("LEXINFORM_MUTATION_FAULT")
    if fault is None:
        return
    batch = fault.startswith("batch_")
    module = importlib.import_module(
        "lexinform.services.analysis" if batch else "lexinform.services.publications"
    )
    owner = module.AnalysisService if batch else module
    name = "submit_queued_batches" if batch else "send_publications"
    tree = ast.parse(dedent(inspect.getsource(getattr(owner, name))))
    function = tree.body[0]
    if not isinstance(function, ast.FunctionDef):
        raise ValueError("Boundary mutation requires a function")
    if fault == "delivery_pending_checkpoint_after_send":
        before = next(node for node in function.body if isinstance(node, ast.If))
        function.body.remove(before)
        attempt = next(node for node in function.body if isinstance(node, ast.Try))
        attempt.body.insert(1, before)
    elif fault == "delivery_receipt_checkpoint_removed":
        after = function.body[-2]
        if not isinstance(after, ast.If):
            raise ValueError("Expected the final receipt checkpoint")
        function.body.remove(after)
    elif fault in {"batch_checkpoint_after_submit", "batch_submitting_after_submit"}:
        group = next(
            node
            for node in ast.walk(function)
            if isinstance(node, ast.For) and any(isinstance(child, ast.Try) for child in node.body)
        )
        attempt = next(node for node in group.body if isinstance(node, ast.Try))
        batch_boundary = next(
            node
            for node in group.body
            if isinstance(node, ast.If if fault == "batch_checkpoint_after_submit" else ast.With)
        )
        group.body.remove(batch_boundary)
        attempt.body.insert(1, batch_boundary)
    else:
        raise ValueError(f"Unknown boundary mutation: {fault}")
    namespace = dict(vars(module))
    exec(compile(ast.fix_missing_locations(tree), f"<mutation:{fault}>", "exec"), namespace)
    setattr(owner, name, namespace[name])


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    directory = os.environ.get("LEXINFORM_MUTATION_PROBE_DIR")
    if directory is None:
        return
    mutant = os.environ.get("MUTANT_UNDER_TEST", "")
    identity = hashlib.sha256(mutant.encode()).hexdigest()[:16]
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    record = {
        "mutant": mutant,
        "package_file": lexinform.__file__,
        "nodeid": report.nodeid,
        "phase": report.when,
        "outcome": report.outcome,
        "seconds": report.duration,
        "failure": report.longreprtext[-6000:] if report.failed else None,
    }
    with (target / f"{identity}-{os.getpid()}.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
