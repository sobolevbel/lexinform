import gzip
import json
from pathlib import Path
from typing import Any

import pytest
from tools import corpus_legacy_suites


def test_real_process_parser_runs_under_the_common_guard(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    for term in (8, 9, 10):
        path = root / f"sejm/term{term}/process_details.json.gz"
        path.parent.mkdir(parents=True)
        path.write_bytes(
            gzip.compress(
                json.dumps(
                    {
                        "1": {
                            "number": "1",
                            "term": term,
                            "title": "Projekt ustawy",
                            "documentTypeEnum": "BILL",
                            "changeDate": "2026-10-03T12:00:00",
                        }
                    }
                ).encode()
            )
        )
    output = tmp_path / "process.json"

    assert corpus_legacy_suites.run(root, output, kind="process") == 0

    result = json.loads(output.read_bytes())
    assert len(result["observations"]) == 3
    assert not result["changed_inputs"]
    assert result["summary"]["coverage"] == {"8": 1, "9": 1, "10": 1}


def test_absent_supplement_is_not_a_successful_empty_senate_suite(tmp_path: Path) -> None:
    output = tmp_path / "senate.json"

    assert corpus_legacy_suites.run(tmp_path / "corpus", output, kind="senate") == 2

    assert json.loads(output.read_bytes())["status"] == "not_run"


def test_guard_rejects_credential_reads_from_a_legacy_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "corpus"
    root.mkdir()
    (root / ".env").write_text("private")

    def unsafe(corpus: Path) -> dict[str, Any]:
        return {"rows": (corpus / ".env").read_text()}

    monkeypatch.setattr(corpus_legacy_suites, "process_report", unsafe)
    output = tmp_path / "failed.json"

    assert corpus_legacy_suites.run(root, output, kind="process") == 1

    assert "must not read .env" in json.loads(output.read_bytes())["error"]
