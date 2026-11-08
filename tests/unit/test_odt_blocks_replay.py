import json
import socket
from pathlib import Path
from typing import Any, Never

import pytest
from tools import replay_odt_blocks
from tools.corpus_artifacts import digest
from tools.corpus_manifests import RawReplayInput, RawReplayManifest
from tools.replay_odt_blocks import run, snapshot, verify_manifest

from lexinform.adapters.document_text import OdtTextExtractor
from lexinform.models import OdtIgnoredNode, OdtTextBlock
from tests.unit.test_odt_blocks import odt_document, package


def population(tmp_path: Path, files: dict[str, bytes]) -> tuple[Path, Path]:
    root = tmp_path / "corpus"
    root.mkdir()
    index = b'{"fixture":true}'
    (root / "INDEX.json").write_bytes(index)
    for name, raw in files.items():
        (root / name).write_bytes(raw)
    manifest = RawReplayManifest(
        version=1,
        index_sha256=digest(index),
        cases=tuple(
            RawReplayInput(
                path=name, sha256=digest(raw), official_ids=("fixture",), cohorts=("odt",)
            )
            for name, raw in files.items()
        ),
        exclusions=("Synthetic regression fixture; no independent legal oracle.",),
    )
    cases = tmp_path / "cases.json"
    cases.write_text(manifest.model_dump_json())
    return root, cases


def read_report(path: Path) -> dict[str, Any]:
    report: dict[str, Any] = json.loads(path.read_bytes())
    return report


def test_replay_compares_odt_archive_members_and_records_negative_controls(tmp_path: Path) -> None:
    raw = odt_document("<text:p>Termin: 31 grudnia.</text:p>")
    nested = package({"odd.pdf": raw, "same.odt": raw, "ignored.pdf": b"%PDF-1.4"})
    root, cases = population(tmp_path, {"nested.zip": nested, "negative.zip": package({})})
    baseline, current = tmp_path / "baseline.json", tmp_path / "current.json"

    assert run(root, cases, baseline, mode="library") == 0
    assert (
        run(
            root,
            cases,
            current,
            mode="adapter",
            reference=baseline,
            reference_sha256=digest(baseline.read_bytes()),
        )
        == 0
    )

    before, after = read_report(baseline), read_report(current)
    assert before["status"] == "recorded" and after["status"] == "passed"
    assert after["observations"] == before["observations"]
    assert not after["errors"] and not after["changed_inputs"] and not after["changes"]
    physical = after["physical"]["nested.zip"]
    assert [item["members"] for item in physical] == [["odd.pdf"], ["same.odt"]]
    assert all(item["verified_coordinates"] == 1 for item in physical)
    assert not after["physical"]["negative.zip"]
    assert after["independent_legal_oracle"] is False
    assert all("text" not in part for part in after["observations"]["nested.zip"]["parts"])


@pytest.mark.parametrize("kind", ["missing", "unsupported", "malformed"])
def test_unreadable_odt_states_preserve_legacy_observations(kind: str) -> None:
    raw = (
        package({"mimetype": b"odt", "content.xml": b"<bad"})
        if kind == "malformed"
        else odt_document("", missing_body=kind == "missing", doctype=kind == "unsupported")
    )

    baseline, _ = snapshot(raw, "library")
    current, physical = snapshot(raw, "adapter")

    assert baseline == current
    if kind == "malformed":
        assert current["attempts"][0]["error_type"] == "ParseError"
        assert not physical
    else:
        assert physical[0]["body_state"] == kind
        assert physical[0]["verified_coordinates"] == 0


