import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from tools import replay_input_coverage
from tools.replay_pdf_pages import PdfReplayInput, PdfReplayManifest

from lexinform.models import DocumentPart, DocumentSelection, PreparedTextInput
from lexinform.prepared_input import prepared_text_input
from lexinform.sections import PAGE_BREAK, BudgetedText, TextBudget
from lexinform.text_projection import SourcePiece, project_pieces
from tests.conftest import FIXTURES
from tests.unit.test_physical_extraction import archive, docx


def inputs(tmp_path: Path) -> tuple[Path, Path, str, Path, Path]:
    corpus = tmp_path / "corpus"
    files = corpus / "documents/files"
    files.mkdir(parents=True)
    index = corpus / "INDEX.json"
    index.write_text("{}")
    word = files / "sample.docx"
    word.write_bytes(docx("USTAWA Art. 1. Przepis. Ustawa wchodzi w życie."))
    package = files / "sample.zip"
    package.write_bytes(archive({"projekt.docx": word.read_bytes()}))
    pdf = corpus / "sample.pdf"
    pdf.write_bytes((FIXTURES / "print_3039.pdf").read_bytes())
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "suite": "external",
                "status": "recorded",
                "requested_profiles": ["extraction"],
                "observations": {
                    "word/sample.docx": {
                        "actual": {"sha256": hashlib.sha256(word.read_bytes()).hexdigest()}
                    },
                    "documents/packages/sample.zip": {
                        "actual": {"sha256": hashlib.sha256(package.read_bytes()).hexdigest()}
                    },
                },
            }
        )
    )
    cases = tmp_path / "pdf-cases.json"
    cases.write_text(
        PdfReplayManifest(
            version=1,
            index_sha256=hashlib.sha256(index.read_bytes()).hexdigest(),
            cases=(
                PdfReplayInput(
                    path="sample.pdf",
                    sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),
                    official_ids=("druk:3039",),
                    cohorts=("regression",),
                ),
            ),
            exclusions=("Synthetic inventory around real PDF and generated Word bytes.",),
        ).model_dump_json()
    )
    return corpus, catalog, hashlib.sha256(catalog.read_bytes()).hexdigest(), cases, word


def test_replay_checks_projection_round_trip_without_changing_prepared_text(tmp_path: Path) -> None:
    corpus, catalog, sha, cases, _ = inputs(tmp_path)
    before, after = tmp_path / "before.json", tmp_path / "after.json"

    assert replay_input_coverage.run(corpus, catalog, sha, cases, before, mode="baseline") == 0
    assert (
        replay_input_coverage.run(
            corpus,
            catalog,
            sha,
            cases,
            after,
            mode="compare",
            reference=before,
            reference_sha256=hashlib.sha256(before.read_bytes()).hexdigest(),
        )
        == 0
    )

    result = json.loads(after.read_bytes())
    assert result["status"] == "passed" and not result["core_changes"] and not result["errors"]
    assert result["coverage_changes"] == ["sample.pdf"]
    assert len(result["observations"]) == 3
    assert all(row["mapping"]["round_trip"] for row in result["observations"].values())
    assert all(len(row["mapping"]["variants"]) == 8 for row in result["observations"].values())
    assert not result["changed_code"] and not result["changed_inputs"]


def test_changed_bytes_fail_even_when_the_document_still_extracts(tmp_path: Path) -> None:
    corpus, catalog, sha, cases, word = inputs(tmp_path)
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    assert replay_input_coverage.run(corpus, catalog, sha, cases, before, mode="baseline") == 0
    word.write_bytes(word.read_bytes() + b"\n")

    assert (
        replay_input_coverage.run(
            corpus,
            catalog,
            sha,
            cases,
            after,
            mode="compare",
            reference=before,
            reference_sha256=hashlib.sha256(before.read_bytes()).hexdigest(),
        )
        == 1
    )

    result = json.loads(after.read_bytes())
    assert result["status"] == "failed"
    assert "SHA mismatch" in result["errors"]["documents/files/sample.docx"]


