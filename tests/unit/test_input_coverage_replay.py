import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from tools import replay_input_coverage
from tools.replay_pdf_pages import PdfReplayInput, PdfReplayManifest

from lexinform.sections import BudgetedText, TextBudget
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