@pytest.mark.parametrize("fault", ["hash", "input", "path", "tag", "ignored"])
def test_independent_xml_validation_rejects_incorrect_manifest(fault: str) -> None:
    raw = odt_document("<text:p>A</text:p><text:h>B</text:h><text:unknown/>")
    extracted = OdtTextExtractor().extract_odt_blocks(raw)
    if fault == "hash":
        invalid = extracted.model_copy(update={"xml_sha256": "0" * 64})
    elif fault == "input":
        invalid = extracted.model_copy(update={"input_sha256": "0" * 64})
    elif fault == "ignored":
        invalid = extracted.model_copy(
            update={"ignored_nodes": (OdtIgnoredNode(path=(2,), tag="incorrect"),)}
        )
    else:
        invalid = extracted.model_copy(
            update={
                "blocks": (
                    OdtTextBlock(
                        kind="paragraph", path=(99,) if fault == "path" else (1,), text="A"
                    ),
                ),
                "legacy_blocks": ("odt-xml:content.xml:body:99",)
                if fault == "path"
                else ("odt-xml:content.xml:body:1",),
            }
        )

    with pytest.raises((ValueError, IndexError)):
        verify_manifest(raw, invalid)


def test_all_non_odt_population_fails_instead_of_passing_empty_coverage(tmp_path: Path) -> None:
    root, cases = population(tmp_path, {"negative.zip": package({})})
    report = tmp_path / "report.json"

    assert run(root, cases, report, mode="library") == 1
    assert "population" in read_report(report)["errors"]


@pytest.mark.parametrize("fault", ["bytes", "index", "escape", "duplicate"])
def test_invalid_population_is_refused(tmp_path: Path, fault: str) -> None:
    raw = odt_document("<text:p>A</text:p>")
    root, cases = population(tmp_path, {"input.odt": raw})
    if fault == "bytes":
        (root / "input.odt").write_bytes(raw + b"changed")
    elif fault == "index":
        (root / "INDEX.json").write_bytes(b"changed")
    else:
        manifest = json.loads(cases.read_bytes())
        if fault == "escape":
            manifest["cases"][0]["path"] = "../input.odt"
        else:
            manifest["cases"].append(manifest["cases"][0])
        cases.write_text(json.dumps(manifest))
    report = tmp_path / "report.json"

    if fault in ("index", "duplicate"):
        with pytest.raises(ValueError):
            run(root, cases, report, mode="library")
    else:
        assert run(root, cases, report, mode="library") == 1
        assert read_report(report)["errors"]


def test_adapter_requires_exact_population_and_pinned_reference(tmp_path: Path) -> None:
    raw = odt_document("<text:p>A</text:p>")
    root, cases = population(tmp_path, {"input.odt": raw})
    baseline = tmp_path / "baseline.json"
    assert run(root, cases, baseline, mode="library") == 0
    for reference, sha in ((None, None), (baseline, "0" * 64)):
        with pytest.raises(ValueError):
            run(
                root,
                cases,
                tmp_path / "current.json",
                mode="adapter",
                reference=reference,
                reference_sha256=sha,
            )
    sha = digest(baseline.read_bytes())
    changed = json.loads(cases.read_bytes())
    changed["exclusions"].append("Additional exclusion")
    cases.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="population differs"):
        run(
            root,
            cases,
            tmp_path / "current.json",
            mode="adapter",
            reference=baseline,
            reference_sha256=sha,
        )


def test_meaningful_text_change_is_reported_even_with_same_archive_members(tmp_path: Path) -> None:
    raw = odt_document("<text:p>Kwota 100 zł.</text:p>")
    root, cases = population(tmp_path, {"input.odt": raw})
    baseline = tmp_path / "baseline.json"
    assert run(root, cases, baseline, mode="library") == 0
    modified = read_report(baseline)
    modified["observations"]["input.odt"]["attempts"][0]["text_sha256"] = "0" * 64
    baseline.write_text(json.dumps(modified))
    current = tmp_path / "current.json"

    assert (
        run(
            root,
            cases,
            current,
            mode="adapter",
            reference=baseline,
            reference_sha256=digest(baseline.read_bytes()),
        )
        == 1
    )
    assert read_report(current)["changes"] == ["input.odt"]


