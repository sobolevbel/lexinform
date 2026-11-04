"""Replay pinned archive roots through the public native loader without network or body output."""

import argparse
import io
import json
import logging
import warnings
import zipfile
from pathlib import Path
from typing import Any

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.models import CandidateDispositionTrace, TextDocument
from lexinform.models.document_reads import LoadedFile
from lexinform.models.physical_inventory import PhysicalContainerExecution, PhysicalRootOrder
from lexinform.observation_order import ObservationOrder
from lexinform.physical_observations import PhysicalObservationCollector
from lexinform.physical_root_assembly import PhysicalAssemblyPacket
from lexinform.physical_walk_capture import PhysicalWalkObservation
from lexinform.services.document_history import DocumentSourceRead
from lexinform.services.documents import TextLoader
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_runtime import CorpusReads, source_fingerprints

VERSION = "archive-dispositions-replay-v1"
INDEX_SHA256 = "2030ab1d13f961fcb09bd58247cbcf97aba3a3575725eed166940620f4c60666"
OPTIONS = {
    "max_bytes": 32_000_000,
    "max_member_bytes": 32_000_000,
    "max_part_chars": 400_000,
    "cache_size": 32,
}


def hashed(value: object) -> str:
    return digest(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    )


def zipped(rows: tuple[tuple[str, bytes], ...]) -> bytes:
    stream = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, body in rows:
                archive.writestr(zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0)), body)
    return stream.getvalue()


def synthetic_inputs() -> dict[str, bytes]:
    def docx(day: int) -> bytes:
        text = (
            f"Projekt z dnia {day} stycznia 2027 r. USTAWA z dnia {day} stycznia 2027 r. "
            "Art. 1. Termin wynosi 30 dni. " + "Cudzoziemiec składa wniosek. " * 20
        )
        xml = (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'
            + text
            + "</w:t></w:r></w:p></w:body></w:document>"
        ).encode()
        return zipped((("word/document.xml", xml),))

    first, second = docx(1), docx(2)
    duplicate = zipped((("project.docx", first), ("project.docx", second)))
    nested = zipped((("inner.zip", duplicate), ("unknown.bin", b"unknown")))
    deep = duplicate
    for _ in range(6):
        deep = zipped((("nested.zip", deep),))
    broken = bytearray(zipped((("bad.docx", first),)))
    broken[30 + len("bad.docx")] ^= 1
    return {
        "synthetic_duplicate_names": duplicate,
        "synthetic_nested_unknown": nested,
        "synthetic_depth": deep,
        "synthetic_crc": bytes(broken),
    }


def archive_leaf(data: bytes, ordinals: tuple[int, ...]) -> bytes:
    for ordinal in ordinals:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if ordinal >= len(members):
                raise ValueError("Archive ordinal is absent from the actual raw header")
            info = members[ordinal]
            if info.file_size > OPTIONS["max_member_bytes"]:
                raise ValueError("Independent member check exceeds the frozen raw byte profile")
            data = archive.read(info)
    return data


def file_core(selected: TextDocument, file: LoadedFile) -> dict[str, Any]:
    payload = {
        "text": file.text,
        "pages": file.pages,
        "oversize": file.oversize,
        "sha256": file.sha256,
        "selection": file.selection.model_dump(mode="json"),
        "opaque_failure_prose": file.opaque_failure_prose,
    }
    trace = (
        file.candidate_trace.model_dump(mode="json", exclude_defaults=True)
        if file.candidate_trace is not None
        else None
    )
    if file.candidate_trace is not None and file.candidate_trace.is_routine_singleton:
        trace = None
    elif trace is not None:
        trace.pop("archive_members", None)
    payload["candidate_trace"] = trace
    return {
        "selected_sha256": hashed(selected.model_dump(mode="json")),
        "file_sha256": hashed(payload),
        "text_sha256": digest((file.text or "").encode()),
        "text_chars": len(file.text or ""),
        "selection_sha256": hashed(file.selection.model_dump(mode="json")),
        "selection_state": file.selection.state,
        "rejected_sha256": hashed(file.selection.rejected),
        "parts": len(file.selection.parts),
    }


