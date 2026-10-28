import builtins
import io
import os
from pathlib import Path
from typing import IO, Any

import pytest
from tools.corpus_runtime import CorpusReads


def forbid_opening(monkeypatch: pytest.MonkeyPatch, protected: Path) -> None:
    original = builtins.open

    def checked(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> IO[Any]:
        if not isinstance(file, int) and Path(os.fsdecode(file)).resolve() == protected.resolve():
            raise AssertionError("protected input was opened")
        return original(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", checked)
    monkeypatch.setattr(io, "open", checked)


@pytest.mark.parametrize("alias", [False, True])
def test_require_refuses_credential_paths_before_opening_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, alias: bool
) -> None:
    root = tmp_path / "corpus"
    root.mkdir()
    protected = root / ".env"
    protected.write_text("controlled test fixture")
    source = root / "alias" if alias else protected
    if alias:
        source.symlink_to(protected)
    forbid_opening(monkeypatch, protected)
    reads = CorpusReads(root)

    with pytest.raises(ValueError, match="must not read .env"):
        reads.require(source)

    assert not reads.initial_sha256


@pytest.mark.parametrize("credential", [False, True])
def test_final_recheck_marks_retargeted_inputs_changed_without_opening_the_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, credential: bool
) -> None:
    root = tmp_path / "corpus"
    root.mkdir()
    source = root / "source"
    source.write_text("original")
    protected = root / ".env" if credential else tmp_path / "outside"
    protected.write_text("controlled test fixture")
    forbid_opening(monkeypatch, protected)
    reads = CorpusReads(root)
    with reads.guard():
        assert source.read_text() == "original"
    source.unlink()
    source.symlink_to(protected)

    assert reads.changed_inputs() == ["source"]


def test_internal_alias_rebinding_is_detected_even_when_its_old_target_is_unchanged(
    tmp_path: Path,
) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    first.write_text("first")
    second.write_text("second")
    alias = tmp_path / "alias"
    alias.symlink_to(first)
    reads = CorpusReads(tmp_path)
    with reads.guard():
        assert alias.read_text() == "first"
    alias.unlink()
    alias.symlink_to(second)

    assert reads.changed_inputs() == ["alias"]


@pytest.mark.parametrize("change", ["none", "contents", "deleted"])
def test_ordinary_input_rechecks_keep_their_existing_results(tmp_path: Path, change: str) -> None:
    source = tmp_path / "source"
    source.write_text("original")
    reads = CorpusReads(tmp_path)
    with reads.guard():
        assert source.read_text() == "original"
    if change == "contents":
        source.write_text("changed")
    elif change == "deleted":
        source.unlink()

    assert reads.changed_inputs() == ([] if change == "none" else ["source"])
