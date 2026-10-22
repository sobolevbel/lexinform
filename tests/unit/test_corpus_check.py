import json
import shutil
import socket
from pathlib import Path

import pytest
from tools import corpus_check
from tools.corpus_check import CorpusCase, CorpusManifest

FIXTURE = Path(__file__).parents[1] / "fixtures" / "corpus_check"


def copy_corpus(tmp_path: Path) -> Path:
    target = tmp_path / "corpus"
    shutil.copytree(FIXTURE, target)
    return target


def write_manifest(corpus: Path, manifest: CorpusManifest) -> None:
    (corpus / "manifest.json").write_text(manifest.model_dump_json())


def test_ci_slice_runs_all_profiles_and_preserves_input_bytes(tmp_path: Path) -> None:
    before = {path.name: path.read_bytes() for path in FIXTURE.iterdir() if path.is_file()}
    output = tmp_path / "result.json"

    code = corpus_check.run(FIXTURE, output)

    result = json.loads(output.read_bytes())
    assert code == 0 and result["status"] == "passed"
    assert result["profiles"] == dict.fromkeys(corpus_check.PROFILES, "passed")
    assert len(result["cases"]) == 7
    assert all(case["effective_split"] == "regression" for case in result["cases"])
    assert result["exclusions"]
    assert len(result["code_sha"]) == 40
    assert len(result["source_sha256"]["src/lexinform/document_selection.py"]) == 64
    assert len(result["source_sha256"]["uv.lock"]) == 64
    assert before == {path.name: path.read_bytes() for path in FIXTURE.iterdir() if path.is_file()}


def test_runner_refuses_to_replace_results_or_write_into_the_corpus(tmp_path: Path) -> None:
    output = tmp_path / "result.json"
    output.write_text("previous evidence")

    with pytest.raises(ValueError, match="new file outside"):
        corpus_check.run(FIXTURE, output)
    with pytest.raises(ValueError, match="new file outside"):
        corpus_check.run(FIXTURE, FIXTURE / "result.json")

    assert output.read_text() == "previous evidence"


def test_missing_corpus_is_not_a_successful_empty_gate(tmp_path: Path) -> None:
    output = tmp_path / "result.json"

    code = corpus_check.run(tmp_path / "absent", output)

    assert code == 2
    assert json.loads(output.read_bytes())["status"] == "not_run"


@pytest.mark.parametrize("missing", [False, True])
def test_missing_and_changed_inputs_are_explicit_failures(tmp_path: Path, missing: bool) -> None:
    corpus = copy_corpus(tmp_path)
    source = corpus / "rcl_779502.docx"
    if missing:
        source.unlink()
    else:
        source.write_bytes(b"changed source")
    output = tmp_path / "result.json"

    code = corpus_check.run(corpus, output, ("extraction",))

    result = json.loads(output.read_bytes())
    assert code == (2 if missing else 1)
    assert result["profiles"]["extraction"] == ("not_run" if missing else "failed")
    assert result["cases"][0]["reason"]


def test_requested_profile_without_cases_is_not_run(tmp_path: Path) -> None:
    corpus = copy_corpus(tmp_path)
    manifest = corpus_check.load_manifest(corpus)
    cases = tuple(case for case in manifest.cases if case.profile != "comparison")
    write_manifest(corpus, manifest.model_copy(update={"cases": cases}))
    output = tmp_path / "result.json"

    code = corpus_check.run(corpus, output, ("comparison",))

    assert code == 2
    assert json.loads(output.read_bytes())["profiles"] == {"comparison": "not_run"}


def test_related_cases_cannot_cross_tuning_and_holdout(tmp_path: Path) -> None:
    corpus = copy_corpus(tmp_path)
    manifest = corpus_check.load_manifest(corpus)
    cases = tuple(
        case.model_copy(update={"split": "holdout"}) if index == 0 else case
        for index, case in enumerate(manifest.cases)
    )
    write_manifest(corpus, manifest.model_copy(update={"cases": cases}))

    with pytest.raises(ValueError, match="crosses dataset splits"):
        corpus_check.load_manifest(corpus)


def test_machine_labels_are_not_an_independent_holdout(tmp_path: Path) -> None:
    corpus = copy_corpus(tmp_path)
    manifest = corpus_check.load_manifest(corpus)
    first = manifest.cases[0].model_copy(update={"split": "holdout", "oracle": "machine_baseline"})
    write_manifest(corpus, manifest.model_copy(update={"cases": (first,)}))

    with pytest.raises(ValueError, match="independent holdout"):
        corpus_check.load_manifest(corpus)


def test_source_relations_keep_related_initiatives_in_one_split(tmp_path: Path) -> None:
    corpus = copy_corpus(tmp_path)
    manifest = corpus_check.load_manifest(corpus)
    cases = tuple(
        case.model_copy(update={"initiative": "different-label", "split": "holdout"})
        if index == 0
        else case
        for index, case in enumerate(manifest.cases)
    )
    write_manifest(corpus, manifest.model_copy(update={"cases": cases}))

    with pytest.raises(ValueError, match="crosses dataset splits"):
        corpus_check.load_manifest(corpus)


def test_inputs_cannot_escape_the_corpus(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="inside corpus"):
        corpus_check.source_file(tmp_path, "../outside")


def test_unknown_expected_field_is_a_mismatch_even_when_expected_is_null() -> None:
    manifest = corpus_check.load_manifest(FIXTURE)
    case = manifest.cases[1].model_copy(update={"expected": {"unknown_output": None}})

    result = corpus_check.check_case(FIXTURE, case)

    assert result["status"] == "failed" and result["mismatches"] == ["unknown_output"]


def test_consumed_holdout_is_recorded_as_regression(tmp_path: Path) -> None:
    manifest = corpus_check.load_manifest(FIXTURE)
    case = manifest.cases[1].model_copy(update={"split": "holdout"})

    result = corpus_check.check_case(FIXTURE, case)

    assert result["status"] == "passed" and result["effective_split"] == "regression"
    assert case.split == "holdout"


def test_symlink_cannot_bypass_the_corpus_boundary(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    target = tmp_path / "outside.txt"
    target.write_text("outside")
    (corpus / "linked.txt").symlink_to(target)

    with pytest.raises(ValueError, match="inside corpus"):
        corpus_check.source_file(corpus, "linked.txt")


def test_runner_blocks_network_connections(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def attempt_network(case: CorpusCase, inputs: list[bytes]) -> dict[str, object]:
        socket.create_connection(("127.0.0.1", 9))
        return {}

    monkeypatch.setattr(corpus_check, "evaluate", attempt_network)
    output = tmp_path / "result.json"

    code = corpus_check.run(FIXTURE, output, ("extraction",))

    assert code == 1
    assert "prohibit network" in json.loads(output.read_bytes())["cases"][0]["reason"]