def physical(loader: TextLoader) -> tuple[dict[str, Any], dict[str, list[PhysicalAssemblyPacket]]]:
    records: list[dict[str, Any]] = []
    packets: dict[str, list[PhysicalAssemblyPacket]] = {}
    recipes: list[dict[str, Any]] = []
    for ordered in loader.read_physical_observations():
        observation = ordered.observation
        packet = observation.assembly
        artifact = observation.artifact.sha256 if observation.artifact is not None else None
        row: dict[str, Any] = {
            "order": list(ordered.order),
            "reused": ordered.reused,
            "artifact": artifact,
            "unavailable": observation.unavailable,
            "failure": observation.failure_class,
        }
        if isinstance(packet, PhysicalAssemblyPacket):
            packets.setdefault(packet.capture.artifact_sha256, []).append(packet)
            metadata = tuple(item for item in packet.metadata if item.kind != "recipe")
            row.update(
                inventory=packet.capture.inventory_id,
                execution=packet.capture.execution_id,
                metadata_sha256=hashed([item.model_dump(mode="json") for item in metadata]),
            )
            recipes.append(
                {
                    "artifact": artifact,
                    "capture_id": packet.capture.capture_id,
                    "recipe_id": packet.capture.recipe_id,
                    "extraction_id": packet.capture.extraction_id,
                }
            )
        else:
            row["assembly"] = "gap" if packet is not None else "missing"
        records.append(row)
    extractions, origins = [], []
    for result in loader.read_extraction_results():
        value = result.model_dump(mode="json")
        origins.append(
            {
                "artifact": result.artifact_sha256,
                "result_id": result.result_id,
                "policy_source": value.pop("package_policy_source_sha256"),
                "component_sources": [
                    item.pop("source_file_sha256", None) for item in value["components"]
                ],
            }
        )
        extractions.append(value)
    return {
        "observations": records,
        "extractions_sha256": hashed(extractions),
        "extraction_options": [
            item.options.model_dump(mode="json") for item in loader.read_extraction_results()
        ],
        "recipes": recipes,
        "extraction_origins": origins,
    }, packets


def verify_members(
    trace: CandidateDispositionTrace | None,
    packets: dict[str, list[PhysicalAssemblyPacket]],
    bodies: dict[str, bytes],
) -> dict[str, Any]:
    if trace is None or trace.archive_members is None:
        return {"status": "absent", "checked": 0, "unknown": 0}
    restored = CandidateDispositionTrace.model_validate_json(
        trace.model_dump_json(exclude_defaults=True)
    )
    section = restored.archive_members
    assert section is not None, "the validated trace retains its supplied archive section"
    checked = unknown = 0
    for row in section.rows:
        if row.association == "unknown":
            unknown += 1
            continue
        root, path = row.root_sha256, row.ordinals
        assert root is not None and path is not None, (
            "A known association requires root and ordinal path"
        )
        if root not in bodies or root not in packets:
            raise ValueError(
                "Claimed archive root lacks independent input and typed physical capture"
            )
        matches = []
        leaf_matches = []
        for packet in packets[root]:
            metadata = {item.metadata_id: item for item in packet.metadata}
            order = metadata[packet.capture.execution_id].metadata
            if not isinstance(order, PhysicalRootOrder):
                continue
            for index, (_, _, slot, execution_id) in enumerate(order.contexts):
                current, chain = index, []
                while order.contexts[current][0] is not None:
                    parent, ordinal, _, _ = order.contexts[current]
                    assert parent is not None and ordinal is not None, (
                        "typed nonroot context requires its parent occurrence"
                    )
                    chain.append(ordinal)
                    current = parent
                context_path = tuple(reversed(chain))
                if context_path == path and (
                    row.physical_part_index is None or slot == row.physical_part_index
                ):
                    leaf_matches.append((order, index))
                if row.stage == "walk" and context_path == path[:-1]:
                    execution = metadata[execution_id].metadata
                    if (
                        isinstance(execution, PhysicalContainerExecution)
                        and execution.route == "archive"
                        and path[-1] < len(execution.statuses)
                        and execution.statuses[path[-1]] == row.reason
                    ):
                        matches.append((order, index))
                elif (
                    row.stage == "policy"
                    and context_path == path
                    and (row.physical_part_index is None or slot == row.physical_part_index)
                ):
                    matches.append((order, index))
        if not matches:
            raise ValueError(
                "Archive association differs from actual typed ordinal/slot/status metadata"
            )
        physical_matches = [
            (order, index)
            for order, index in leaf_matches
            if order.contexts[index][2] is not None
            and any(context == index for context, _, _ in order.physical)
        ]
        if row.physical_part_index is not None and not physical_matches:
            raise ValueError("Archive physical slot differs from the actual extracted leaf")
        if row.stage == "policy" and not physical_matches:
            raise ValueError("Archive policy association is not an actual extracted leaf")
        parent_data = archive_leaf(bodies[root], path[:-1])
        with zipfile.ZipFile(io.BytesIO(parent_data)) as archive:
            if path[-1] >= len(archive.infolist()):
                raise ValueError("Claimed archive ordinal is absent from the raw parent header")
        if (
            row.leaf_sha256 is not None
            and digest(archive_leaf(bodies[root], path)) != row.leaf_sha256
        ):
            raise ValueError("Archive leaf hash differs from actual raw ordinal payload")
        if row.text_sha256 is not None and not any(
            any(context == index and text == row.text_sha256 for context, text, _ in order.physical)
            for order, index in physical_matches
        ):
            raise ValueError("Archive text hash differs from the captured physical leaf")
        checked += 1
    return {
        "status": "recorded",
        "checked": checked,
        "unknown": unknown,
        "section": section.model_dump(mode="json"),
        "trace_id": restored.trace_id,
    }


