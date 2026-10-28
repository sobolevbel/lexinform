import hashlib
import json
import os
import socket
from pathlib import Path
from typing import Any, Never

import pytest
from tools import replay_pdf_pages

from tests.conftest import FIXTURES
from tests.unit.test_corpus_runtime_reads import forbid_opening


def inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "corpus"
    root.mkdir()
    index = root / "INDEX.json"
    index.write_text("{}")
    source = root / "source.pdf"
    source.write_bytes((FIXTURES / "print_3039.pdf").read_bytes())
    cases = tmp_path / "cases.json"
    cases.write_text(
        replay_pdf_pages.PdfReplayManifest(
            version=1,
            index_sha256=hashlib.sha256(index.read_bytes()).hexdigest(),
            cases=(
                replay_pdf_pages.PdfReplayInput(
                    path="source.pdf",
                    sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                    official_ids=("druk:3039",),
                    cohorts=("regression",),
                ),
            ),
            exclusions=("Synthetic inventory around one real PDF, without a legal oracle.",),
        ).model_dump_json()
    )
    return root, cases, source


def baseline(root: Path, cases: Path, output: Path) -> str:
    assert replay_pdf_pages.run(root, cases, output, page_reader="library") == 0
    return hashlib.sha256(output.read_bytes()).hexdigest()


def test_pinned_pdf_replay_compares_legacy_selection_and_physical_pages(tmp_path: Path) -> None:
    root, cases, source = inputs(tmp_path)
    original = source.read_bytes()
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    digest = baseline(root, cases, before)

    assert (
        replay_pdf_pages.run(
            root, cases, after, page_reader="adapter", reference=before, reference_sha256=digest
        )
        == 0
    )

    saved = json.loads(after.read_bytes())
    assert saved["status"] == "passed" and not saved["changes"] and not saved["errors"]
    assert not saved["changed_inputs"] and not saved["changed_code"]
    assert len(saved["page_manifests"]["source.pdf"]) == 64
    assert saved["observations"]["source.pdf"]["physical"]["page_count"] == 17
    assert saved["read_inputs"] == {
        "INDEX.json": hashlib.sha256((root / "INDEX.json").read_bytes()).hexdigest(),
        "source.pdf": hashlib.sha256(original).hexdigest(),
    }
    assert source.read_bytes() == original


def test_replay_cannot_accept_changed_bytes_even_if_pdf_text_is_unchanged(tmp_path: Path) -> None:
    root, cases, source = inputs(tmp_path)
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    digest = baseline(root, cases, before)
    source.write_bytes(source.read_bytes() + b"\n")

    assert (
        replay_pdf_pages.run(
            root, cases, after, page_reader="adapter", reference=before, reference_sha256=digest
        )
        == 1
    )

    saved = json.loads(after.read_bytes())
    assert saved["status"] == "failed" and "SHA mismatch" in saved["errors"]["source.pdf"]


def test_replay_rejects_a_different_reference_hash_before_creating_output(tmp_path: Path) -> None:
    root, cases, _ = inputs(tmp_path)
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    baseline(root, cases, before)

    with pytest.raises(ValueError, match="Reference SHA mismatch"):
        replay_pdf_pages.run(
            root, cases, after, page_reader="adapter", reference=before, reference_sha256="0" * 64
        )

    assert not after.exists()


def test_replay_rejects_a_different_population_before_creating_output(tmp_path: Path) -> None:
    root, cases, _ = inputs(tmp_path)
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    digest = baseline(root, cases, before)
    manifest = json.loads(cases.read_bytes())
    manifest["cases"][0]["cohorts"] = ["another population"]
    cases.write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="population differs"):
        replay_pdf_pages.run(
            root, cases, after, page_reader="adapter", reference=before, reference_sha256=digest
        )

    assert not after.exists()


@pytest.mark.parametrize("operation", ["network", "write"])
def test_replay_guards_its_reader_against_network_and_input_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    root, cases, source = inputs(tmp_path)
    before = source.read_bytes()
    output = tmp_path / "replay.json"

    def forbidden(data: bytes, page_reader: replay_pdf_pages.PageReader) -> Never:
        if operation == "network":
            socket.create_connection(("127.0.0.1", 1))
        else:
            source.write_bytes(b"unwanted mutation")
        raise AssertionError("replay missed a forbidden action")

    monkeypatch.setattr(replay_pdf_pages, "snapshot", forbidden)

    assert replay_pdf_pages.run(root, cases, output, page_reader="library") == 1

    saved = json.loads(output.read_bytes())
    assert saved["status"] == "failed"
    expected = "prohibits network" if operation == "network" else "read-only"
    assert expected in saved["errors"]["source.pdf"]
    assert source.read_bytes() == before


def test_replay_cannot_pass_if_its_code_changes_during_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, cases, _ = inputs(tmp_path)
    output = tmp_path / "replay.json"
    fingerprints = iter(({}, {"changed.py": "another source"}))
    monkeypatch.setattr(replay_pdf_pages, "source_fingerprints", lambda: next(fingerprints))

    assert replay_pdf_pages.run(root, cases, output, page_reader="library") == 1

    saved = json.loads(output.read_bytes())
    assert saved["status"] == "failed" and saved["changed_code"]


def test_final_input_check_does_not_follow_a_retargeted_link_outside_the_corpus(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, cases, source = inputs(tmp_path)
    protected = tmp_path / "outside"
    protected.write_text("controlled test fixture")
    replacement = tmp_path / "replacement"
    replacement.symlink_to(protected)
    forbid_opening(monkeypatch, protected)
    original = replay_pdf_pages.snapshot

    def retarget(
        data: bytes,
        page_reader: replay_pdf_pages.PageReader,
    ) -> tuple[dict[str, Any], str | None]:
        result = original(data, page_reader)
        os.replace(replacement, source)
        return result

    monkeypatch.setattr(replay_pdf_pages, "snapshot", retarget)
    output = tmp_path / "after.json"

    assert replay_pdf_pages.run(root, cases, output, page_reader="library") == 1

    saved = json.loads(output.read_bytes())
    assert saved["status"] == "failed" and saved["changed_inputs"] == ["source.pdf"]


@pytest.mark.parametrize("argument", ["cases", "reference"])
def test_a_credential_filename_is_never_read_as_a_replay_input(
    tmp_path: Path, argument: str
) -> None:
    root, cases, _ = inputs(tmp_path)
    credential = tmp_path / ".env"
    credential.touch()
    output = tmp_path / "replay.json"
    if argument == "cases":
        cases = credential
    with pytest.raises(ValueError, match="credential file"):
        replay_pdf_pages.run(
            root,
            cases,
            output,
            page_reader="library",
            reference=credential if argument == "reference" else None,
        )
    assert not output.exists()


def test_missing_inputs_are_reported_without_a_successful_empty_population(tmp_path: Path) -> None:
    root, cases, source = inputs(tmp_path)
    source.unlink()
    output = tmp_path / "replay.json"

    assert replay_pdf_pages.run(root, cases, output, page_reader="library") == 1

    saved: dict[str, Any] = json.loads(output.read_bytes())
    assert saved["status"] == "failed" and saved["errors"]
    assert not saved["observations"]