def test_optional_prepared_manifests_keep_every_existing_replay_field(tmp_path: Path) -> None:
    corpus, catalog, sha, cases, word = inputs(tmp_path)
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    data = word.read_bytes()
    plain = replay_input_coverage.snapshot(data, mapped=True)
    detailed = replay_input_coverage.snapshot(data, mapped=True, prepared_manifest=True)
    manifests = detailed.pop("prepared_inputs")
    assert detailed == plain and len(manifests) == 8
    for row in manifests.values():
        manifest = PreparedTextInput.model_validate(row["manifest"])
        assert manifest.input_id == row["input_id"] and manifest.mapping == "exact"
        assert manifest.physical_origins == "unmapped"
    assert replay_input_coverage.run(corpus, catalog, sha, cases, before, mode="baseline") == 0

    assert (
        replay_input_coverage.run(
            corpus,
            catalog,
            sha,
            cases,
            after,
            mode="compare",
            reference=before,
            reference_sha256=hashlib.sha256(before.read_bytes()).hexdigest(),
            prepared_manifest=True,
            include_coverage_views=True,
        )
        == 0
    )

    result = json.loads(after.read_bytes())
    assert result["status"] == "passed" and not result["errors"]
    assert all(len(row["prepared_inputs"]) == 8 for row in result["observations"].values())
    assert all(
        row["coverage_views"]["emitted"]["status"] == "prepared"
        for row in result["observations"].values()
    )


def test_prepared_manifests_require_explicit_mapping(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="mapped replay"):
        replay_input_coverage.snapshot(b"", mapped=False, prepared_manifest=True)
    with pytest.raises(ValueError, match="mapped compare"):
        replay_input_coverage.run(
            tmp_path,
            tmp_path / "absent",
            "0" * 64,
            tmp_path / "absent",
            tmp_path / "output.json",
            mode="baseline",
            prepared_manifest=True,
        )
    assert not (tmp_path / "output.json").exists()


def test_replay_cannot_compare_an_unpinned_reference(tmp_path: Path) -> None:
    corpus, catalog, sha, cases, _ = inputs(tmp_path)
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    assert replay_input_coverage.run(corpus, catalog, sha, cases, before, mode="baseline") == 0

    with pytest.raises(ValueError, match="Reference SHA mismatch"):
        replay_input_coverage.run(
            corpus,
            catalog,
            sha,
            cases,
            after,
            mode="compare",
            reference=before,
            reference_sha256="0" * 64,
        )

    assert not after.exists()


def test_replay_rejects_wrong_budget_origin_despite_an_unchanged_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus, catalog, sha, cases, _ = inputs(tmp_path)
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    assert replay_input_coverage.run(corpus, catalog, sha, cases, before, mode="baseline") == 0
    original = TextBudget.apply

    def wrong_origin(
        self: TextBudget, text: str, spans: Sequence[tuple[int, int]] = ()
    ) -> BudgetedText:
        result = original(self, text, spans)
        return BudgetedText(
            result.text,
            result.truncated,
            project_pieces("FOREIGN " + text, (SourcePiece(result.text),)),
        )

    monkeypatch.setattr(TextBudget, "apply", wrong_origin)

    assert (
        replay_input_coverage.run(
            corpus,
            catalog,
            sha,
            cases,
            after,
            mode="compare",
            reference=before,
            reference_sha256=hashlib.sha256(before.read_bytes()).hexdigest(),
        )
        == 1
    )
    result = json.loads(after.read_bytes())
    assert result["status"] == "failed" and len(result["errors"]) == 3
    assert all("input differs" in error for error in result["errors"].values())


