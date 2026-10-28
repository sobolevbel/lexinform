"""Validate fixed RCL pilot plans and reserve bounded application and retained bytes."""

import datetime as dt
import fcntl
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx2 as httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from lexinform.models import RclProject
from tools.corpus_artifacts import create_only, digest, read_artifact
from tools.corpus_health import local_path
from tools.corpus_http import attachment_extension, fetch
from tools.corpus_physical import container_state
from tools.corpus_runs import finish, new_run, verify, write_started
from tools.corpus_runtime import capture_code, source_fingerprints

BODY_BYTES = 5 * 1024**2
CHUNK_BYTES = 64 * 1024
TOTAL_BYTES = 128 * 1024**2
METADATA_BYTES = 24 * 1024**2
SMALL_BYTES = 64 * 1024
LARGE_BYTES = 256 * 1024
PLAN_BYTES = 128 * 1024
UNKNOWN_BYTES = BODY_BYTES * 2 + CHUNK_BYTES


class PilotPin(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    path: str = Field(min_length=1, max_length=4096)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bytes: int = Field(ge=0, le=32_000_000)


class PilotSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: str = Field(pattern=r"^[0-9]{1,20}$")
    url: str = Field(max_length=2048)
    declared_extension: Literal["pdf", "doc", "docx", "docm", "odt", "xml", "zip"]
    project: int = Field(gt=0)
    stage: int | None = Field(default=None, gt=0)
    folder_kind: str | None = Field(default=None, max_length=128)
    catalog_path: str = Field(pattern=r"^corpus/[0-9]+\.json$")
    catalog_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    historical_created: str | None = Field(default=None, max_length=128)
    expected_raw_sha256: None = None
    fetched_at: None = None

    @model_validator(mode="after")
    def official_identity(self) -> PilotSource:
        parsed = urlsplit(self.url)
        match = re.search(r"dokument([0-9]+)\.([a-zA-Z0-9]+)$", parsed.path)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "legislacja.rcl.gov.pl"
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
            or parsed.query
            or parsed.fragment
            or match is None
            or match[1] != self.id
            or match[2].lower() != self.declared_extension
            or self.catalog_path != f"corpus/{self.project}.json"
        ):
            raise ValueError("Pilot source must match its official URL and catalog identity")
        return self


class PilotLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    max_source_ids: Literal[20]
    max_source_attempts: Literal[20]
    workers: Literal[1]
    automatic_retries: Literal[0]
    max_body_bytes_per_attempt: Literal[5242880]
    max_total_application_bytes: Literal[134217728]
    reserved_body_bytes: Literal[104857600]
    max_stream_chunk_bytes: Literal[65536]
    max_budget_for_source_snapshots_receipts_and_logs: Literal[25165824]
    redirect_hops_per_attempt_max: Literal[6]
    http_operation_timeout_seconds: Literal[15]
    elapsed_fetch_guard_seconds: Literal[60]
    network_allowed_now: Literal[False]
    interpretation: str = Field(max_length=2048)


class PilotPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: Literal[1]
    kind: Literal["rcl-gap-originals-pilot-v1"]
    status: Literal["planned; not executable by the uncapped legacy collector"]
    input_pins: dict[str, PilotPin]
    limits: PilotLimits
    sources: tuple[PilotSource, ...] = Field(min_length=1, max_length=20)
    selection: dict[str, Any]
    provenance_limits: tuple[str, ...]

    @model_validator(mode="after")
    def fixed_population(self) -> PilotPlan:
        if set(self.input_pins) != {"index", "document_index", "candidates", "readiness"} or len(
            {source.id for source in self.sources}
        ) != len(self.sources):
            raise ValueError("Pilot needs all pinned inputs and unique declared sources")
        return self


def metadata_reservation(archive_bytes: int, source_json_bytes: int, slots: int) -> int:
    return 3 * (archive_bytes + source_json_bytes) + 12 * LARGE_BYTES + slots * 10 * SMALL_BYTES


def receipt_charge(receipt: dict[str, Any], retained: int) -> int:
    if not 0 <= retained <= BODY_BYTES:
        raise ValueError("Retained source exceeds the pilot reservation")
    observed = receipt.get("bytes_read")
    if (
        not isinstance(observed, int)
        or isinstance(observed, bool)
        or not 0 <= observed <= BODY_BYTES + CHUNK_BYTES
    ):
        raise ValueError("Receipt byte count exceeds the pilot reservation")
    if receipt.get("complete") is True:
        digest = receipt.get("sha256")
        if not isinstance(digest, str) or re.fullmatch(r"[a-f0-9]{64}", digest) is None:
            raise ValueError("Complete receipt has no body identity")
        return observed + retained
    if receipt.get("sha256") is not None:
        raise ValueError("Incomplete receipt claims a complete body identity")
    return (BODY_BYTES + CHUNK_BYTES if receipt.get("body_started") else 0) + retained


