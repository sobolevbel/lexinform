"""Orchestrate frozen external replays as observations or SHA-pinned regression comparisons."""

import builtins
import datetime as dt
import hashlib
import io
import json
import os
import re
import socket
import sys
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from typing import IO, Any, Literal
from unittest.mock import patch

from tests.scenario.test_delivery_recovery import (
    test_confirm_an_accepted_delivery_does_not_send_again,
    test_dismiss_closes_without_claiming_delivery_or_retrying,
    test_retry_uses_saved_snapshot_and_parent_once_after_restart,
)

from lexinform.models import Bill, BillStatus, RclProject, process_summary
from lexinform.services.documents import TextLoader
from lexinform.services.sources import RclTextSource
from tools.probe_law_comparison import run as compare_laws
from tools.probe_law_mutations import run as mutate_laws
from tools.replay_document_candidates import CachedExtractor, document_id
from tools.replay_document_selection import replay as replay_documents

Mode = Literal["baseline", "compare"]
EXCLUSIONS = (
    "Machine observations are regression baselines, never independent legal truth or a holdout.",
    "Raw replay covers docx/docm/Word XML and nested ZIP samples; standalone doc/PDF excluded.",
    "RCL candidates use cached extracted text; absent cached text is reported, not reconstructed.",
    "Recorded transition kind rpw is excluded; rpw-without-rule transitions are retained.",
    "Senate, process-evidence and labelled policy replays need separate inventories.",
    "Delivery runs the fixed offline fake-provider slice, not the production Telegram history.",
)


