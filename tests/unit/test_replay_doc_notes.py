"""Corpus text references must match both bytes and the exact replay population."""

import hashlib
import json
import os
from pathlib import Path

import pytest
from tools.replay_doc_notes import run


def replay_inputs(
    tmp_path: Path, *, wrong_text: bool = False, wrong_population: bool = False
) -> tuple[Path, Path, Path, str]:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "INDEX.json").write_bytes(b"{}")
    data = b"non-OLE document"
    (corpus / "file.doc").write_bytes(data)
    identity = hashlib.sha256(data).hexdigest()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "index_sha256": hashlib.sha256(b"{}").hexdigest(),
                "cases": [{"path": "file.doc", "sha256": identity, "extension": ".doc"}],
            }
        )
    )
    reference = tmp_path / "reference.json"
    reference.write_text(
        json.dumps(
            {
                "observations": {
                    "a" * 64 if wrong_population else identity: {
                        "legacy_text_sha256": hashlib.sha256(
                            b"other" if wrong_text else b""
                        ).hexdigest()
                    }
                }
            }
        )
    )
    return corpus, manifest, reference, hashlib.sha256(reference.read_bytes()).hexdigest()


def test_pinned_baseline_records_honest_unavailable_input_without_text_changes(
    tmp_path: Path,
) -> None:
    corpus, manifest, reference, pin = replay_inputs(tmp_path)
    output = tmp_path / "result"

    result = run(
        corpus, manifest, output, source_characters=True, reference=reference, reference_sha256=pin
    )

    assert result["unique_inputs"] == 1
    assert result["status_counts"] == {"unavailable": 1}
    assert result["changed_inputs"] == []
    assert (output / "report.json").is_file()


@pytest.mark.parametrize(
    ("wrong_text", "wrong_population", "reason"),
    [
        (True, False, "Legacy text differs"),
        (False, True, "populations differ"),
    ],
)
def test_reference_mismatch_never_publishes_a_passing_report(
    tmp_path: Path, wrong_text: bool, wrong_population: bool, reason: str
) -> None:
    corpus, manifest, reference, pin = replay_inputs(
        tmp_path, wrong_text=wrong_text, wrong_population=wrong_population
    )
    output = tmp_path / "result"

    with pytest.raises(ValueError, match=reason):
        run(
            corpus,
            manifest,
            output,
            source_characters=True,
            reference=reference,
            reference_sha256=pin,
        )

    assert not output.exists()


def test_changed_reference_bytes_fail_before_replay(tmp_path: Path) -> None:
    corpus, manifest, reference, pin = replay_inputs(tmp_path)
    reference.write_text("{}")

    with pytest.raises(ValueError, match="reference SHA differs"):
        run(
            corpus,
            manifest,
            tmp_path / "result",
            source_characters=True,
            reference=reference,
            reference_sha256=pin,
        )


@pytest.mark.parametrize("relative", [False, True])
def test_manifest_cannot_count_project_sources_as_corpus_inputs(
    tmp_path: Path, relative: bool
) -> None:
    corpus, manifest, _, _ = replay_inputs(tmp_path)
    target = Path(__file__).resolve()
    values = json.loads(manifest.read_text())
    values["cases"][0]["path"] = os.path.relpath(target, corpus) if relative else str(target)
    values["cases"][0]["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(values))
    output = tmp_path / "result"

    with pytest.raises(ValueError, match="escapes root"):
        run(corpus, manifest, output)

    assert not output.exists()
