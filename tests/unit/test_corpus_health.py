import gzip
import json
from pathlib import Path

import pytest
from tools.corpus_health import inspect, run

from tests.harness import rcl_document, rcl_folder, rcl_project, rcl_stage


def corpus_at(root: Path) -> Path:
    (root / "documents/files").mkdir(parents=True)
    (root / "documents/text").mkdir()
    (root / "corpus").mkdir()
    records = {
        "1": {"extension": "pdf", "url": "https://source.test/dokument1.pdf"},
        "2": {"extension": "docx", "url": "https://source.test/dokument2.docx"},
        "3": {"extension": "pdf"},
        "4": {"extension": "pdf"},
    }
    (root / "documents/index.json").write_text(json.dumps(records))
    for identity in ("1", "3", "4"):
        (root / f"documents/files/{identity}.pdf").write_bytes(b"raw")
    (root / "documents/text/2.txt.gz").write_bytes(gzip.compress(b"Text"))
    (root / "documents/text/3.txt.gz").write_bytes(gzip.compress(b" \n"))
    (root / "documents/text/4.txt.gz").write_bytes(b"not gzip")
    return root


def test_health_separates_downloads_from_local_reextraction_and_empty_text(tmp_path: Path) -> None:
    root = corpus_at(tmp_path / "corpus")

    result = inspect(root, check_text=True)

    tasks = {(row["id"], row["action"]): row for row in result["actions"]}
    assert tasks["1", "reextract_text"]["network_required"] is False
    assert tasks["2", "fetch_source"]["network_required"] is True
    assert tasks["2", "fetch_source"]["urls"] == ["https://source.test/dokument2.docx"]
    assert tasks["3", "review_empty_text"]["network_required"] is False
    assert tasks["4", "reextract_text"]["network_required"] is False
    assert result["counts"] == {
        "raw_present": 3,
        "raw_missing": 1,
        "text_missing": 1,
        "text_readable": 1,
        "text_empty": 1,
        "text_unreadable": 1,
    }
    assert result["read_inputs"]["documents/index.json"]


def test_candidate_gaps_include_source_urls_and_historical_case_without_changing_inputs(
    tmp_path: Path,
) -> None:
    root = corpus_at(tmp_path / "corpus")
    project = rcl_project(
        stages=(
            rcl_stage(
                1,
                "Projekt",
                "reached",
                rcl_folder(
                    1,
                    "Projekt",
                    rcl_document(999, "Projekt ustawy dokument999999.docx"),
                    rcl_document(1000, "Autopoprawka.docx"),
                ),
            ),
        )
    )
    (root / f"corpus/{project.id}.json").write_text(project.model_dump_json())
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    output = tmp_path / "health.json"

    run(root, output)

    result = json.loads(output.read_bytes())
    assert result["candidate_cohort"] == {"projects": 1, "stages": 1, "cases": 1}
    assert "999999" not in result["records"]
    missing = result["records"]["1000"]
    assert missing["raw_state"] == missing["text_state"] == "missing"
    assert missing["references"][0]["case"] == f"{project.id}/1"
    assert missing["urls"] and missing["urls"][0].endswith("dokument1000.docx")
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
    assert result["records"]["4"]["text_state"] == "present_unchecked"
    with pytest.raises(ValueError, match="new and outside"):
        run(root, output)
    with pytest.raises(ValueError, match="new and outside"):
        run(root, root / "output.json")


@pytest.mark.parametrize(
    "relative", ["documents/index.json", "documents/files/1.pdf", "documents/text/2.txt.gz"]
)
def test_health_rejects_symlinks_without_reading_an_outside_file(
    tmp_path: Path,
    relative: str,
) -> None:
    root = corpus_at(tmp_path / "corpus")
    outside = tmp_path / "private"
    outside.write_bytes(b"not a corpus input")
    alias = root / relative
    alias.unlink()
    alias.symlink_to(outside)

    with pytest.raises(ValueError, match="without symlinks"):
        inspect(root, check_text=True)


def test_invalid_document_identity_cannot_escape_the_corpus(tmp_path: Path) -> None:
    root = corpus_at(tmp_path / "corpus")
    (root / "documents/index.json").write_text('{"1": {"extension": "../../private"}}')

    with pytest.raises(ValueError, match="identity or extension"):
        inspect(root)
