import gzip
import json
from pathlib import Path

import pytest
from tools.audit_sejm_adoption import audit_adoption
from tools.corpus_artifacts import digest

OWNER = "https://api.sejm.gov.pl/sejm/term10/processes/42"
LOCATOR = f"{OWNER}/attachment/42_u3.pdf"


def snapshot(tmp_path: Path, **stage_overrides: object) -> tuple[Path, str]:
    data = {
        "42": {
            "term": 10,
            "number": "42",
            "title": "PRIVATE-DRAFT-TITLE",
            "documentType": "projekt ustawy",
            "documentTypeEnum": "BILL",
            "changeDate": "2026-01-04T12:00:00",
            "passed": False,
            "stages": [
                {
                    "stageType": "SejmReading",
                    "stageName": "III czytanie na posiedzeniu Sejmu",
                    "date": "2026-01-03",
                    "decision": "uchwalono",
                    "textAfter3": LOCATOR,
                    **stage_overrides,
                },
                {
                    "stageType": "PresidentMotionConsideration",
                    "stageName": "Rozpatrywanie wniosku Prezydenta",
                    "date": "2026-01-04",
                    "decision": "nie uchwalona ponownie",
                },
            ],
        }
    }
    path = tmp_path / "processes.json.gz"
    raw = gzip.compress(json.dumps(data).encode(), mtime=0)
    path.write_bytes(raw)
    return path, digest(raw)


def test_adoption_reports_only_exact_historical_stage_and_locator(tmp_path: Path) -> None:
    path, pin = snapshot(tmp_path)

    report = audit_adoption(path, snapshot_sha256=pin, term=10, number="42", stage_index=0)

    assert report["adopted_text_locator"] == LOCATOR
    assert report["reading_outcome"] == "passed"
    assert report["stage_date"] == "2026-01-03"
    assert report["snapshot_sha256"] == pin
    assert "PRIVATE-DRAFT-TITLE" not in json.dumps(report)
    assert digest(path.read_bytes()) == pin


@pytest.mark.parametrize(
    ("stage_change", "failure"),
    [
        ({"decision": None}, "lacks a known"),
        ({"stageType": "End"}, "lacks a known"),
        ({"stageName": "II czytanie na posiedzeniu Sejmu"}, "lacks a known"),
        ({"textAfter3": f"{OWNER}/attachment/43_u3.pdf"}, "same-owner"),
        ({"textAfter3": LOCATOR.replace("/42/", "/43/")}, "same-owner"),
        ({"textAfter3": LOCATOR.replace("api.sejm.gov.pl", "example.test")}, "same-owner"),
        ({"date": None}, "no date"),
    ],
)
def test_adoption_rejects_wrong_stage_or_locator(
    tmp_path: Path, stage_change: dict[str, object], failure: str
) -> None:
    path, pin = snapshot(tmp_path, **stage_change)

    with pytest.raises(ValueError, match=failure):
        audit_adoption(path, snapshot_sha256=pin, term=10, number="42", stage_index=0)


@pytest.mark.parametrize(
    ("term", "number", "index", "failure"),
    [
        (9, "42", 0, "requested owner"),
        (10, "43", 0, "process is unavailable"),
        (10, "42", 1, "lacks a known"),
        (10, "42", 2, "top-level stage"),
        (10, "42", -1, "zero-based stage"),
    ],
)
def test_adoption_requires_exact_selector(
    tmp_path: Path, term: int, number: str, index: int, failure: str
) -> None:
    path, pin = snapshot(tmp_path)

    with pytest.raises(ValueError, match=failure):
        audit_adoption(path, snapshot_sha256=pin, term=term, number=number, stage_index=index)


def test_adoption_rejects_a_substituted_snapshot(tmp_path: Path) -> None:
    path, pin = snapshot(tmp_path)
    snapshot(tmp_path, date="2026-01-05")

    with pytest.raises(ValueError, match="expected pin"):
        audit_adoption(path, snapshot_sha256=pin, term=10, number="42", stage_index=0)
