import gzip
import hashlib
import json
from pathlib import Path

import pytest
from tools.replay_admissibility import replay, run

MARKER = "NEVER-EMIT-THE-CACHED-ADMISSION-CONTENT"


def corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    (root / "documents/text").mkdir(parents=True)
    (root / "INDEX.json").write_text("{}")
    return root


def test_shadow_matrix_records_page_probes_without_claiming_historical_pages(
    tmp_path: Path,
) -> None:
    root = corpus(tmp_path)
    path = root / "documents/text/1.gz"
    text = "USTAWA\nArt. 1. Cudzoziemiec otrzymuje świadczenie. " + MARKER
    path.write_bytes(gzip.compress(text.encode(), mtime=0))
    before = path.read_bytes()
    report = replay(root)
    row = report["results"]["rcl/documents/text/1.gz"]
    assert report["status"] == "recorded" and report["selection_state"] == "unverified"
    assert report["page_probes"] == [0, 1, 20]
    assert row["text_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert row["page_probes"]["0"] == {"prefilter": "no_document", "analysis": "text"}
    assert row["page_probes"]["20"] == {"prefilter": "no_document", "analysis": "scan"}
    assert MARKER not in json.dumps(report) and path.read_bytes() == before


def test_unreadable_and_empty_caches_remain_explicit_observations(tmp_path: Path) -> None:
    root = corpus(tmp_path)
    (root / "documents/text/1.gz").write_bytes(b"invalid-gzip")
    (root / "documents/text/2.gz").write_bytes(gzip.compress(b"", mtime=0))
    report = replay(root)
    assert report["counts"]["unreadable_cache"] == report["counts"]["empty_cache"] == 1
    assert report["results"]["rcl/documents/text/1.gz"]["state"] == "unreadable_cache"
    assert report["results"]["rcl/documents/text/2.gz"]["page_probes"]["0"] == {
        "prefilter": "no_document",
        "analysis": "scan",
    }


def test_no_cached_views_is_not_a_successful_empty_verdict(tmp_path: Path) -> None:
    root = corpus(tmp_path)
    output = tmp_path / "report.json"
    assert run(root, output) == 2
    assert json.loads(output.read_text())["status"] == "not_run"


def test_report_paths_cannot_overwrite_input_or_prior_evidence(tmp_path: Path) -> None:
    root = corpus(tmp_path)
    output = tmp_path / "report.json"
    output.write_text("preserve")
    for target in (output, root / "new-report.json"):
        with pytest.raises(ValueError, match="new and outside"):
            run(root, target)
    assert output.read_text() == "preserve"
