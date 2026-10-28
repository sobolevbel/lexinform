"""Run a reproducible, bounded sample after mutmut has generated delivery and batch mutants."""

import argparse
import hashlib
import json
import os
import signal
import subprocess
import time
from pathlib import Path

FUNCTIONS = {
    "src/lexinform/services/message_parts.py": {
        "x_freeze_parts": 45,
        "x_send_continuations": 40,
    },
    "src/lexinform/services/publications.py": {
        "x_send_publications": 45,
        "x_complete_delivery": 40,
    },
    "src/lexinform/services/analysis.py": {
        "xǁAnalysisServiceǁsubmit_queued_batches": 50,
        "xǁAnalysisServiceǁ_consume": 30,
    },
}


def source_hashes() -> dict[str, str]:
    return {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in FUNCTIONS}


def selection() -> tuple[list[str], dict[str, int]]:
    selected: list[str] = []
    generated: dict[str, int] = {}
    for path, functions in FUNCTIONS.items():
        meta = json.loads(Path(f"mutants/{path}.meta").read_text())
        for function, limit in functions.items():
            keys = sorted(
                (
                    key
                    for key in meta["exit_code_by_key"]
                    if key.rpartition(".")[2].split("__mutmut_")[0] == function
                ),
                key=lambda key: int(key.rsplit("_", 1)[1]),
            )
            if len(keys) < limit:
                raise ValueError(f"Expected at least {limit} mutations for {function}")
            generated[function] = len(keys)
            selected.extend(keys[index * (len(keys) - 1) // (limit - 1)] for index in range(limit))
    return selected, generated


def run(output: Path, functions: list[str]) -> None:
    selected, generated = selection()
    if len(selected) != 250 or len(set(selected)) != 250:
        raise ValueError("The campaign requires exactly 250 distinct mutations")
    if functions:
        if not set(functions) <= generated.keys():
            raise ValueError("Refresh functions must belong to the predeclared campaign")
        selected = [
            key for key in selected if key.rpartition(".")[2].split("__mutmut_")[0] in functions
        ]
    output.mkdir(parents=True, exist_ok=False)
    before = source_hashes()
    command = [".venv/bin/mutmut", "run", "--max-children", "2", *selected]
    environment = dict(os.environ, LEXINFORM_MUTATION_PROBE_DIR=str(output.resolve() / "reports"))
    started = time.monotonic()
    exhausted = False
    with (output / "run.log").open("w") as stream:
        process = subprocess.Popen(
            command,
            env=environment,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            code = process.wait(timeout=1200)
        except subprocess.TimeoutExpired:
            exhausted = True
            os.killpg(process.pid, signal.SIGINT)
            try:
                code = process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                code = process.wait(timeout=10)
    after = source_hashes()
    outcomes: dict[str, int | None] = {}
    for path in FUNCTIONS:
        meta = json.loads(Path(f"mutants/{path}.meta").read_text())
        outcomes.update(
            {key: value for key, value in meta["exit_code_by_key"].items() if key in selected}
        )
    result = {
        "command": command,
        "selection_method": "Evenly spaced numeric IDs within six predeclared function quotas.",
        "generated_per_function": generated,
        "selected": selected,
        "seconds": time.monotonic() - started,
        "exit_code": code,
        "budget_seconds": 1200,
        "budget_exhausted": exhausted,
        "source_sha256_before": before,
        "source_sha256_after": after,
        "outcomes": outcomes,
    }
    (output / "run.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                key: value
                for key, value in result.items()
                if key not in {"command", "selected", "outcomes"}
            },
            indent=2,
        )
    )
    if before != after or exhausted or code != 0:
        raise SystemExit("Mutation campaign did not finish within its declared contract")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--function", action="append", default=[])
    arguments = parser.parse_args()
    run(arguments.output, arguments.function)
