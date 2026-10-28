import json
import tarfile
from pathlib import Path
from typing import Any

import pytest
from tools.corpus_artifacts import digest
from tools.corpus_runs import compare, execute, register, verify

from tests.unit.test_odt_blocks import odt_document
from tests.unit.test_odt_blocks_replay import population


def report_at(path: Path, *, value: int = 1, sha: str = "a") -> Path:
    path.write_text(
        json.dumps(
            {
                "status": "recorded",
                "records": {"1": {"decision": value}},
                "read_inputs": {"source": sha},
                "source_sha256": {"producer.py": "old-code"},
            }
        )
    )
    return path


def test_registration_preserves_original_provenance_after_the_source_artifact_is_lost(
    tmp_path: Path,
) -> None:
    source = report_at(tmp_path / "old.json")
    root = tmp_path / "runs"

    result = register(root, "old", "health", source)
    source.unlink()
    checked = verify(root / "old")

    assert result == checked and checked["status"] == "recorded"
    assert checked["producer"]["source_sha256"] == {"producer.py": "old-code"}
    with pytest.raises(FileExistsError):
        register(root, "old", "health", report_at(tmp_path / "new.json"))


def test_missing_and_tampered_registered_artifacts_are_reported(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    register(root, "old", "health", report_at(tmp_path / "old.json"))
    path = root / "old/result.json"
    original = path.read_bytes()
    path.write_bytes(original + b" ")
    with pytest.raises(ValueError, match="SHA/size"):
        verify(root / "old")
    path.unlink()
    with pytest.raises(FileNotFoundError):
        verify(root / "old")


def test_run_manifest_hash_and_path_boundaries_are_verified(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    register(root, "old", "health", report_at(tmp_path / "old.json"))
    path = root / "old/manifest.json"
    data = json.loads(path.read_bytes())
    data["artifacts"]["../outside"] = {"sha256": "a", "bytes": 1}
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="manifest SHA"):
        verify(root / "old")
    (root / "old/manifest.sha256").write_text(digest(path.read_bytes()))
    with pytest.raises(ValueError, match="escapes"):
        verify(root / "old")


def test_diff_separates_changed_observations_and_changed_input_identity(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    register(root, "before", "health", report_at(tmp_path / "before.json"))
    register(root, "after", "health", report_at(tmp_path / "after.json", value=2, sha="b"))

    result = compare(root / "before", root / "after", tmp_path / "diff.json")

    assert result["status"] == "changed"
    assert result["changes"] == [
        {"id": "1", "kind": "changed", "before": {"decision": 1}, "after": {"decision": 2}}
    ]
    assert result["changed_inputs"] == ["source"]
    with pytest.raises(ValueError, match="outside"):
        compare(root / "before", root / "after", root / "after/diff.json")


def test_real_offline_ci_suite_runs_inside_an_immutable_directory(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    corpus = Path(__file__).parents[1] / "fixtures/corpus_check"

    result = execute(root, "ci", "ci", corpus, [])

    assert result["status"] == "passed" and result["observations"] > 0
    assert verify(root / "ci") == result
    with tarfile.open(root / "ci/code.tar.gz") as snapshot:
        assert set(snapshot.getnames()) == set(result["started"]["source_sha256"])
        assert ".env" not in snapshot.getnames()
    assert result["started"]["command"][-1] == str((root / "ci/result.json").resolve())
    with pytest.raises(ValueError, match="outside"):
        execute(corpus / "runs", "wrong", "ci", corpus, [])


@pytest.mark.parametrize(
    "extra",
    [["--output=escape"], ["--out", "escape"], ["--cor", "escape"], ["--suite", "external"]],
)
def test_child_options_cannot_override_owned_paths_or_suite(
    tmp_path: Path, extra: list[str]
) -> None:
    with pytest.raises(ValueError, match="controlled"):
        execute(tmp_path / "runs", "bad", "ci", tmp_path / "corpus", extra)
    assert not (tmp_path / "runs").exists()


def test_empty_populations_do_not_become_completed_runs(tmp_path: Path) -> None:
    source = tmp_path / "empty.json"
    source.write_text('{"status":"recorded","records":{}}')

    result = register(tmp_path / "runs", "empty", "health", source)

    assert result["status"] == "not_run" and result["observations"] == 0


@pytest.mark.parametrize("flag", ["--kind", "--ki", "--kind=process"])
def test_suite_presets_cannot_be_overridden(tmp_path: Path, flag: str) -> None:
    with pytest.raises(ValueError, match="controlled"):
        execute(tmp_path / "runs", "bad", "senate", tmp_path / "corpus", [flag, "process"])
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize("kind", ["observation", "input", "population"])
@pytest.mark.parametrize("reverse", [False, True])
def test_diff_detects_presence_and_population_identity(
    tmp_path: Path, kind: str, reverse: bool
) -> None:
    runs = tmp_path / "runs"
    left: dict[str, Any] = {"status": "recorded", "observations": {"control": 1}}
    right: dict[str, Any] = {"status": "recorded", "observations": {"control": 1}}
    if kind == "observation":
        left["observations"]["unknown"] = None
    elif kind == "input":
        left["read_inputs"], right["read_inputs"] = {"unknown": None}, {}
    else:
        left["case_manifest_sha256"], right["case_manifest_sha256"] = "a" * 64, "b" * 64
    if reverse:
        left, right = right, left
    for name, report in (("before", left), ("after", right)):
        source = tmp_path / (name + ".json")
        source.write_text(json.dumps(report))
        register(runs, name, "health", source)

    result = compare(runs / "before", runs / "after", tmp_path / "diff.json")

    assert result["status"] == "changed"
    if kind == "observation":
        assert result["changes"] == [
            {
                "id": "unknown",
                "kind": "added" if reverse else "removed",
                "before": None,
                "after": None,
            }
        ]
    elif kind == "input":
        assert result["changed_inputs"] == ["unknown"]
    else:
        assert result["summary_changes"]["case_manifest_sha256"] == {
            "before": left["case_manifest_sha256"],
            "after": right["case_manifest_sha256"],
        }


def test_odt_replay_runs_verifies_and_compares_through_the_shared_registry(tmp_path: Path) -> None:
    raw = odt_document("<text:p>Kwota 100 zł.</text:p>")
    corpus, cases = population(tmp_path, {"input.odt": raw})
    runs = tmp_path / "runs"

    library = execute(runs, "library", "odt", corpus, ["--cases", str(cases), "--mode", "library"])
    adapter = execute(
        runs,
        "adapter",
        "odt",
        corpus,
        [
            "--cases",
            str(cases),
            "--mode",
            "adapter",
            "--reference",
            str(runs / "library/result.json"),
            "--reference-sha256",
            digest((runs / "library/result.json").read_bytes()),
        ],
    )

    assert library["status"] == "recorded" and adapter["status"] == "passed"
    assert verify(runs / "library") == library and verify(runs / "adapter") == adapter
    result = json.loads((runs / "adapter/result.json").read_bytes())
    assert result["read_inputs"] == {
        "INDEX.json": digest((corpus / "INDEX.json").read_bytes()),
        "input.odt": digest(raw),
    }
    diff = compare(runs / "library", runs / "adapter", tmp_path / "diff.json")
    assert diff["status"] == "same" and not diff["changes"] and not diff["changed_inputs"]
