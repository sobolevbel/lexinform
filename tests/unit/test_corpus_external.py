import builtins
import hashlib
import json
import socket
from pathlib import Path
from typing import Any

import pytest
from tools import corpus_external


def corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    (root / "documents/files").mkdir(parents=True)
    (root / "INDEX.json").write_text('{"entities": {}}')
    (root / "documents/index.json").write_text("{}")
    (root / "documents/files/1.docx").write_bytes(b"frozen official input")
    return root


def replay_fixture(root: Path, *, all_word: bool = False) -> dict[str, Any]:
    data = (root / "documents/files/1.docx").read_bytes()
    return {
        "projects": {},
        "prints": {},
        "packages": {"1.docx": {"sha256": hashlib.sha256(data).hexdigest(), "chars": 10}},
    }


def test_external_baseline_and_pinned_comparison_preserve_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = corpus(tmp_path)
    monkeypatch.setattr(corpus_external, "replay_documents", replay_fixture)
    before = {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    baseline, candidate = tmp_path / "baseline.json", tmp_path / "candidate.json"

    assert corpus_external.run_external(root, baseline, ("extraction",), mode="baseline") == 0
    pin = hashlib.sha256(baseline.read_bytes()).hexdigest()
    assert (
        corpus_external.run_external(
            root,
            candidate,
            ("extraction",),
            mode="compare",
            reference=baseline,
            reference_sha256=pin,
        )
        == 0
    )

    saved = json.loads(baseline.read_bytes())
    result = json.loads(candidate.read_bytes())
    assert saved["status"] == "recorded" and not saved["independent_oracle"]
    assert result["status"] == "passed" and result["changes"] == []
    assert saved["inputs"]["documents/files/1.docx"]["official_ids"] == ["RCL/document/1"]
    assert saved["inputs"]["documents/files/1.docx"]["coverage"] == "raw"
    assert saved["observations"]["word/1.docx"]["inputs"] == ["documents/files/1.docx"]
    assert before == {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_changed_input_and_decision_cannot_pass_a_pinned_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = corpus(tmp_path)
    monkeypatch.setattr(corpus_external, "replay_documents", replay_fixture)
    baseline, candidate = tmp_path / "baseline.json", tmp_path / "candidate.json"
    corpus_external.run_external(root, baseline, ("extraction",), mode="baseline")
    (root / "documents/files/1.docx").write_bytes(b"different official input")

    code = corpus_external.run_external(
        root,
        candidate,
        ("extraction",),
        mode="compare",
        reference=baseline,
        reference_sha256=hashlib.sha256(baseline.read_bytes()).hexdigest(),
    )

    result = json.loads(candidate.read_bytes())
    assert code == 1
    assert result["changed_inputs"] == ["documents/files/1.docx"]
    assert result["changes"][0]["after"]["inputs"] == ["documents/files/1.docx"]


def test_external_compare_requires_an_explicit_verified_baseline(tmp_path: Path) -> None:
    root = corpus(tmp_path)
    with pytest.raises(ValueError, match="needs --reference"):
        corpus_external.run_external(root, tmp_path / "out.json", ("comparison",), mode="compare")
    baseline = tmp_path / "baseline.json"
    baseline.write_text("{}")
    with pytest.raises(ValueError, match="SHA mismatch"):
        corpus_external.run_external(
            root,
            tmp_path / "out.json",
            ("comparison",),
            mode="compare",
            reference=baseline,
            reference_sha256="0" * 64,
        )


def test_missing_external_corpus_is_not_run(tmp_path: Path) -> None:
    output = tmp_path / "out.json"

    assert (
        corpus_external.run_external(tmp_path / "absent", output, ("comparison",), mode="baseline")
        == 2
    )

    assert json.loads(output.read_bytes())["missing_inputs"] == ["INDEX.json"]


def test_empty_external_profile_is_not_a_successful_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = corpus(tmp_path)
    monkeypatch.setattr(corpus_external, "compare_laws", lambda root: {"pairs": []})
    monkeypatch.setattr(corpus_external, "mutate_laws", lambda root: {})
    output = tmp_path / "out.json"

    assert corpus_external.run_external(root, output, ("comparison",), mode="baseline") == 2

    assert json.loads(output.read_bytes())["profiles"]["comparison"] == "not_run"


def test_print_metadata_alone_cannot_pass_raw_extraction(tmp_path: Path) -> None:
    root = corpus(tmp_path)
    (root / "documents/files/1.docx").unlink()
    source = Path(__file__).parents[1] / "fixtures/document_selection/print_8_2864.json"
    for term in (8, 9, 10):
        directory = root / f"sejm/term{term}"
        directory.mkdir(parents=True)
        (directory / "prints.json").write_text(
            "[" + source.read_text() + "]" if term == 8 else "[]"
        )
    output = tmp_path / "out.json"

    assert corpus_external.run_external(root, output, ("extraction",), mode="baseline") == 2

    saved = json.loads(output.read_bytes())
    assert saved["operations"]["documents"]["rows"] == 1
    assert saved["profiles"]["extraction"] == "not_run"


@pytest.mark.parametrize(
    "mutations",
    [{}, {"documents": 3, "counts": {"deadline": {"total": 0}}, "missed": []}],
)
def test_comparison_pairs_cannot_hide_an_empty_mutation_population(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutations: dict[str, Any]
) -> None:
    root = corpus(tmp_path)
    pair = {
        "kind": "rcl",
        "key": "rcl:1",
        "druk": "1",
        "old_path": "old.txt",
        "new_path": "new.txt",
    }
    monkeypatch.setattr(corpus_external, "compare_laws", lambda root: {"pairs": [pair]})
    monkeypatch.setattr(corpus_external, "mutate_laws", lambda root: mutations)
    output = tmp_path / "out.json"

    assert corpus_external.run_external(root, output, ("comparison",), mode="baseline") == 2

    saved = json.loads(output.read_bytes())
    assert saved["operations"]["comparison"]["population"] == 1
    assert saved["operations"]["mutations"]["population"] == 0
    assert saved["profiles"]["comparison"] == "not_run"


def test_word_files_cannot_hide_an_empty_nested_package_population(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = corpus(tmp_path)

    def partial_replay(path: Path, *, all_word: bool = False) -> dict[str, Any]:
        return replay_fixture(path) if all_word else {"prints": {"10/1": None}}

    monkeypatch.setattr(corpus_external, "replay_documents", partial_replay)
    output = tmp_path / "out.json"

    assert corpus_external.run_external(root, output, ("extraction",), mode="baseline") == 2

    saved = json.loads(output.read_bytes())
    assert saved["operations"]["documents"]["population"] == 0
    assert saved["operations"]["word"]["population"] == 1
    assert saved["profiles"]["extraction"] == "not_run"


def test_print_metadata_cannot_hide_an_empty_candidate_population(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = corpus(tmp_path)
    monkeypatch.setattr(
        corpus_external,
        "replay_documents",
        lambda root: {"prints": {"10/1": None}},
    )
    monkeypatch.setattr(corpus_external, "replay_candidates", lambda root, reads: {})
    output = tmp_path / "out.json"

    assert corpus_external.run_external(root, output, ("selection",), mode="baseline") == 2

    saved = json.loads(output.read_bytes())
    assert saved["operations"]["documents"]["population"] == 1
    assert saved["operations"]["candidates"]["population"] == 0
    assert saved["profiles"]["selection"] == "not_run"


def test_replay_failure_takes_precedence_over_missing_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = corpus(tmp_path)
    (root / "documents/index.json").write_text('{"2": {"extension": "docx"}}')

    def failed_replay(root: Path, *, all_word: bool = False) -> dict[str, Any]:
        raise RuntimeError("broken replay")

    monkeypatch.setattr(corpus_external, "replay_documents", failed_replay)
    output = tmp_path / "out.json"

    assert corpus_external.run_external(root, output, ("extraction",), mode="baseline") == 1

    saved = json.loads(output.read_bytes())
    assert saved["missing_inputs"] == ["documents/files/2.docx"]
    assert saved["status"] == "failed"


def test_scoped_guard_blocks_writes_network_and_outside_reads_then_restores_io(
    tmp_path: Path,
) -> None:
    root = corpus(tmp_path)
    reads = corpus_external.CorpusReads(root)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    with reads.guard():
        assert (root / "INDEX.json").read_bytes()
        with pytest.raises(PermissionError, match="read-only"):
            (root / "INDEX.json").write_text("overwrite")
        with pytest.raises(PermissionError, match="outside"):
            outside.read_bytes()
        with pytest.raises(RuntimeError, match="prohibits network"):
            socket.getaddrinfo("example.invalid", 443)
    outside.write_text("guard removed")
    assert outside.read_text() == "guard removed"


def test_cached_text_gap_is_recorded_even_when_old_replay_returns_empty(tmp_path: Path) -> None:
    root = corpus(tmp_path)
    reads = corpus_external.CorpusReads(root)
    extractor = corpus_external.RequiredCachedExtractor(root, reads)

    with reads.guard():
        assert extractor.extract(b"777") == ""

    assert reads.missing == {"documents/text/777.txt.gz"}


def test_existing_result_and_corpus_output_paths_are_rejected(tmp_path: Path) -> None:
    root = corpus(tmp_path)
    output = tmp_path / "saved.json"
    output.write_text("old result")
    for target in (output, root / "new.json"):
        with pytest.raises(ValueError, match="new file outside"):
            corpus_external.run_external(root, target, ("comparison",), mode="baseline")
    assert output.read_text() == "old result"


@pytest.mark.parametrize("profiles", [(), ("unknown",), ("comparison", "comparison")])
def test_external_api_rejects_invalid_profile_populations(
    tmp_path: Path, profiles: tuple[str, ...]
) -> None:
    with pytest.raises(ValueError, match="known profiles"):
        corpus_external.run_external(
            tmp_path / "corpus", tmp_path / "result.json", profiles, mode="baseline"
        )


def test_inputs_changing_during_replay_cannot_claim_the_final_sha_as_consumed_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = corpus(tmp_path)
    original_open = builtins.open
    source = root / "documents/files/1.docx"
    initial = hashlib.sha256(source.read_bytes()).hexdigest()

    def changing_replay(path: Path, *, all_word: bool = False) -> dict[str, Any]:
        result = replay_fixture(path, all_word=all_word)
        with original_open(source, "wb") as stream:
            stream.write(b"concurrent external change")
        return result

    monkeypatch.setattr(corpus_external, "replay_documents", changing_replay)
    output = tmp_path / "out.json"

    assert corpus_external.run_external(root, output, ("extraction",), mode="baseline") == 1

    result = json.loads(output.read_bytes())
    assert result["changed_during_replay"] == ["documents/files/1.docx"]
    assert result["inputs"]["documents/files/1.docx"]["sha256"] == initial
    assert result["inputs"]["documents/files/1.docx"]["final_sha256"] != initial


def test_corpus_symlink_to_trusted_code_still_cannot_escape_root(tmp_path: Path) -> None:
    root = corpus(tmp_path)
    (root / "link.py").symlink_to(Path(__file__).resolve())
    reads = corpus_external.CorpusReads(root)

    with reads.guard(), pytest.raises(PermissionError, match="symlink escapes"):
        (root / "link.py").read_bytes()