def test_runtime_extraction_replay_preserves_the_existing_prepared_input_contract(
    tmp_path: Path,
) -> None:
    corpus, catalog, sha, cases, word = inputs(tmp_path)
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    data = word.read_bytes()
    plain = replay_input_coverage.snapshot(data, mapped=True, prepared_manifest=True)
    detailed = replay_input_coverage.snapshot(
        data, mapped=True, prepared_manifest=True, extraction_manifest=True
    )
    extraction = detailed.pop("extraction")
    assert detailed == plain
    assert extraction["metadata_bytes"] < 16_384
    assert extraction["manifest"]["bodies"] == "not_retained"
    assert extraction["manifest"]["physical_coordinates"] == "manifest_not_retained"
    assert extraction["manifest"]["input_mapping"] == "unmapped"
    assert replay_input_coverage.run(corpus, catalog, sha, cases, before, mode="baseline") == 0

    assert (
        replay_input_coverage.run(
            corpus,
            catalog,
            sha,
            cases,
            after,
            mode="compare",
            reference=before,
            reference_sha256=hashlib.sha256(before.read_bytes()).hexdigest(),
            prepared_manifest=True,
            extraction_manifest=True,
        )
        == 0
    )

    result = json.loads(after.read_bytes())
    assert not result["core_changes"] and not result["errors"]
    assert all(row["extraction"]["result_id"] for row in result["observations"].values())


def test_runtime_extraction_replay_requires_a_pinned_compatibility_reference(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="pinned compare"):
        replay_input_coverage.run(
            tmp_path,
            tmp_path / "absent",
            "0" * 64,
            tmp_path / "absent",
            tmp_path / "output.json",
            mode="baseline",
            extraction_manifest=True,
        )
    assert not (tmp_path / "output.json").exists()


def test_optional_coverage_views_keep_existing_fields_and_extraction_scope() -> None:
    data = docx("USTAWA Art. 1. Przepis. Ustawa wchodzi w życie.")
    plain = replay_input_coverage.snapshot(data, mapped=True)
    detailed = replay_input_coverage.snapshot(data, mapped=True, include_coverage_views=True)
    report = detailed.pop("coverage_views")

    assert detailed == plain
    assert report["extraction"]["evidence"] == "observed_artifacts"
    assert report["extraction"]["artifacts"][0]["completeness"] == "not_asserted"
    assert report["selection"]["state"] == "unverified"
    assert len(report["emitted"]["variants"]) == 8


def test_emitted_unknown_mapping_keeps_source_selection_and_extraction_separate() -> None:
    selection = DocumentSelection(parts=(DocumentPart(role="bill", text="project"),))
    manifest = prepared_text_input(selection, "project", projection=None)
    report = replay_input_coverage.coverage_views(selection, inputs={"input": manifest})

    assert report["extraction"] == {"evidence": "unknown", "artifacts": []}
    assert report["selection"]["state"] == "unverified"
    assert report["emitted"]["status"] == "prepared"
    assert report["emitted"]["variants"]["input"]["chars_by_origin"] == {
        "source": 0,
        "generated": 0,
        "unknown": 7,
    }
    assert replay_input_coverage.coverage_views(selection)["emitted"]["status"] == "unknown"
    assert (
        replay_input_coverage.coverage_views(selection, inputs={})["emitted"]["status"]
        == "not_prepared"
    )
    with pytest.raises(ValueError, match="differs from its source selection"):
        replay_input_coverage.coverage_views(
            selection.model_copy(update={"question": "other"}), inputs={"input": manifest}
        )


def test_exact_emission_counts_only_the_part_that_was_copied() -> None:
    selection = DocumentSelection(
        parts=(
            DocumentPart(role="bill", text="repeated"),
            DocumentPart(role="justification", text="repeated"),
        ),
        completeness=("recorded source evidence",),
    )
    source = PAGE_BREAK.join(part.text for part in selection.parts)
    projection = project_pieces(source, (SourcePiece("repeated", len("repeated" + PAGE_BREAK)),))
    manifest = prepared_text_input(selection, projection.text, projection=projection)

    report = replay_input_coverage.coverage_views(selection, inputs={"input": manifest})

    assert report["selection"]["completeness"] == ["recorded source evidence"]
    emitted = report["emitted"]["variants"]["input"]
    assert emitted["mapping"] == "exact"
    assert emitted["copied_chars_by_part"] == [
        {"part_index": 1, "role": "justification", "chars": 8}
    ]
