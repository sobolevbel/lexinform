"""Run declared offline corpus checks without changing source files or replacing results."""

import argparse
import hashlib
import json
import socket
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal
from unittest.mock import patch

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from tests.scenario.test_delivery_recovery import (
    test_confirm_an_accepted_delivery_does_not_send_again,
    test_dismiss_closes_without_claiming_delivery_or_retrying,
    test_retry_uses_saved_snapshot_and_parent_once_after_restart,
)

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.adapters.sejm_api import parse_print
from lexinform.models import RclProject
from lexinform.sections import document_kind
from tools.corpus_external import run_external
from tools.probe_law_comparison import decide
from tools.probe_law_mutations import mutations

Profile = Literal["extraction", "selection", "comparison", "delivery"]
PROFILES: tuple[Profile, ...] = ("extraction", "selection", "comparison", "delivery")
DELIVERY: dict[str, Callable[[], None]] = {
    "confirm": test_confirm_an_accepted_delivery_does_not_send_again,
    "dismiss": test_dismiss_closes_without_claiming_delivery_or_retrying,
    "retry_snapshot": test_retry_uses_saved_snapshot_and_parent_once_after_restart,
}
TOOL_VERSION = 1


class CorpusInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    coverage: Literal["raw", "cached", "excerpt", "synthetic"]
    official_id: str = Field(min_length=1)
    relations: tuple[str, ...] = ()


class CorpusCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    profile: Profile
    kind: Literal["extract", "print", "rcl", "compare", "delivery"]
    initiative: str = Field(min_length=1)
    split: Literal["tuning", "holdout", "regression"]
    inputs: tuple[CorpusInput, ...] = Field(min_length=1)
    expected: dict[str, JsonValue] = Field(min_length=1)
    basis: str = Field(min_length=1)
    oracle: Literal["human_review", "source_metadata", "synthetic_invariant", "machine_baseline"]


class CorpusManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1]
    scope: str = Field(min_length=1)
    exclusions: tuple[str, ...]
    cases: tuple[CorpusCase, ...] = Field(min_length=1)


def source_file(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or Path(relative).is_absolute():
        raise ValueError(f"Input must stay inside corpus: {relative}")
    return path


def load_manifest(root: Path) -> CorpusManifest:
    manifest = CorpusManifest.model_validate_json((root / "manifest.json").read_bytes())
    groups: dict[str, str] = {}
    identities: set[str] = set()
    for case in manifest.cases:
        if case.id in identities:
            raise ValueError(f"Duplicate case ID: {case.id}")
        identities.add(case.id)
        related = {case.initiative}
        for source in case.inputs:
            related.add(source.official_id)
            related.update(source.relations)
        for identity in related:
            previous = groups.setdefault(identity, case.split)
            if previous != case.split:
                raise ValueError(f"Initiative crosses dataset splits: {identity}")
        if case.split == "holdout" and case.oracle == "machine_baseline":
            raise ValueError(f"Machine labels cannot be an independent holdout: {case.id}")
    return manifest


def _blocked_network(*args: object, **kwargs: object) -> None:
    raise RuntimeError("Corpus checks prohibit network access")


def evaluate(case: CorpusCase, inputs: list[bytes]) -> dict[str, Any]:
    if case.kind == "extract" and case.profile == "extraction":
        extractor = DocumentTextExtractor(
            PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor()
        )
        extracted = extractor.extract_document(inputs[0])
        needles = case.expected.get("contains_text", [])
        if not isinstance(needles, list) or any(not isinstance(item, str) for item in needles):
            raise ValueError("contains_text must be an array of strings")
        return {
            "kind": document_kind(extracted.text),
            "contains_text": [
                item for item in needles if isinstance(item, str) and item in extracted.text
            ],
            "chars": len(extracted.text),
            "text_sha256": hashlib.sha256(extracted.text.encode()).hexdigest(),
            "selection_state": extracted.selection.state,
        }
    if case.kind == "print" and case.profile == "selection":
        raw = json.loads(inputs[0])
        info = parse_print(raw, term=raw["term"])
        return {"main_pdf": info.main_pdf.name if info.main_pdf else None}
    if case.kind == "rcl" and case.profile == "selection":
        project = RclProject.model_validate_json(inputs[0])
        return {role: document.id for role, document in project.text_documents().items()}
    if case.kind == "compare" and case.profile == "comparison":
        old, new = (data.decode() for data in inputs)
        probes = mutations(old)
        missed = [
            probe.kind for probe in probes if decide(old, probe.text).route in ("digest", "noise")
        ]
        return {
            "route": decide(old, new).route,
            "mutation_families": [probe.kind for probe in probes],
            "meaningful_mutations_missed": len(missed),
            "missed": missed,
        }
    if case.kind == "delivery" and case.profile == "delivery":
        scenario = json.loads(inputs[0])["scenario"]
        DELIVERY[scenario]()
        return {"invariant_passed": True, "scenario": scenario}
    raise ValueError(f"Unsupported kind/profile pair: {case.kind}/{case.profile}")


def check_case(root: Path, case: CorpusCase) -> dict[str, Any]:
    result: dict[str, Any] = {
        **case.model_dump(mode="json"),
        "independent_oracle": case.oracle != "machine_baseline",
        "effective_split": "regression" if case.split == "holdout" else case.split,
    }
    try:
        inputs = []
        for source in case.inputs:
            data = source_file(root, source.path).read_bytes()
            if hashlib.sha256(data).hexdigest() != source.sha256:
                raise ValueError(f"Input SHA mismatch: {source.path}")
            inputs.append(data)
        with (
            patch.object(socket.socket, "connect", _blocked_network),
            patch.object(socket.socket, "connect_ex", _blocked_network),
            patch.object(socket, "create_connection", _blocked_network),
        ):
            actual = evaluate(case, inputs)
        result["actual"] = actual
        mismatches = [
            key for key, value in case.expected.items() if key not in actual or actual[key] != value
        ]
        result["mismatches"] = mismatches
        result["status"] = "failed" if mismatches else "passed"
    except FileNotFoundError as exc:
        result.update(status="not_run", reason=str(exc))
    except Exception as exc:
        result.update(status="failed", reason=f"{type(exc).__name__}: {exc}")
    return result


def code_version() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def source_fingerprints() -> dict[str, str]:
    root = Path(__file__).resolve().parents[1]
    paths = [root / "uv.lock"]
    for directory in ("src", "tests", "tools"):
        paths.extend(sorted((root / directory).rglob("*.py")))
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths
    }


