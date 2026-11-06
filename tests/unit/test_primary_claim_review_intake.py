from pathlib import Path
from typing import Any

import pytest
from tools import record_primary_claim_review
from tools.audit_saved_primary_claims import SavedClaimReview
from tools.corpus_artifacts import digest, write_json
from tools.replay_primary_claims import technical_answer

TEXT = "Art. 1. Termin wynosi trzy dni."
RESULT = "result-v1:" + "a" * 64
REQUEST = "b" * 64
CLAIM = "Offline technical check"


@pytest.fixture
def database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "fixture.db"
    db.write_bytes(b"unit-only saved snapshot fixture")
    monkeypatch.setattr(
        record_primary_claim_review,
        "saved_claim_review",
        lambda *_args, **_kwargs: SavedClaimReview(
            technical_answer(TEXT), "3231", REQUEST, digest(TEXT.encode()), TEXT
        ),
    )
    return db


def answers(
    db: Path, reviewer: str, *, judgment: str = "supported", **changes: Any
) -> tuple[Path, str]:
    citation = {
        "evidence": {"document_id": "submitted-context", "locator": "Art. 1", "quote": TEXT},
        "start": 0,
        "end": len(TEXT),
    }
    path = db.parent / (reviewer + ".json")
    write_json(
        path,
        {
            "version": "saved-primary-claim-review-v1",
            "database_sha256": digest(db.read_bytes()),
            "result_id": RESULT,
            "request_sha256": REQUEST,
            "text_sha256": digest(TEXT.encode()),
            "reviewer": reviewer,
            "reviewed_on": "2026-11-06",
            "reviewer_kind": "human",
            "independent": True,
            "claims": [
                {
                    "field": "key_changes",
                    "index": 0,
                    "claim_sha256": digest(CLAIM.encode()),
                    "judgment": judgment,
                    "reason": "Unit fixture judgment",
                    "citation": citation if judgment == "supported" else None,
                }
            ],
            **changes,
        },
    )
    return path, digest(path.read_bytes())


def test_explicit_independent_disagreement_stays_conflict(database: Path) -> None:
    output = database.parent / "result.json"
    result = record_primary_claim_review.record(
        database,
        digest(database.read_bytes()),
        RESULT,
        [answers(database, "Fixture 1"), answers(database, "Fixture 2", judgment="unsupported")],
        output,
    )
    assert result["claims"] == [
        {
            "field": "key_changes",
            "index": 0,
            "claim_sha256": digest(CLAIM.encode()),
            "judgment": "conflict",
        }
    ]
    assert output.is_file()


@pytest.mark.parametrize(
    ("kind", "independent", "expected"),
    [
        ("human", True, "unknown"),
        ("human", False, "unreviewed"),
        ("synthetic_control", False, "unreviewed"),
    ],
)
def test_unknown_and_nonindependent_inputs_keep_distinct_states(
    database: Path, kind: str, independent: bool, expected: str
) -> None:
    result = record_primary_claim_review.record(
        database,
        digest(database.read_bytes()),
        RESULT,
        [
            answers(
                database, "Fixture", judgment="unknown", reviewer_kind=kind, independent=independent
            )
        ],
        database.parent / "result.json",
    )
    assert result["claims"] == [
        {
            "field": "key_changes",
            "index": 0,
            "claim_sha256": digest(CLAIM.encode()),
            "judgment": expected,
        }
    ]


@pytest.mark.parametrize(
    "changes",
    [
        {"request_sha256": "0" * 64},
        {"reviewer_kind": "synthetic_control"},
        {"reviewed_on": 0},
        {"claims": [{"field": "key_changes", "index": 0, "claim_sha256": digest(CLAIM.encode())}]},
    ],
)
def test_invalid_provenance_or_placeholder_judgment_leaves_no_output(
    database: Path, changes: dict[str, Any]
) -> None:
    before = database.read_bytes()
    path = database.parent / "result.json"
    with pytest.raises(ValueError):
        record_primary_claim_review.record(
            database, digest(before), RESULT, [answers(database, "Fixture", **changes)], path
        )
    assert not path.exists()
    assert database.read_bytes() == before


def test_template_is_create_only_and_not_an_answer(database: Path) -> None:
    pin = digest(database.read_bytes())
    path = database.parent / "template.json"

    payload = record_primary_claim_review.template(database, pin, RESULT, path)

    assert payload["database_sha256"] == pin
    assert payload["result_id"] == RESULT and payload["request_sha256"] == REQUEST
    assert payload["text_sha256"] == digest(TEXT.encode())
    assert all(
        payload[key] is None for key in ("reviewer", "reviewed_on", "reviewer_kind", "independent")
    )
    assert payload["claims"] == [
        {
            "field": "key_changes",
            "index": 0,
            "claim_sha256": digest(CLAIM.encode()),
            "judgment": None,
            "reason": None,
            "citation": None,
        }
    ]
    frozen = path.read_bytes()
    with pytest.raises(ValueError, match="new and separate"):
        record_primary_claim_review.template(database, pin, RESULT, path)
    with pytest.raises(ValueError, match="claim review schema"):
        record_primary_claim_review.record(
            database, pin, RESULT, [(path, digest(frozen))], database.parent / "rejected.json"
        )
    assert path.read_bytes() == frozen
    assert not (database.parent / "rejected.json").exists()