class ReplayPhysicalCollector(PhysicalObservationCollector):
    def __init__(self, ordering: ObservationOrder, events: list[dict[str, str | None]]) -> None:
        super().__init__(ordering)
        self.events = events

    def cached(self, observation: PhysicalWalkObservation | None) -> None:
        artifact = observation.artifact if observation is not None else None
        self.events.append(
            {"event": "cache", "artifact": artifact.sha256 if artifact is not None else None}
        )
        super().cached(observation)


def observe(inputs: dict[str, bytes], documents: list[tuple[str, TextDocument]]) -> dict[str, Any]:
    calls: list[str] = []
    io_events: list[dict[str, str | None]] = []

    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        calls.append(hashed(url))
        io_events.append({"event": "download", "url_sha256": hashed(url)})
        data = inputs[url]
        if max_bytes is not None and len(data) > max_bytes:
            raise ValueError("Frozen replay root exceeds download budget")
        return data

    def factory(ordering: ObservationOrder) -> PhysicalObservationCollector:
        return ReplayPhysicalCollector(ordering, io_events)

    extractor = DocumentTextExtractor(
        PypdfTextExtractor(),
        DocxTextExtractor(),
        DocTextExtractor(),
        max_member_bytes=OPTIONS["max_member_bytes"],
        max_part_chars=OPTIONS["max_part_chars"],
    )
    loader = TextLoader(
        {"replay.test": download},
        extractor,
        max_bytes=OPTIONS["max_bytes"],
        cache_size=OPTIONS["cache_size"],
        physical_factory=factory,
    )
    rows: dict[str, dict[str, Any]] = {}
    traces: dict[str, CandidateDispositionTrace | None] = {}
    for identity, requested in documents:
        for repeat in range(2):
            key = identity + f"/{repeat}"
            try:
                selected, loaded = loader.select_document(requested)
            except Exception as error:
                rows[key] = {"error": type(error).__name__}
            else:
                rows[key] = file_core(selected, loaded)
                source = loader.capture_source_read(requested, selected, loaded)
                rows[key]["source_capture"] = {
                    "kind": type(source).__name__,
                    "artifacts_sha256": hashed(
                        [item.model_dump(mode="json") for item in source.artifacts]
                    ),
                    "reads_sha256": hashed([item.model_dump(mode="json") for item in source.reads]),
                    "selection_id": source.selection.selection_id
                    if isinstance(source, DocumentSourceRead)
                    else None,
                }
                traces[key] = loaded.candidate_trace
    facts, packets = physical(loader)
    recipes = {"physical": facts.pop("recipes"), "extraction": facts.pop("extraction_origins")}
    bodies = {digest(data): data for data in inputs.values()}
    members = {key: verify_members(trace, packets, bodies) for key, trace in traces.items()}
    diagnostics = loader.read_physical_diagnostics()
    return {
        "core": {
            "outcomes": rows,
            "downloads": calls,
            "io_events": io_events,
            "metrics": loader.read_metrics().model_dump(mode="json"),
            "physical": facts,
            "physical_diagnostics": diagnostics.model_dump(mode="json")
            if diagnostics is not None
            else None,
        },
        "archive_members": members,
        "code_recipe_origins": recipes,
    }