def pilot_json(path: Path, value: Any, limit: int = SMALL_BYTES) -> None:
    raw = (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode()
    if len(raw) > limit:
        raise ValueError("Pilot metadata exceeds its frozen reservation")
    create_only(path, raw)
    with path.open("rb") as stream:
        os.fsync(stream.fileno())


def pilot_read(path: Path, limit: int = LARGE_BYTES) -> bytes:
    if path.absolute() != path.resolve() or path.stat().st_size > limit:
        raise ValueError("Pilot artifact is aliased or exceeds its bound")
    return read_artifact(path)


def pilot_inputs(
    corpus: Path, path: Path, pin: str
) -> tuple[PilotPlan, dict[str, Any], dict[str, str]]:
    raw = pilot_read(path, PLAN_BYTES)
    if digest(raw) != pin:
        raise ValueError("Pilot plan SHA mismatch")
    try:
        plan = PilotPlan.model_validate_json(raw)
    except ValueError:
        raise ValueError("Pilot plan is invalid") from None
    inputs = {str(path): pin}
    for name, artifact in plan.input_pins.items():
        artifact_path = Path(artifact.path)
        if not artifact_path.is_absolute():
            raise ValueError("Pilot input paths must be absolute")
        data = pilot_read(artifact_path, 32_000_000)
        if len(data) != artifact.bytes or digest(data) != artifact.sha256:
            raise ValueError("Pilot input pin changed")
        if name in ("index", "document_index") and artifact_path != corpus / (
            "INDEX.json" if name == "index" else "documents/index.json"
        ):
            raise ValueError("Pilot corpus identity differs from its pinned input")
        inputs[str(artifact_path)] = artifact.sha256
    candidate = json.loads(read_artifact(Path(plan.input_pins["candidates"].path)))
    missing = set(candidate.get("missing_inputs", ()))
    if candidate.get("status") != "recorded" or any(
        f"documents/text/{source.id}.txt.gz" not in missing
        or candidate.get("read_inputs", {}).get(f"documents/text/{source.id}.txt.gz", "not_missing")
        is not None
        for source in plan.sources
    ):
        raise ValueError("Pilot sources are not the declared absent cache population")
    readiness = json.loads(read_artifact(Path(plan.input_pins["readiness"].path)))
    for name in ("index", "document_index", "candidates"):
        artifact = plan.input_pins[name]
        if (
            readiness.get("coverage", {}).get("inputs", {}).get(artifact.path, {}).get("sha256")
            != artifact.sha256
        ):
            raise ValueError("Pilot readiness is not bound to its input snapshots")
    index = json.loads(read_artifact(corpus / "documents/index.json"))
    metadata: dict[str, Any] = {}
    for source in plan.sources:
        if (
            source.id in index
            or (corpus / f"documents/text/{source.id}.txt.gz").exists()
            or any((corpus / "documents/files").glob(f"{source.id}.*"))
        ):
            raise ValueError("Pilot source is no longer an absent original/cache")
        catalog = local_path(corpus, source.catalog_path)
        raw = pilot_read(catalog, 32_000_000)
        if digest(raw) != source.catalog_sha256:
            raise ValueError("Pilot official catalog changed")
        inputs[str(catalog)] = source.catalog_sha256
        project = RclProject.model_validate_json(raw)
        found = [
            (document, folder)
            for stage in project.stages
            if source.stage is None or stage.number == source.stage
            for folder in stage.folders
            for document in folder.documents
            if str(document.id) == source.id and document.url == source.url
        ]
        if project.id != source.project or len(found) != 1:
            raise ValueError("Pilot source has no unique official catalog binding")
        document, folder = found[0]
        if source.folder_kind is not None and source.folder_kind != folder.kind:
            raise ValueError("Pilot folder classification differs from its catalog")
        meta = {
            **document.model_dump(mode="json"),
            "project": source.project,
            "folder": folder.kind,
            "catalog_path": source.catalog_path,
        }
        if len(json.dumps(meta).encode()) > SMALL_BYTES // 4:
            raise ValueError("Pilot source metadata exceeds its reservation")
        metadata[source.id] = meta
    return plan, metadata, inputs


def pilot_unchanged(inputs: dict[str, str], code: dict[str, str]) -> None:
    for name, expected in inputs.items():
        path = Path(name)
        if (
            path.absolute() != path.resolve()
            or not path.is_file()
            or digest(read_artifact(path)) != expected
        ):
            raise ValueError("Pilot pinned input changed during staging")
    if source_fingerprints() != code:
        raise ValueError("Pilot frozen code changed during staging")


def pilot_metadata_bytes(
    directory: Path,
    identities: set[str],
    retained: dict[str, dict[tuple[int, int], int]] | None = None,
    *,
    finalized: bool = False,
) -> int:
    total = 0
    bodies: dict[str, dict[tuple[int, int], int]] = retained if retained is not None else {}
    for number, path in enumerate(directory.rglob("*")):
        if number > 1024 or path.is_symlink():
            raise ValueError("Pilot staging directory contains unbounded or aliased artifacts")
        if path.is_file():
            size = path.stat()
            if {"downloads", "quarantine"}.intersection(path.relative_to(directory).parts):
                key = path.name.split(".", 1)[0]
                relative = path.relative_to(directory)
                parts = relative.parts
                if finalized and parts[:2] == ("bundle", "pilot"):
                    parts = parts[2:]
                child_body = (
                    len(parts) == 4
                    and parts[:2] == ("children", f"slot-{key}")
                    and parts[2] in ("downloads", "quarantine")
                )
                final_body = finalized and len(parts) == 2 and parts[0] == "downloads"
                if (
                    key not in identities
                    or size.st_size > BODY_BYTES
                    or not (child_body or final_body)
                    or re.fullmatch(r"[0-9]+\.(pdf|doc|docx|docm|odt|xml|zip)(\.tmp)?", path.name)
                    is None
                ):
                    raise ValueError("Pilot raw artifact exceeds its source reservation")
                bodies.setdefault(key, {})[(size.st_dev, size.st_ino)] = size.st_size
            else:
                total += size.st_size
    if any(sum(files.values()) > BODY_BYTES for files in bodies.values()):
        raise ValueError("Pilot source retained multiple independent bodies")
    if total > METADATA_BYTES:
        raise ValueError("Pilot metadata reservation exceeded")
    return total


def pilot_verify(directory: Path) -> dict[str, Any]:
    if directory.absolute() != directory.resolve():
        raise ValueError("Pilot sealed run must not be an alias")
    actual: set[str] = set()
    total = 0
    for number, path in enumerate(directory.rglob("*")):
        if number > 1024 or path.is_symlink():
            raise ValueError("Pilot sealed run contains unbounded or aliased artifacts")
        if path.is_file():
            relative = path.relative_to(directory)
            size = path.stat().st_size
            limit = (
                BODY_BYTES
                if {"downloads", "quarantine"}.intersection(relative.parts)
                else METADATA_BYTES
            )
            total += size
            if size > limit or total > TOTAL_BYTES:
                raise ValueError("Pilot sealed artifacts exceed their byte bounds")
            actual.add(str(relative))
    manifest = json.loads(pilot_read(directory / "manifest.json"))
    if actual != set(manifest["artifacts"]) | {"manifest.json", "manifest.sha256"}:
        raise ValueError("Pilot sealed run contains undeclared artifacts")
    return verify(directory)


def pilot_inventory(
    directory: Path, identities: set[str], outcomes: dict[str, Any]
) -> tuple[int, dict[str, dict[tuple[int, int], int]]]:
    retained: dict[str, dict[tuple[int, int], int]] = {}
    metadata = pilot_metadata_bytes(directory, identities, retained)
    for key in identities:
        size = sum(retained.get(key, {}).values())
        outcome = outcomes.get(key, {})
        if size > outcome.get("retained_bytes_charged", 0) or (
            outcome.get("state") != "unknown" and size != outcome.get("retained_bytes_observed", 0)
        ):
            raise ValueError("Pilot retained inode inventory differs from its reservations")
    return metadata, retained


def pilot_child(directory: Path, key: str, pin: str, snapshot: str) -> tuple[dict[str, Any], int]:
    manifest = pilot_verify(directory)
    started = manifest["started"]
    if (
        manifest["suite"] != "sources"
        or manifest["status"] != "recorded"
        or started.get("source_id") != key
        or started.get("pilot_plan_sha256") != pin
        or started.get("shared_snapshot_sha256") != snapshot
        or started.get("shared_snapshot_path") != "../../code.tar.gz"
        or digest(pilot_read(directory.parent.parent / "code.tar.gz", METADATA_BYTES)) != snapshot
    ):
        raise ValueError("Pilot child identity or shared snapshot dependency differs")
    report = json.loads(pilot_read(directory / "result.json"))
    if set(report["observations"]) != {key}:
        raise ValueError("Pilot child has the wrong source population")
    row: dict[str, Any] = report["observations"][key]
    if row.get("state") not in ("downloaded", "staged_unusable", "unavailable") or json.loads(
        pilot_read(directory / "receipt.json")
    ) != row.get("receipt"):
        raise ValueError("Pilot child outcome or receipt is inconsistent")
    if row["state"] != "unavailable" and row.get("path") is None:
        raise ValueError("Pilot retained original has no owned path")
    retained = 0
    if row.get("path") is not None:
        relative = row["path"]
        if (
            relative not in manifest["artifacts"]
            or row.get("extension") not in ("pdf", "doc", "docx", "docm", "odt", "xml", "zip")
            or relative
            not in (f"downloads/{key}.{row['extension']}", f"quarantine/{key}.{row['extension']}")
        ):
            raise ValueError("Pilot original path is not owned by its sealed source")
        data = pilot_read(local_path(directory, relative), BODY_BYTES)
        if digest(data) != row.get("sha256") or len(data) != row.get("bytes"):
            raise ValueError("Pilot original differs from its source receipt")
        retained = len(data)
        if (
            row.get("state") == "downloaded"
            and row.get("container", {}).get("state") != "valid_container"
        ):
            raise ValueError("Pilot installable source has no valid container receipt")
        if row["receipt"].get("complete") is not True or row["receipt"].get("sha256") != row.get(
            "sha256"
        ):
            raise ValueError("Pilot original has no complete response identity")
    charge = receipt_charge(row["receipt"], retained)
    return row, charge


def pilot_outcome(directory: Path, key: str, pin: str, snapshot: str) -> dict[str, Any]:
    row, amount = pilot_child(directory, key, pin, snapshot)
    retained = row.get("bytes", 0) if row.get("path") is not None else 0
    return {
        "state": row["state"],
        "charged_bytes": amount,
        "application_bytes_observed": row["receipt"]["bytes_read"],
        "application_bytes_charged": amount - retained,
        "retained_bytes_observed": retained,
        "retained_bytes_charged": retained,
        "manifest_sha256": digest(read_artifact(directory / "manifest.json")),
    }


def pilot_existing(
    directory: Path, identities: set[str], pin: str, snapshot: str
) -> tuple[dict[str, Any], int]:
    outcomes: dict[str, Any] = {}
    allowed = {f"slot-{key}" for key in identities}
    for number, child in enumerate((directory / "children").iterdir()):
        if number >= 20 or child.name not in allowed or not child.is_dir() or child.is_symlink():
            raise ValueError("Pilot contains an undeclared source slot or alias")
    for key in sorted(identities):
        child = directory / "children" / f"slot-{key}"
        if child.exists():
            if (child / "manifest.json").exists() and (child / "manifest.sha256").exists():
                outcomes[key] = pilot_outcome(child, key, pin, snapshot)
            else:
                outcomes[key] = {
                    "state": "unknown",
                    "charged_bytes": UNKNOWN_BYTES,
                    "application_bytes_observed": None,
                    "application_bytes_charged": BODY_BYTES + CHUNK_BYTES,
                    "retained_bytes_observed": None,
                    "retained_bytes_charged": BODY_BYTES,
                }
    return outcomes, sum(row["charged_bytes"] for row in outcomes.values())


def pilot_fsync(path: Path) -> None:
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def pilot_complete(saved: dict[str, Any], current: dict[str, Any], identities: set[str]) -> None:
    expected = saved.get("observations", {})
    if (
        saved.get("all_declared_dispositions") is not True
        or set(expected) != identities
        or any(expected[key] != value for key, value in current.items())
        or any(
            row
            != {
                "state": "unattempted_budget",
                "charged_bytes": 0,
                "application_bytes_observed": 0,
                "application_bytes_charged": 0,
                "retained_bytes_observed": 0,
                "retained_bytes_charged": 0,
            }
            for key, row in expected.items()
            if key not in current
        )
    ):
        raise ValueError("Completed pilot dispositions or dependencies changed")
    for name in (
        "application_bytes_observed",
        "application_bytes_charged",
        "retained_bytes_observed",
        "retained_bytes_charged",
    ):
        if saved.get(name) != sum(row[name] or 0 for row in expected.values()):
            raise ValueError("Completed pilot byte accounting differs from its dispositions")
    if (
        saved.get("charged_application_and_retained_bytes")
        != sum(row["charged_bytes"] for row in expected.values())
        or saved.get("reserved_metadata_bytes") != METADATA_BYTES
        or saved.get("unknown_attempts")
        != sum(row["state"] == "unknown" for row in expected.values())
    ):
        raise ValueError("Completed pilot reservations differ from its dispositions")


def _pilot(
    corpus: Path,
    plan_path: Path,
    pin: str,
    root: Path,
    identity: str,
    client: httpx.Client,
    *,
    resume: bool = False,
) -> dict[str, Any]:
    corpus, root = corpus.resolve(strict=True), root.absolute()
    if (
        root != root.resolve()
        or root.is_relative_to(corpus)
        or re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", identity) is None
    ):
        raise ValueError("Pilot output must be canonical and outside corpus")
    plan, metadata, inputs = pilot_inputs(corpus, plan_path.absolute(), pin)
    code = source_fingerprints()
    directory = root / identity
    sealed = False
    if directory.exists():
        if not resume:
            raise FileExistsError(directory)
        started = json.loads(pilot_read(directory / "started.json"))
        if (
            started.get("pilot_plan_sha256") != pin
            or started.get("source_sha256") != code
            or started.get("read_inputs") != inputs
        ):
            raise ValueError("Pilot resume has different plan, inputs or code")
        if (directory / "manifest.json").exists():
            sealed = True
    else:
        with tempfile.TemporaryDirectory(prefix="lexinform-pilot-code-") as temporary:
            snapshot = capture_code(Path(temporary), code)
            if (
                metadata_reservation(
                    (Path(temporary) / snapshot["path"]).stat().st_size,
                    len(json.dumps(code).encode()),
                    len(plan.sources),
                )
                > METADATA_BYTES
            ):
                raise ValueError("Pilot frozen archive and receipt reservation exceed24MiB")
        directory = new_run(root, identity, "sources")
        started = {
            "pilot_plan_sha256": pin,
            "source_sha256": code,
            "read_inputs": inputs,
            "scope": "Bounded staging only; no corpus publication",
            "network": "explicit serial pilot requests",
        }
        write_started(directory, started)
    snapshot_path = directory / "code.tar.gz"
    snapshot_sha = digest(pilot_read(snapshot_path, METADATA_BYTES))
    if snapshot_sha != started["code_snapshot"]["sha256"]:
        raise ValueError("Pilot shared source snapshot changed")
    children = directory / "children"
    children.mkdir(exist_ok=True)
    identities = {source.id for source in plan.sources}
    pilot_metadata_bytes(directory, identities)
    outcomes, charged = pilot_existing(directory, identities, pin, snapshot_sha)
    pilot_inventory(directory, identities, outcomes)
    if charged + METADATA_BYTES > TOTAL_BYTES:
        raise ValueError("Existing pilot reservations exhaust the byte budget")
    if sealed:
        checked = pilot_verify(directory)
        if checked["status"] != "recorded":
            raise ValueError("Pilot resume is not a completed staging run")
        saved = json.loads(pilot_read(directory / "result.json"))
        pilot_complete(saved, outcomes, identities)
        return checked
    for source in plan.sources:
        pilot_unchanged(inputs, code)
        pilot_inventory(directory, identities, outcomes)
        if digest(read_artifact(snapshot_path)) != snapshot_sha:
            raise ValueError("Pilot shared snapshot changed before the next source")
        child = children / f"slot-{source.id}"
        if source.id in outcomes:
            continue
        if (corpus / f"documents/text/{source.id}.txt.gz").exists() or any(
            (corpus / "documents/files").glob(f"{source.id}.*")
        ):
            raise ValueError("Pilot source appeared in active corpus during staging")
        if not child.exists():
            if (
                charged + UNKNOWN_BYTES + METADATA_BYTES > TOTAL_BYTES
                or pilot_metadata_bytes(directory, identities) + 4 * SMALL_BYTES + 2 * LARGE_BYTES
                > METADATA_BYTES
            ):
                outcomes[source.id] = {
                    "state": "unattempted_budget",
                    "charged_bytes": 0,
                    "application_bytes_observed": 0,
                    "application_bytes_charged": 0,
                    "retained_bytes_observed": 0,
                    "retained_bytes_charged": 0,
                }
                continue
            child = new_run(children, f"slot-{source.id}", "sources")
            intent = {
                "source_id": source.id,
                "pilot_plan_sha256": pin,
                "shared_snapshot_sha256": snapshot_sha,
                "shared_snapshot_path": "../../code.tar.gz",
                "reserved_bytes": UNKNOWN_BYTES,
                "phase": "request_started",
                "recorded_at": dt.datetime.now(dt.UTC).isoformat(),
            }
            write_started(child, intent)
            for path in (snapshot_path, directory / "started.json", child / "started.json"):
                pilot_fsync(path)
            for path in (child, *child.parents):
                pilot_fsync(path)
            receipt: dict[str, Any] = {}
            row: dict[str, Any] = {
                "state": "unavailable",
                "url": source.url,
                "metadata": metadata[source.id],
                "receipt": receipt,
            }
            raw = None
            disabled = logging.root.manager.disable
            try:
                logging.disable(logging.CRITICAL)
                response = fetch(
                    client,
                    source.url,
                    max_bytes=BODY_BYTES,
                    chunk_size=CHUNK_BYTES,
                    strict=True,
                    receipt=receipt,
                )
                observed = container_state(response.data)
                row["container"] = observed
                extension = attachment_extension(response.data, source.declared_extension)
                row["extension"] = extension
                raw = response.data
            except Exception as exc:
                row["error_type"] = type(exc).__name__[:128]
            finally:
                logging.disable(disabled)
            pilot_json(child / "receipt.json", receipt)
            pilot_unchanged(inputs, code)
            if raw is not None:
                state = (
                    "downloaded"
                    if row["container"]["state"] == "valid_container"
                    else "staged_unusable"
                )
                folder = "downloads" if state == "downloaded" else "quarantine"
                (child / folder).mkdir()
                target = child / folder / f"{source.id}.{row['extension']}"
                temporary_body = target.with_suffix(target.suffix + ".tmp")
                with temporary_body.open("xb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.link(temporary_body, target)
                temporary_body.unlink()
                row.update(
                    state=state,
                    path=str(target.relative_to(child)),
                    sha256=digest(raw),
                    bytes=len(raw),
                )
            pilot_json(
                child / "result.json", {"status": "recorded", "observations": {source.id: row}}
            )
            finish(child, "sources", returncode=0, started=intent)
        outcome = pilot_outcome(child, source.id, pin, snapshot_sha)
        charged += outcome["charged_bytes"]
        if charged + METADATA_BYTES > TOTAL_BYTES:
            raise ValueError("Pilot application and retained byte budget exceeded")
        outcomes[source.id] = outcome
    pilot_unchanged(inputs, code)
    pilot_inventory(directory, identities, outcomes)
    if (
        digest(read_artifact(snapshot_path)) != snapshot_sha
        or charged + METADATA_BYTES > TOTAL_BYTES
    ):
        raise ValueError("Pilot snapshot or budget changed")
    for key, outcome in outcomes.items():
        if (
            "manifest_sha256" in outcome
            and digest(read_artifact(children / f"slot-{key}" / "manifest.json"))
            != outcome["manifest_sha256"]
        ):
            raise ValueError("Pilot child dependency changed during staging")
    result = {
        "status": "recorded",
        "observations": outcomes,
        "pilot_plan_sha256": pin,
        "index_sha256": plan.input_pins["document_index"].sha256,
        "shared_snapshot_sha256": snapshot_sha,
        "charged_application_and_retained_bytes": charged,
        "application_bytes_observed": sum(
            row["application_bytes_observed"] or 0 for row in outcomes.values()
        ),
        "application_bytes_charged": sum(
            row["application_bytes_charged"] for row in outcomes.values()
        ),
        "retained_bytes_observed": sum(
            row["retained_bytes_observed"] or 0 for row in outcomes.values()
        ),
        "retained_bytes_charged": sum(row["retained_bytes_charged"] for row in outcomes.values()),
        "unknown_attempts": sum(row["state"] == "unknown" for row in outcomes.values()),
        "reserved_metadata_bytes": METADATA_BYTES,
        "all_declared_dispositions": True,
        "scope": "Staged sources; legal role, historical edition and corpus acceptance unknown",
    }
    pilot_json(directory / "result.json", result, LARGE_BYTES)
    if pilot_metadata_bytes(directory, identities) + LARGE_BYTES > METADATA_BYTES:
        raise ValueError("Pilot final manifest exceeds the metadata reservation")
    return finish(directory, "sources", returncode=0, started=started)


def pilot(
    corpus: Path,
    plan_path: Path,
    pin: str,
    root: Path,
    identity: str,
    client: httpx.Client,
    *,
    resume: bool = False,
) -> dict[str, Any]:
    root = root.absolute()
    if (
        root != root.resolve()
        or root.is_relative_to(corpus.resolve())
        or re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", identity) is None
    ):
        raise ValueError("Pilot output must be canonical and outside corpus")
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / f".{identity}.pilot.lock"
    if lock_path.is_symlink():
        raise ValueError("Pilot lock must not be an alias")
    with lock_path.open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Pilot staging already has an active writer") from None
        return _pilot(corpus, plan_path, pin, root, identity, client, resume=resume)


def _finalize_pilot(
    corpus: Path,
    plan_path: Path,
    pin: str,
    pilot_run: Path,
    pilot_manifest_pin: str,
    root: Path,
    identity: str,
) -> dict[str, Any]:
    plan, metadata, inputs = pilot_inputs(corpus.resolve(strict=True), plan_path.absolute(), pin)
    pilot_run = pilot_run.absolute()
    root = root.absolute()
    if (
        pilot_run != pilot_run.resolve()
        or root != root.resolve()
        or root.is_relative_to(corpus.resolve())
        or root.is_relative_to(pilot_run)
    ):
        raise ValueError("Finalized output must be outside corpus and pilot input")
    identities = {source.id for source in plan.sources}
    pilot_metadata_bytes(pilot_run, identities)
    manifest = pilot_verify(pilot_run)
    if (
        digest(read_artifact(pilot_run / "manifest.json")) != pilot_manifest_pin
        or manifest["suite"] != "sources"
        or manifest["status"] != "recorded"
        or manifest["started"].get("pilot_plan_sha256") != pin
    ):
        raise ValueError("Finalization requires the pinned complete pilot run")
    snapshot_sha = manifest["started"]["code_snapshot"]["sha256"]
    if digest(read_artifact(pilot_run / "code.tar.gz")) != snapshot_sha:
        raise ValueError("Pilot shared snapshot dependency is corrupt")
    report = json.loads(pilot_read(pilot_run / "result.json"))
    if set(report["observations"]) != {source.id for source in plan.sources}:
        raise ValueError("Finalization must preserve every declared pilot disposition")
    code = source_fingerprints()
    if manifest["started"].get("source_sha256") != code:
        raise ValueError("Finalization frozen code differs from the pilot")
    current, charged = pilot_existing(pilot_run, identities, pin, snapshot_sha)
    pilot_complete(report, current, identities)
    if charged + METADATA_BYTES > TOTAL_BYTES:
        raise ValueError("Finalization source reservations exceed the byte budget")
    parent_metadata, retained = pilot_inventory(pilot_run, identities, current)
    original_retained = {key: dict(files) for key, files in retained.items()}
    retained_before = sum(sum(files.values()) for files in retained.values())
    if retained_before > sum(row["retained_bytes_charged"] for row in current.values()):
        raise ValueError("Finalization originals exceed their retained-byte reservations")
    if (
        2 * parent_metadata
        + (pilot_run / "code.tar.gz").stat().st_size
        + len(json.dumps(code).encode())
        + 2 * 1024**2
        + 2 * LARGE_BYTES
        > METADATA_BYTES
    ):
        raise ValueError("Finalization bundle exceeds the metadata reservation")
    rows: dict[str, Any] = {}
    for source in plan.sources:
        saved = report["observations"][source.id]
        if saved["state"] in ("unknown", "unattempted_budget"):
            rows[source.id] = {"state": saved["state"], "metadata": metadata[source.id]}
            continue
        child = pilot_run / "children" / f"slot-{source.id}"
        if digest(read_artifact(child / "manifest.json")) != saved["manifest_sha256"]:
            raise ValueError("Finalization child differs from its parent pin")
        row, _ = pilot_child(child, source.id, pin, snapshot_sha)
        if row.get("metadata") != metadata[source.id]:
            raise ValueError("Finalization source metadata differs from its official pin")
        rows[source.id] = row
    started = {
        "pilot_manifest_sha256": pilot_manifest_pin,
        "pilot_plan_sha256": pin,
        "shared_snapshot_sha256": snapshot_sha,
        "source_sha256": code,
        "network": "prohibited; offline finalization",
    }
    directory = root / identity
    if directory.exists():
        final_metadata = pilot_metadata_bytes(directory, identities, retained, finalized=True)
        if final_metadata + parent_metadata > METADATA_BYTES:
            raise ValueError("Existing finalization exceeds the shared metadata reservation")
        if not (directory / "manifest.json").exists():
            raise ValueError("Finalization is incomplete; preserve its deterministic output")
        existing = pilot_verify(directory)
        if any(existing["started"].get(key) != value for key, value in started.items()) or (
            existing["suite"] != "sources" or existing["status"] != "recorded"
        ):
            raise ValueError("Existing finalization differs from its pinned pilot")
        if pilot_verify(directory / "bundle/pilot") != manifest:
            raise ValueError("Existing finalization has a different pilot dependency")
        for source in plan.sources:
            child = directory / "bundle/pilot/children" / f"slot-{source.id}"
            if rows[source.id]["state"] not in ("unknown", "unattempted_budget"):
                pilot_child(child, source.id, pin, snapshot_sha)
        if retained != original_retained:
            raise ValueError("Existing finalization added independent retained bodies")
        pilot_unchanged(inputs, code)
        return existing
    directory = new_run(root, identity, "sources")
    write_started(directory, started)
    bundle = directory / "bundle" / "pilot"
    for path in pilot_run.rglob("*"):
        if path.is_symlink():
            raise ValueError("Pilot bundle contains a symlink")
        target = bundle / path.relative_to(pilot_run)
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.link(path, target)
            if not path.samefile(target):
                raise ValueError("Finalization requires verified deduplication by hardlink")
    (directory / "downloads").mkdir()
    for key, row in rows.items():
        if row["state"] == "downloaded":
            source = pilot_run / "children" / f"slot-{key}" / row["path"]
            target = directory / "downloads" / f"{key}.{row['extension']}"
            os.link(source, target)
            if not source.samefile(target):
                raise ValueError("Finalization requires verified deduplication by hardlink")
            rows[key] = {**row, "path": str(target.relative_to(directory))}
        elif row.get("path") is not None:
            rows[key] = {
                **row,
                "path": str(Path("bundle/pilot/children") / f"slot-{key}" / row["path"]),
            }
    pilot_unchanged(inputs, code)
    pilot_verify(pilot_run)
    if digest(read_artifact(pilot_run / "manifest.json")) != pilot_manifest_pin:
        raise ValueError("Pilot dependency changed during finalization")
    final_metadata = pilot_metadata_bytes(directory, identities, retained, finalized=True)
    retained_after = sum(sum(files.values()) for files in retained.values())
    if retained != original_retained:
        raise ValueError("Finalization added retained bodies outside the shared byte reservation")
    if final_metadata + parent_metadata + 2 * 1024**2 + LARGE_BYTES > METADATA_BYTES:
        raise ValueError("Finalized metadata exceeds the shared reservation")
    pilot_json(
        directory / "result.json",
        {
            "status": "recorded",
            "observations": rows,
            "index_sha256": plan.input_pins["document_index"].sha256,
            "pilot_dependency_verified": {
                "manifest_sha256": pilot_manifest_pin,
                "shared_snapshot_sha256": snapshot_sha,
                "bundle": "bundle/pilot",
            },
            "pilot_budget": {
                name: report[name]
                for name in (
                    "application_bytes_observed",
                    "application_bytes_charged",
                    "retained_bytes_observed",
                    "retained_bytes_charged",
                    "unknown_attempts",
                    "reserved_metadata_bytes",
                )
            },
            "retained_accounting": {
                "method": "Verified hardlinks; unique device/inode across pilot and final output",
                "unique_retained_bytes": retained_after,
                "additional_retained_body_bytes": 0,
                "metadata_shared_limit_bytes": METADATA_BYTES,
            },
            "scope": "Verified staged originals only; explicit install is a separate operation",
        },
        2 * 1024**2,
    )
    return finish(directory, "sources", returncode=0, started=started)


def finalize_pilot(
    corpus: Path,
    plan_path: Path,
    pin: str,
    pilot_run: Path,
    pilot_manifest_pin: str,
    root: Path,
    identity: str,
) -> dict[str, Any]:
    pilot_run, root = pilot_run.absolute(), root.absolute()
    if root != pilot_run.parent or identity != f"final-{pilot_manifest_pin}":
        raise ValueError("Finalization requires the deterministic sibling root and manifest ID")
    lock_path = root / f".{pilot_run.name}.pilot.lock"
    if lock_path.absolute() != lock_path.resolve():
        raise ValueError("Pilot lock must not be an alias")
    with lock_path.open("rb") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Pilot staging already has an active writer") from None
        return _finalize_pilot(
            corpus, plan_path, pin, pilot_run, pilot_manifest_pin, root, identity
        )
