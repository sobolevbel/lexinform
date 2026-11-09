"""Pin observed use of reserved corpus cases without rewriting their queue or annotations."""

import argparse
import json
from collections import defaultdict, deque
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from lexinform.models.physical_inventory import Sha256
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_runs import verify

type ExposurePin = tuple[Path, str]
type ExposureKind = Literal["assisted_review", "qualification"]
type ExposureNode = tuple[str, str]


class ExposureCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, max_length=256)
    group: str = Field(min_length=1, max_length=256)
    originals: tuple[Sha256, ...] = Field(min_length=1, max_length=64)


class HoldoutExposure(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1] = 1
    queue: str
    queue_sha256: Sha256
    kind: ExposureKind
    evidence: str
    evidence_sha256: Sha256
    cases: tuple[ExposureCase, ...] = Field(min_length=1, max_length=256)


def _pin(path: Path, expected: str) -> bytes:
    data = read_artifact(path)
    if digest(data) != expected:
        raise ValueError(f"Exposure SHA mismatch: {path.name}")
    return data


def _queue_cases(queue: Path, queue_pin: str) -> dict[str, ExposureCase]:
    _pin(queue / "manifest.json", queue_pin)
    manifest = verify(queue)
    if (manifest["suite"], manifest["status"]) != ("holdout", "recorded"):
        raise ValueError("Exposure requires a sealed reserved holdout queue")
    result = json.loads(_pin(queue / "result.json", manifest["artifacts"]["result.json"]["sha256"]))
    _pin(queue / "manifest.json", queue_pin)
    cases = {
        row["id"]: ExposureCase(
            id=row["id"],
            group=row["group"],
            originals=tuple(sorted({item["sha256"] for item in row["originals"]})),
        )
        for row in result["cases"]
    }
    if len(cases) != len(result["cases"]):
        raise ValueError("Exposure queue contains duplicate case identities")
    return cases


def _validate(exposure: HoldoutExposure) -> None:
    queue = Path(exposure.queue)
    if not queue.is_absolute() or not Path(exposure.evidence).is_absolute():
        raise ValueError("Exposure requires exact local artifact paths")
    cases = _queue_cases(queue, exposure.queue_sha256)
    ids = [case.id for case in exposure.cases]
    if ids != sorted(set(ids)) or any(cases.get(case.id) != case for case in exposure.cases):
        raise ValueError("Exposure cases differ from the pinned queue identities or originals")
    proof = json.loads(_pin(Path(exposure.evidence), exposure.evidence_sha256))
    if not isinstance(proof, dict):
        raise ValueError("Exposure evidence must be a JSON object")
    if exposure.kind == "assisted_review":
        if (
            proof.get("review_type") != "human_verification_of_agent_draft"
            or proof.get("queue_manifest_sha256") != exposure.queue_sha256
            or set(proof.get("cases", {})) != set(ids)
            or any(
                proof["cases"][case.id].get("original_sha256") not in case.originals
                for case in exposure.cases
            )
        ):
            raise ValueError("Assisted exposure evidence does not bind the complete shown cases")
    elif len(ids) != 1 or not any(
        proof.get(key) in exposure.cases[0].originals
        for key in ("source_sha256", "artifact_sha256")
    ):
        raise ValueError("Qualification evidence does not bind the exposed original")


def load_exposures(inputs: Sequence[ExposurePin]) -> dict[str, HoldoutExposure]:
    if len({pin for _, pin in inputs}) != len(inputs):
        raise ValueError("Supply each exposure receipt once")
    result = {}
    for path, pin in inputs:
        exposure = HoldoutExposure.model_validate_json(_pin(path, pin))
        _validate(exposure)
        result[pin] = exposure
    return result


def create(
    queue: Path,
    queue_pin: str,
    kind: ExposureKind,
    evidence: Path,
    evidence_pin: str,
    identities: Sequence[str],
    output: Path,
) -> HoldoutExposure:
    queue = queue.resolve(strict=True)
    if output.resolve().is_relative_to(queue):
        raise ValueError("Exposure receipt must be outside the sealed queue")
    candidates = _queue_cases(queue, queue_pin)
    try:
        cases = tuple(candidates[identity] for identity in sorted(identities))
    except KeyError as exc:
        raise ValueError("Exposure names a case outside the queue") from exc
    exposure = HoldoutExposure(
        queue=str(queue),
        queue_sha256=queue_pin,
        kind=kind,
        evidence=str(evidence.resolve(strict=True)),
        evidence_sha256=evidence_pin,
        cases=cases,
    )
    _validate(exposure)
    write_json(output, exposure.model_dump(mode="json"))
    return exposure


def connected_exposed_cases(
    cases: Iterable[ExposureCase], exposures: Iterable[HoldoutExposure]
) -> set[str]:
    links: dict[ExposureNode, set[ExposureNode]] = defaultdict(set)
    exposed: set[ExposureNode] = set()

    def connect(case: ExposureCase) -> ExposureNode:
        node = ("case", case.id)
        for alias in (("group", case.group), *(("raw", raw) for raw in case.originals)):
            links[node].add(alias)
            links[alias].add(node)
        return node

    for case in cases:
        connect(case)
    queues: set[str] = set()
    for exposure in exposures:
        if exposure.queue_sha256 not in queues:
            queues.add(exposure.queue_sha256)
            for case in _queue_cases(Path(exposure.queue), exposure.queue_sha256).values():
                connect(case)
        for case in exposure.cases:
            exposed.add(connect(case))
    pending = deque(exposed)
    while pending:
        for alias in links[pending.popleft()] - exposed:
            exposed.add(alias)
            pending.append(alias)
    return {name for kind, name in exposed if kind == "case"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", nargs=2, metavar=("DIRECTORY", "SHA256"), required=True)
    parser.add_argument("--evidence", nargs=2, metavar=("FILE", "SHA256"), required=True)
    parser.add_argument("--kind", choices=("assisted_review", "qualification"), required=True)
    parser.add_argument("--case", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        create(
            Path(args.queue[0]),
            args.queue[1],
            args.kind,
            Path(args.evidence[0]),
            args.evidence[1],
            args.case,
            args.output,
        )
    except (OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