def code_dirty() -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode != 0 or bool(result.stdout.strip())


def run(corpus: Path, output: Path, profiles: tuple[Profile, ...] = PROFILES) -> int:
    root, target = corpus.resolve(), output.resolve()
    if target.is_relative_to(root) or target.exists():
        raise ValueError("Output must be a new file outside the corpus")
    if not profiles or len(set(profiles)) != len(profiles):
        raise ValueError("Choose at least one profile, without duplicates")
    result: dict[str, Any] = {
        "tool_version": TOOL_VERSION,
        "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "code_sha": code_version(),
        "code_dirty": code_dirty(),
        "source_sha256": source_fingerprints(),
        "corpus": str(root),
        "output": str(target),
        "requested_profiles": list(profiles),
        "network": "prohibited",
        "models": "fake delivery providers only; no paid calls",
    }
    try:
        manifest = load_manifest(root)
    except FileNotFoundError as exc:
        result.update(status="not_run", reason=str(exc))
    except ValueError as exc:
        result.update(status="failed", reason=f"{type(exc).__name__}: {exc}")
    else:
        result["manifest_sha256"] = hashlib.sha256(
            (root / "manifest.json").read_bytes()
        ).hexdigest()
        result["scope"] = manifest.scope
        result["exclusions"] = list(manifest.exclusions)
        results = [check_case(root, case) for case in manifest.cases if case.profile in profiles]
        result["cases"] = results
        result["profiles"] = {
            profile: (
                "not_run"
                if not (selected := [row for row in results if row["profile"] == profile])
                else "failed"
                if any(row["status"] == "failed" for row in selected)
                else "not_run"
                if any(row["status"] == "not_run" for row in selected)
                else "passed"
            )
            for profile in profiles
        }
        statuses = list(result["profiles"].values())
        result["status"] = (
            "failed" if "failed" in statuses else "not_run" if "not_run" in statuses else "passed"
        )
    with target.open("x") as stream:
        json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
    return 0 if result["status"] == "passed" else 1 if result["status"] == "failed" else 2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profiles", default=",".join(PROFILES))
    parser.add_argument("--suite", choices=("manifest", "external"), default="manifest")
    parser.add_argument("--mode", choices=("baseline", "compare"))
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--reference-sha256")
    args = parser.parse_args()
    requested = args.profiles.split(",")
    if any(profile not in PROFILES for profile in requested):
        parser.error("Profiles: " + ",".join(PROFILES))
    profiles = tuple(profile for profile in PROFILES if profile in requested)
    if len(profiles) != len(requested):
        parser.error("Profiles must be nonempty and unique")
    if args.suite == "external" and args.mode is None:
        parser.error("External suite needs explicit --mode baseline or --mode compare")
    if args.suite == "manifest" and any((args.mode, args.reference, args.reference_sha256)):
        parser.error("Mode and reference options apply only to the external suite")
    try:
        code = (
            run_external(
                args.corpus,
                args.output,
                profiles,
                mode=args.mode,
                reference=args.reference,
                reference_sha256=args.reference_sha256,
                context={
                    "code_sha": code_version(),
                    "code_dirty": code_dirty(),
                    "source_sha256": source_fingerprints(),
                },
            )
            if args.suite == "external"
            else run(args.corpus, args.output, profiles)
        )
    except ValueError as exc:
        parser.error(str(exc))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