@pytest.mark.parametrize("operation", ["network", "write"])
def test_guard_refuses_network_and_input_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    raw = odt_document("<text:p>A</text:p>")
    root, cases = population(tmp_path, {"input.odt": raw})
    report = tmp_path / "report.json"

    def forbidden(data: bytes, mode: replay_odt_blocks.Mode) -> Never:
        if operation == "network":
            socket.create_connection(("127.0.0.1", 1))
        else:
            (root / "input.odt").write_bytes(b"changed")
        raise AssertionError("Replay missed a forbidden action")

    monkeypatch.setattr(replay_odt_blocks, "snapshot", forbidden)

    assert run(root, cases, report, mode="library") == 1
    assert (root / "input.odt").read_bytes() == raw
    message = read_report(report)["errors"]["input.odt"]
    assert ("prohibits network" if operation == "network" else "read-only") in message


def test_code_change_during_replay_fails_qualification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = odt_document("<text:p>A</text:p>")
    root, cases = population(tmp_path, {"input.odt": raw})
    report = tmp_path / "report.json"
    fingerprints = iter(({}, {"changed.py": "different"}))
    monkeypatch.setattr(replay_odt_blocks, "source_fingerprints", lambda: next(fingerprints))

    assert run(root, cases, report, mode="library") == 1
    assert read_report(report)["changed_code"]


def test_soft_page_breaks_bind_inline_span_and_report_ignored_body_marker() -> None:
    before = odt_document(
        "<text:p>ab<text:s text:c='2'/><text:span>ą<text:soft-page-break/>cd</text:span></text:p>"
        "<text:soft-page-break/>"
    )
    after = odt_document(
        "<text:p>ab<text:s text:c='2'/><text:span>ącd</text:span></text:p>"
        "<text:soft-page-break/><text:soft-page-break/>"
    )
    base, normal = snapshot(before, "adapter")
    opted, physical = snapshot(before, "adapter", soft_page_breaks=True)
    assert base == opted and "soft_page_breaks" not in normal[0]
    verified = physical[0]["soft_page_breaks"]
    assert verified["status"] == "verified" and verified["raw_count"] == 2
    assert verified["emitted"] == [
        {
            "path": (0, 1, 0),
            "block_path": (0,),
            "span": (5, 6),
            "block_text_sha256": digest("ab  ą\fcd".encode()),
        }
    ]
    assert verified["ignored_paths"] == [(1,)]
    _, physical = snapshot(after, "adapter", soft_page_breaks=True)
    verified = physical[0]["soft_page_breaks"]
    assert verified["raw_count"] == 2 and not verified["emitted"]
    assert verified["ignored_paths"] == [(1,), (2,)]


def test_soft_page_breaks_leave_unsupported_inline_shape_unverified() -> None:
    raw = odt_document("<text:p>text<text:unknown><text:soft-page-break/></text:unknown></text:p>")
    _, physical = snapshot(raw, "adapter", soft_page_breaks=True)
    result = physical[0]["soft_page_breaks"]
    assert result["status"] == "partial"
    assert result["unverified_paths"] == [(0, 0, 0)]
    assert not result["emitted"]


def test_replay_reports_declared_table_geometry_without_expanding_text() -> None:
    raw = odt_document(
        "<table:table><table:table-column table:number-columns-repeated='2'/>"
        "<table:table-row><table:table-cell table:number-rows-spanned='2'>"
        "<text:p>Art. 1</text:p></table:table-cell></table:table-row>"
        "<table:table-row><table:covered-table-cell/></table:table-row>"
        "<table:table-row table:number-rows-repeated='3'><table:table-cell>"
        "<text:p>Termin</text:p></table:table-cell></table:table-row></table:table>"
    )

    extracted = OdtTextExtractor().extract_odt_blocks(raw)

    assert extracted.legacy_text == "Art. 1\n\nTermin"
    assert verify_manifest(raw, extracted)["table_geometry"] == {
        "covered-table-cell": {"declared_nodes": 1, "nodes_with_blocks": 1},
        "number-columns-repeated": {"declared_nodes": 1, "nodes_with_blocks": 0},
        "number-rows-repeated": {"declared_nodes": 1, "nodes_with_blocks": 1},
        "number-rows-spanned": {"declared_nodes": 1, "nodes_with_blocks": 1},
    }