def run(
    corpus: Path, cohort_path: Path, output: Path, *, baseline: Path | None = None
) -> dict[str, Any]:
    root, output = corpus.resolve(strict=True), output.resolve()
    if output.is_relative_to(root) or output.exists():
        raise ValueError("Output must be new and outside the corpus")
    cohort_raw = read_artifact(cohort_path)
    cohort = json.loads(cohort_raw)
    if (
        cohort["index_sha256_before"] != INDEX_SHA256
        or cohort["index_sha256_after"] != INDEX_SHA256
    ):
        raise ValueError("Cohort INDEX pin differs from this replay version")
    reads, source_before = CorpusReads(root), source_fingerprints()
    data, documents = {}, []
    with reads.guard():
        if digest((root / "INDEX.json").read_bytes()) != INDEX_SHA256:
            raise ValueError("Corpus INDEX changed")
        for case in cohort["cases"]:
            raw = case["raw"]
            body = (root / raw["path"]).read_bytes()
            if digest(body) != raw["sha256"] or len(body) != raw["bytes"]:
                raise ValueError("Pinned archive bytes changed")
            identity = "natural_" + str(case["id"])
            url = "https://replay.test/" + identity
            data[url] = body
            documents.append((identity, TextDocument(url=url, kind="rcl")))
        if len(documents) >= 2:
            first, second = documents[0][1], documents[1][1]
            documents.append(
                (
                    "controlled_natural_outer_pair",
                    first.model_copy(update={"compare_versions": True, "alternatives": (second,)}),
                )
            )
        natural = observe(data, documents)
        synthetic = synthetic_inputs()
        synthetic_data = {"https://replay.test/" + key: value for key, value in synthetic.items()}
        controls = [
            (key, TextDocument(url="https://replay.test/" + key, kind="rcl")) for key in synthetic
        ]
        controlled = observe(synthetic_data, controls)
    changed = reads.changed_inputs()
    if changed or source_before != source_fingerprints():
        raise ValueError("Replay inputs or source changed during observation")
    if baseline is None and any(
        item["status"] != "absent"
        for sample in (natural, controlled)
        for item in sample["archive_members"].values()
    ):
        raise ValueError("Baseline requires archive producer OFF")
    result: dict[str, Any] = {
        "version": VERSION,
        "driver_sha256": source_before["tools/replay_archive_dispositions.py"],
        "status": "recorded",
        "cohort_sha256": digest(cohort_raw),
        "index_sha256": INDEX_SHA256,
        "options": OPTIONS,
        "natural": natural,
        "synthetic": controlled,
        "synthetic_raw_sha256": {key: digest(value) for key, value in synthetic.items()},
        "source_sha256": source_before,
        "read_inputs": reads.initial_sha256,
        "changed_inputs": changed,
        "scope": {
            "natural_roots": len(data),
            "natural_requests": len(documents),
            "natural_bytes": sum(map(len, data.values())),
            "not_zip_census": True,
        },
        "exclusions": [
            "Bare singleton failures have no source trace; error classes remain core observations.",
            "Public source capture runs; no SQL persistence or paid/provider/Telegram behavior.",
            "No semantic gold; archive checks are mechanical, not policy approval.",
            "No independent full canonical-row hash proof for omitted archive row tails.",
        ],
    }
    if baseline is not None:
        previous = json.loads(read_artifact(baseline))
        for key in (
            "version",
            "driver_sha256",
            "cohort_sha256",
            "index_sha256",
            "options",
            "read_inputs",
            "synthetic_raw_sha256",
        ):
            if previous[key] != result[key]:
                raise ValueError("Paired replay scope/pins/options differ")
        result["comparison"] = {
            name: previous[name]["core"] == result[name]["core"]
            for name in ("natural", "synthetic")
        }
        if not all(result["comparison"].values()):
            raise ValueError("Archive writer changed legacy core outcomes or physical IO")
    write_json(output, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    result = run(args.corpus, args.cohort, args.output, baseline=args.baseline)
    print(json.dumps({"status": result["status"], "scope": result["scope"]}))


if __name__ == "__main__":
    main()
