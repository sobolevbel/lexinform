"""Authorize one exact finite legacy maintenance plan without running application effects."""

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from lexinform.adapters.sqlite_repo import SCHEMA_VERSION, SqliteBillRepository
from lexinform.legacy_maintenance import LegacyMaintenancePlan


class MaintenanceSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    version: Literal[1] = 1
    operation: Literal["legacy_compact"] = "legacy_compact"
    expected_state_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    imported_at: AwareDatetime

    @field_validator("version", mode="before")
    @classmethod
    def integer_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("Maintenance version must be an integer")
        return value

    @field_validator("imported_at")
    @classmethod
    def utc_import_time(cls, value: datetime) -> datetime:
        if value.utcoffset() != timedelta(0):
            raise ValueError("Maintenance import time must use UTC")
        return value


def _git(directory: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(directory), *arguments],
        check=True,
        text=True,
        capture_output=True,
        timeout=30,
    ).stdout.strip()


def _state_baseline(state_file: Path, expected: str) -> str:
    state_file = state_file.resolve(strict=True)
    root = Path(_git(state_file.parent, "rev-parse", "--show-toplevel")).resolve()
    if root != state_file.parent or _git(root, "rev-parse", "HEAD") != expected:
        raise ValueError("Maintenance requires the exact checked-out state commit")
    tracked = subprocess.run(
        ["git", "-C", str(root), "show", f"HEAD:{state_file.name}"],
        check=True,
        capture_output=True,
        timeout=30,
    ).stdout
    if tracked != state_file.read_bytes():
        raise ValueError("Maintenance state dump differs from its checked-out commit")
    return tracked.decode("utf-8")


def plan_sha256(plan: LegacyMaintenancePlan) -> str:
    return hashlib.sha256(TypeAdapter(LegacyMaintenancePlan).dump_json(plan)).hexdigest()


def _plan(repo: SqliteBillRepository, imported_at: datetime, source: str) -> LegacyMaintenancePlan:
    if repo.schema_version != SCHEMA_VERSION:
        raise ValueError("Maintenance requires an already restored current schema")
    restored = SqliteBillRepository(":memory:")
    try:
        restored.restore(source)
        if restored.dump() != repo.dump():
            raise ValueError("Maintenance database differs from its exact restored state")
    finally:
        restored.close()
    return repo.plan_legacy_maintenance(imported_at=imported_at, share_analysis_bodies=True)


def prepare(
    database: Path, state_file: Path, *, expected_state_sha: str, imported_at: datetime
) -> MaintenanceSpec:
    source = _state_baseline(state_file, expected_state_sha)
    database.resolve(strict=True)
    repo = SqliteBillRepository(database)
    try:
        with repo.atomic():
            plan = _plan(repo, imported_at, source)
        return MaintenanceSpec(
            expected_state_sha=expected_state_sha,
            snapshot_sha256=plan.snapshot_sha256,
            plan_sha256=plan_sha256(plan),
            imported_at=imported_at,
        )
    finally:
        repo.close()


def apply(
    database: Path, state_file: Path, spec: MaintenanceSpec, *, dry_run: bool
) -> dict[str, object]:
    source = _state_baseline(state_file, spec.expected_state_sha)
    database.resolve(strict=True)
    repo = SqliteBillRepository(database)
    try:
        with repo.atomic():
            plan = _plan(repo, spec.imported_at, source)
            if (
                plan.snapshot_sha256 != spec.snapshot_sha256
                or plan_sha256(plan) != spec.plan_sha256
            ):
                raise ValueError("Maintenance plan differs from its reviewed exact snapshot")
            result = None if dry_run else repo.apply_legacy_maintenance(plan)
        return {
            "operation": spec.operation,
            "expected_state_sha": spec.expected_state_sha,
            "snapshot_sha256": spec.snapshot_sha256,
            "plan_sha256": spec.plan_sha256,
            "imported_at": spec.imported_at.isoformat(),
            "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "dry_run": dry_run,
            "applied": result.applied if result is not None else False,
            "manifest_sha256": result.manifest_sha256 if result is not None else None,
            "baseline_sql_bytes": result.baseline_sql_bytes if result is not None else None,
            "baseline_gzip_bytes": result.baseline_gzip_bytes if result is not None else None,
            "compact_sql_bytes": result.compact_sql_bytes if result is not None else None,
            "compact_gzip_bytes": result.compact_gzip_bytes if result is not None else None,
        }
    finally:
        repo.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "apply"):
        command = commands.add_parser(name)
        command.add_argument("--database", type=Path, required=True)
        command.add_argument("--state-file", type=Path, required=True)
        if name == "prepare":
            command.add_argument("--expected-state-sha", required=True)
            command.add_argument("--imported-at", required=True)
        else:
            command.add_argument("--spec", required=True)
            command.add_argument("--dry-run", action="store_true")
            command.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            spec = prepare(
                args.database,
                args.state_file,
                expected_state_sha=args.expected_state_sha,
                imported_at=TypeAdapter(AwareDatetime).validate_python(args.imported_at),
            )
            print(spec.model_dump_json())
        else:
            if len(args.spec) > 4096 or len(args.spec.encode("utf-8")) > 4096:
                raise ValueError("Maintenance specification exceeds its fixed envelope")
            spec = MaintenanceSpec.model_validate_json(args.spec)
            receipt = apply(args.database, args.state_file, spec, dry_run=args.dry_run)
            print(json.dumps(receipt, sort_keys=True))
            if receipt["applied"] is True and args.github_output is not None:
                with args.github_output.open("a", encoding="utf-8") as output:
                    output.write("maintenance_authorized=true\n")
    except Exception as error:
        raise SystemExit(f"maintenance refused ({type(error).__name__})") from None


if __name__ == "__main__":
    main()