@pytest.mark.parametrize(
    ("second_row", "reason"),
    [
        (
            "<table:table-row><table:covered-table-cell/><table:covered-table-cell/></table:table-row>",
            None,
        ),
        ("<table:table-row><table:covered-table-cell/></table:table-row>", "missing_covered_cell"),
        ("<table:table-row table:number-rows-repeated='0'/>", "invalid_row_repeat"),
        ("<text:section/>", "unsupported_table_child"),
    ],
)
def test_replay_links_rectangular_merge_only_when_explicit_cells_match(
    second_row: str, reason: str | None
) -> None:
    raw = odt_document(
        "<table:table><table:table-header-rows><table:table-row>"
        "<table:table-cell table:number-rows-spanned='2' table:number-columns-spanned='2'>"
        "<text:p>Termin</text:p></table:table-cell><table:covered-table-cell/>"
        "</table:table-row></table:table-header-rows>"
        f"{second_row}</table:table>"
    )

    extracted = OdtTextExtractor().extract_odt_blocks(raw)
    (layout,) = verify_manifest(raw, extracted)["merged_tables"]

    if reason is not None:
        assert layout == {"table_path": (0,), "status": "unresolved", "reason": reason}
    else:
        assert layout == {
            "table_path": (0,),
            "status": "verified",
            "merges": [
                {
                    "anchor": (0, 0, 0, 0),
                    "logical_anchor": (0, 0),
                    "logical_covered": [(0, 1), (1, 0), (1, 1)],
                    "covered": [(0, 0, 0, 1), (0, 1, 0), (0, 1, 1)],
                }
            ],
        }


def test_replay_distinguishes_logical_positions_sharing_a_repeated_covered_cell() -> None:
    raw = odt_document(
        "<table:table><table:table-row table:number-rows-repeated='2'>"
        "<table:table-cell table:number-columns-repeated='4'/></table:table-row>"
        "<table:table-row><table:table-cell/>"
        "<table:table-cell table:number-rows-spanned='3' table:number-columns-spanned='2'>"
        "<text:p>Termin</text:p></table:table-cell><table:covered-table-cell/>"
        "</table:table-row><table:table-row table:number-rows-repeated='2'><table:table-cell/>"
        "<table:covered-table-cell table:number-columns-repeated='2'/>"
        "</table:table-row></table:table>"
    )

    extracted = OdtTextExtractor().extract_odt_blocks(raw)
    (layout,) = verify_manifest(raw, extracted)["merged_tables"]
    (merge,) = layout["merges"]

    assert merge["anchor"] == (0, 1, 1)
    assert merge["logical_anchor"] == (2, 1)
    assert merge["covered"] == [(0, 1, 2), *((0, 2, 1),) * 4]
    assert merge["logical_covered"] == [(2, 2), (3, 1), (3, 2), (4, 1), (4, 2)]


def test_replay_rejects_large_logical_table_without_expanding_it() -> None:
    raw = odt_document(
        "<table:table><table:table-row table:number-rows-repeated='9000'>"
        "<table:table-cell table:number-columns-repeated='9000'/>"
        "</table:table-row><table:table-row><table:covered-table-cell/>"
        "</table:table-row></table:table>"
    )
    extracted = OdtTextExtractor().extract_odt_blocks(raw)

    assert verify_manifest(raw, extracted)["merged_tables"] == [
        {"table_path": (0,), "status": "unresolved", "reason": "logical_table_limit"}
    ]