class CorpusReads:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.paths: set[str] = set()
        self.active: set[str] = set()
        self.missing: set[str] = set()
        self.initial_sha256: dict[str, str | None] = {}
        self.allowed = (
            Path(__file__).resolve().parents[1],
            Path(sys.prefix).resolve(),
            Path(sys.base_prefix).resolve(),
        )
        self._open = builtins.open
        self._io_open = io.open

    def require(self, path: Path) -> None:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError(f"Corpus input escapes root: {path}")
        relative = str(resolved.relative_to(self.root))
        self.paths.add(relative)
        self.active.add(relative)
        if relative not in self.initial_sha256:
            digest = None
            if resolved.is_file():
                with self._open(resolved, "rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
            self.initial_sha256[relative] = digest
        if not resolved.is_file():
            self.missing.add(relative)

    def _checked(self, file: Any, mode: str) -> None:
        if any(flag in mode for flag in "wax+"):
            raise PermissionError("External replay inputs are read-only; writes are prohibited")
        if isinstance(file, int):
            return
        lexical = Path(os.fsdecode(file)).absolute()
        path = lexical.resolve()
        if lexical.is_relative_to(self.root) and not path.is_relative_to(self.root):
            raise PermissionError(f"Corpus symlink escapes root: {lexical}")
        if path.name == ".env":
            raise PermissionError("External replay must not read .env")
        if path.is_relative_to(self.root):
            self.require(path)
        elif not any(path.is_relative_to(root) for root in self.allowed):
            raise PermissionError(f"External replay cannot read outside its corpus: {path}")

    def open(self, file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> IO[Any]:
        self._checked(file, mode)
        return self._open(file, mode, *args, **kwargs)

    def io_open(self, file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> IO[Any]:
        self._checked(file, mode)
        return self._io_open(file, mode, *args, **kwargs)

    def guard(self) -> ExitStack:
        stack = ExitStack()
        stack.enter_context(patch.object(builtins, "open", self.open))
        stack.enter_context(patch.object(io, "open", self.io_open))
        for owner, name in (
            (socket.socket, "connect"),
            (socket.socket, "connect_ex"),
            (socket.socket, "sendto"),
            (socket, "create_connection"),
            (socket, "getaddrinfo"),
        ):
            stack.enter_context(patch.object(owner, name, blocked_network))
        return stack


def blocked_network(*args: Any, **kwargs: Any) -> None:
    raise RuntimeError("External corpus replay prohibits network/model access")


class RequiredCachedExtractor(CachedExtractor):
    def __init__(self, corpus: Path, reads: CorpusReads) -> None:
        super().__init__(corpus)
        self.reads = reads

    def extract(self, data: bytes) -> str:
        self.reads.require(self.corpus / f"documents/text/{data.decode()}.txt.gz")
        return super().extract(data)


def replay_candidates(root: Path, reads: CorpusReads) -> dict[str, Any]:
    source = RclTextSource()
    loader = TextLoader(
        {"legislacja.rcl.gov.pl": document_id},
        RequiredCachedExtractor(root, reads),
        max_bytes=32_000_000,
        cache_size=256,
    )
    now = dt.datetime(2026, 10, 17, tzinfo=dt.UTC)
    results: dict[str, Any] = {}
    for path in sorted((root / "corpus").glob("1*.json")):
        project = RclProject.model_validate_json(path.read_bytes())
        for stage in project.reached_stages:
            snapshot = project.model_copy(
                update={"stages": tuple(st for st in project.stages if st.number <= stage.number)}
            )
            bill = Bill(
                summary=process_summary(snapshot, term=10),
                rcl=snapshot,
                status=BillStatus.ANALYSIS_PENDING,
                first_seen_at=now,
                last_checked_at=now,
            )
            located = source.locate(bill)
            key = f"{project.id}/{stage.number}"
            if located.document is None:
                results[key] = {"state": "no_candidate"}
                continue
            selected, file = loader.select_document(located.document)
            results[key] = {
                "state": "waiting"
                if file.text is None and file.selection.state == "unverified"
                else file.selection.state,
                "url": selected.url,
                "chars": len(file.text or ""),
                "reasons": file.selection.rejected,
                "extras": selected.extra_urls,
                "amendments": selected.amendment_urls,
                "normative": selected.normative_urls,
                "input_paths": [
                    str(path.relative_to(root)),
                    *sorted(
                        f"documents/text/{identity}.txt.gz"
                        for identity in set(
                            re.findall(r"dokument(\d+)", located.document.model_dump_json())
                        )
                        if f"documents/text/{identity}.txt.gz" in reads.paths
                    ),
                ],
            }
    return results


def provenance(root: Path, reads: CorpusReads, index: dict[str, Any]) -> dict[str, Any]:
    entities = index.get("entities", {})
    documents: dict[str, set[str]] = {}
    for key, entity in entities.items():
        related = {key}
        related.update(value for name in ("rcl", "druk", "rpw") if (value := entity.get(name)))
        for identity in entity.get("documents", []):
            documents.setdefault(str(identity), set()).update(related)
    result: dict[str, Any] = {}
    for relative in sorted(reads.paths):
        path = root / relative
        document = re.fullmatch(r"documents/(?:files|text)/(\d+)\..+", relative)
        project = re.fullmatch(r"corpus/(\d+)\.json", relative)
        printing = re.fullmatch(r"sejm/term(\d+)/(?:text|files|prints)/([^/.]+).+", relative)
        ids = (
            [f"RCL/document/{document[1]}"]
            if document
            else [f"RCL/{project[1]}"]
            if project
            else [f"Sejm/{printing[1]}/{printing[2].split('__')[0]}"]
            if printing
            else []
        )
        relations = sorted(documents.get(document[1], set())) if document else []
        entity_key = f"rcl:{project[1]}" if project else None
        if entity_key and entity_key in entities:
            entity = entities[entity_key]
            relations = [entity_key, *(str(entity[name]) for name in ("druk",) if entity.get(name))]
        final_sha = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        initial_sha = reads.initial_sha256[relative]
        result[relative] = {
            "sha256": initial_sha,
            "final_sha256": final_sha,
            "changed_during_replay": initial_sha != final_sha,
            "official_ids": ids,
            "relations": relations,
            "initiative": relations[0] if relations else ids[0] if ids else "corpus_metadata",
            "coverage": "raw" if "/files/" in relative else "cached",
            "availability": "available" if path.is_file() else "missing",
            "split": "regression",
        }
    return result


def flatten(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not payload or (operation == "mutations" and not payload.get("documents")):
        return {}
    if operation == "documents":
        return {
            f"{operation}/{section}/{key}": actual
            for section, rows in payload.items()
            for key, actual in rows.items()
        }
    if operation == "word":
        return {f"word/{key}": actual for key, actual in payload["packages"].items()}
    if operation == "comparison":
        return {
            f"comparison/{row['kind']}/{row['key']}/{row['druk']}": row for row in payload["pairs"]
        }
    if operation == "candidates":
        return {f"candidates/{key}": actual for key, actual in payload.items()}
    return {operation: payload}


def row_inputs(key: str, actual: Any, paths: list[str]) -> list[str]:
    if key.startswith("documents/projects/"):
        return [f"corpus/{key.split('/')[-2]}.json"]
    if key.startswith("candidates/"):
        return [
            str(path) for path in actual.get("input_paths", [f"corpus/{key.split('/')[-2]}.json"])
        ]
    if key.startswith("documents/prints/"):
        return [f"sejm/term{key.split('/')[-2]}/prints.json"]
    if key.startswith("documents/packages/") or key.startswith("word/"):
        return [f"documents/files/{key.split('/')[-1]}"]
    if key.startswith("comparison/"):
        return [
            "INDEX.json",
            "checks/out/92_transitions.json",
            actual["old_path"],
            actual["new_path"],
        ]
    return paths


def compare_rows(current: dict[str, Any], reference: dict[str, Any]) -> list[dict[str, Any]]:
    changes = []
    old_rows = reference.get("observations", {})
    for key in sorted(current.keys() | old_rows.keys()):
        before, after = old_rows.get(key), current.get(key)
        if before is None or after is None or before["actual"] != after["actual"]:
            old_auto, new_auto = automatic_match(before), automatic_match(after)
            changes.append(
                {
                    "id": key,
                    "before": before,
                    "after": after,
                    "new_automatic_match": new_auto and not old_auto,
                    "lost_automatic_match": old_auto and not new_auto,
                }
            )
    return changes


def automatic_match(row: dict[str, Any] | None) -> bool:
    if row is None or not isinstance(row.get("actual"), dict):
        return False
    actual = row["actual"]
    return actual.get("baseline", {}).get("route") in ("digest", "noise")


def row_ids(key: str, actual: Any) -> list[str]:
    if key.startswith("documents/projects/") or key.startswith("candidates/"):
        return [f"RCL/{key.split('/')[-2]}"]
    if key.startswith("documents/prints/"):
        return [f"Sejm/{key.split('/')[-2]}/{key.split('/')[-1]}"]
    if key.startswith("documents/packages/") or key.startswith("word/"):
        return [f"RCL/document/{key.split('/')[-1].split('.')[0]}"]
    if key.startswith("comparison/"):
        return [actual["key"], f"Sejm/10/{actual['druk']}"]
    return []


def run_external(
    corpus: Path,
    output: Path,
    profiles: tuple[str, ...],
    *,
    mode: Mode,
    reference: Path | None = None,
    reference_sha256: str | None = None,
    context: dict[str, Any] | None = None,
) -> int:
    root, target = corpus.resolve(), output.resolve()
    if target.is_relative_to(root) or target.exists():
        raise ValueError("Output must be a new file outside the corpus")
    if (
        not profiles
        or len(set(profiles)) != len(profiles)
        or any(
            profile not in ("extraction", "selection", "comparison", "delivery")
            for profile in profiles
        )
    ):
        raise ValueError("Choose known profiles, without duplicates or an empty population")
    previous = None
    if mode == "compare":
        if reference is None or reference_sha256 is None:
            raise ValueError("External compare needs --reference and --reference-sha256")
        raw = reference.read_bytes()
        if hashlib.sha256(raw).hexdigest() != reference_sha256:
            raise ValueError("Reference SHA mismatch")
        previous = json.loads(raw)
        if previous.get("suite") != "external" or previous.get("status") != "recorded":
            raise ValueError("Reference must be a complete explicitly recorded external baseline")
        if previous.get("requested_profiles") != list(profiles):
            raise ValueError("Reference profile population differs")
    elif reference is not None or reference_sha256 is not None:
        raise ValueError("References are accepted only in explicit compare mode")
    reads = CorpusReads(root)
    result: dict[str, Any] = {
        **(context or {}),
        "suite": "external",
        "mode": mode,
        "corpus": str(root),
        "output": str(target),
        "requested_profiles": list(profiles),
        "oracle": "machine_baseline",
        "independent_oracle": False,
        "exclusions": EXCLUSIONS,
        "observations": {},
        "operations": {},
        "profiles": {},
    }
    index: dict[str, Any] = {}
    try:
        with reads.guard():
            index = json.loads((root / "INDEX.json").read_bytes())
            inventory = json.loads((root / "documents/index.json").read_bytes())
            if "extraction" in profiles:
                for identity, record in inventory.items():
                    if record.get("extension") in ("docx", "docm", "xml"):
                        reads.require(root / f"documents/files/{identity}.{record['extension']}")
            operations: list[tuple[str, str, Callable[[], dict[str, Any]]]] = []
            if "selection" in profiles or "extraction" in profiles:
                operations.append(
                    (
                        "documents",
                        "selection" if "selection" in profiles else "extraction",
                        lambda: replay_documents(root),
                    )
                )
            if "extraction" in profiles:
                operations.append(
                    ("word", "extraction", lambda: replay_documents(root, all_word=True))
                )
            if "selection" in profiles:
                operations.append(
                    ("candidates", "selection", lambda: replay_candidates(root, reads))
                )
            if "comparison" in profiles:
                operations.extend(
                    [
                        ("comparison", "comparison", lambda: compare_laws(root)),
                        ("mutations", "comparison", lambda: mutate_laws(root)),
                    ]
                )
            if "delivery" in profiles:
                operations.append(("delivery", "delivery", delivery_observations))
            for name, profile, replay in operations:
                reads.active.clear()
                try:
                    payload = replay()
                    rows = json.loads(json.dumps(flatten(name, payload)))
                    paths = sorted(reads.active)
                    errors = [
                        key
                        for key, actual in rows.items()
                        if isinstance(actual, dict) and "error" in actual
                    ]
                    result["operations"][name] = {
                        "profile": profile,
                        "rows": len(rows),
                        "population": sum(
                            count.get("total", 0) for count in payload.get("counts", {}).values()
                        )
                        if name == "mutations"
                        else len(payload.get("packages", {}))
                        if name == "documents" and "extraction" in profiles
                        else len(rows),
                        "inputs": paths,
                        "error_observations": errors,
                    }
                    for key, actual in rows.items():
                        result["observations"][key] = {
                            "profile": (
                                "extraction"
                                if key.startswith("documents/packages/")
                                and "extraction" in profiles
                                else profile
                            ),
                            "actual": actual,
                            "inputs": row_inputs(key, actual, paths),
                            "official_ids": row_ids(key, actual),
                            "expected": None,
                            "basis": "Machine observation; acceptance needs reviewed comparison.",
                            "oracle": "machine_baseline",
                            "split": "regression",
                        }
                except Exception as exc:
                    result["operations"][name] = {
                        "profile": profile,
                        "rows": 0,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
    except FileNotFoundError as exc:
        result["missing_prerequisite"] = str(exc)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["inputs"] = provenance(root, reads, index)
    result["missing_inputs"] = sorted(reads.missing)
    result["changed_during_replay"] = [
        path for path, source in result["inputs"].items() if source["changed_during_replay"]
    ]
    for profile in profiles:
        profile_operations = [
            op for op in result["operations"].values() if op["profile"] == profile
        ]
        population = [
            key
            for key, row in result["observations"].items()
            if row["profile"] == profile
            and (profile != "extraction" or key.startswith(("word/", "documents/packages/")))
            and (profile != "comparison" or key.startswith("comparison/"))
        ]
        result["profiles"][profile] = (
            "failed"
            if any("error" in op or op.get("error_observations") for op in profile_operations)
            else "not_run"
            if not profile_operations
            or not population
            or any(not op.get("population") for op in profile_operations)
            else "recorded"
        )
    result["status"] = "recorded"
    if "error" in result or any(value == "failed" for value in result["profiles"].values()):
        result["status"] = "failed"
    elif reads.missing or any(value == "not_run" for value in result["profiles"].values()):
        result["status"] = "not_run"
    if result["changed_during_replay"]:
        result["status"] = "failed"
    if previous is not None:
        result["reference_sha256"] = reference_sha256
        result["reference_code_sha"] = previous.get("code_sha")
        result["changes"] = compare_rows(result["observations"], previous)
        result["new_automatic_matches"] = sum(
            row["new_automatic_match"] for row in result["changes"]
        )
        result["lost_automatic_matches"] = sum(
            row["lost_automatic_match"] for row in result["changes"]
        )
        result["changed_inputs"] = [
            path
            for path in sorted(result["inputs"].keys() | previous["inputs"].keys())
            if result["inputs"].get(path, {}).get("sha256")
            != previous["inputs"].get(path, {}).get("sha256")
        ]
        if result["status"] == "recorded":
            result["status"] = (
                "failed" if result["changes"] or result["changed_inputs"] else "passed"
            )
    with target.open("x") as stream:
        json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
    return (
        0
        if result["status"] in ("recorded", "passed")
        else 2
        if result["status"] == "not_run"
        else 1
    )


def delivery_observations() -> dict[str, Any]:
    scenarios = {
        "confirm": test_confirm_an_accepted_delivery_does_not_send_again,
        "dismiss": test_dismiss_closes_without_claiming_delivery_or_retrying,
        "retry_snapshot": test_retry_uses_saved_snapshot_and_parent_once_after_restart,
    }
    for replay in scenarios.values():
        replay()
    return {name: "passed synthetic invariant" for name in scenarios}
