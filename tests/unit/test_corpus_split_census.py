from pathlib import Path
from typing import Any

import pytest
from tools import corpus_split_census
from tools.corpus_artifacts import digest, write_json
from tools.corpus_review import settle
from tools.corpus_runs import SUITES, finish, new_run, verify, write_started
from tools.corpus_split_census import audit


def seal(root: Path, name: str, suite: str, report: dict[str, Any]) -> tuple[Path, str]:
    directory = new_run(root, name, suite)
    module, preset = SUITES[suite]
    started = {"command": ["python", "-m", f"tools.{module}", *preset]}
    write_started(directory, started)
    write_json(directory / "result.json", {"status": "recorded", **report})
    finish(directory, suite, returncode=0, started=started)
    return directory, digest((directory / "manifest.json").read_bytes())


def holdout_case() -> dict[str, Any]:
    return {
        "id": "rpw:unseen",
        "group": "rpw:unseen",
        "title": "Unseen",
        "split": "holdout_pending",
        "used_for_tuning": False,
        "questions": {"independent_initiative": "unknown", "role": "unknown"},
        "originals": [{"sha256": "a" * 64}],
    }


def test_exact_case_ids_raw_aliases_and_form_do_not_inflate_population(tmp_path: Path) -> None:
    holdout = seal(tmp_path, "holdout", "holdout", {"cases": [holdout_case()]})
    annotations = seal(
        tmp_path,
        "annotations",
        "annotations",
        {"cases": [{"id": "doc:1", "group": "g", "questions": {"role": "unknown"}}]},
    )
    empty = seal(
        tmp_path,
        "empty",
        "empty-review",
        {"records": {"1": {"group": "g", "raw_sha256": "a" * 64}}},
    )
    form = seal(
        tmp_path,
        "form",
        "review-form",
        {"queue": {"suite": "holdout", "manifest_sha256": holdout[1]}, "cases": [holdout_case()]},
    )

    result = audit([form, empty, annotations, holdout], tmp_path / "census.json")

    assert result["exact_case_ids"] == 2
    assert result["case_components_after_raw_aliases"] == 1
    assert result["recorded_groups"] == 2
    assert result["diagnostic_case_ids"] == result["reserved_holdout_case_ids"] == 1
    assert result["reserved_overlap_case_ids"] == ["rpw:unseen"]
    assert result["human_review_question_counts"] == {"known": 0, "unknown": 0, "conflict": 0}
    assert result["holdout_evaluation_use"] == "unknown"


@pytest.mark.parametrize("kind", ["human", "agent"])
def test_only_pinned_human_review_contributes_labels(tmp_path: Path, kind: str) -> None:
    case = holdout_case()
    queue = seal(tmp_path, "holdout", "holdout", {"cases": [case]})
    answers = {
        "reviewer": "Fixture",
        "reviewer_kind": kind,
        "answers_sha256": "b" * 64,
        "answers": {"independent_initiative": "yes", "role": "unknown"},
    }
    target = {
        "title": case["title"],
        "group": case["group"],
        "questions": list(case["questions"]),
        "holdout_eligible": True,
    }
    reviewed = settle(case["id"], target, [answers])
    review = seal(
        tmp_path,
        "review",
        "review",
        {
            "queue": {"suite": "holdout", "manifest_sha256": queue[1]},
            "cases": [reviewed],
            "answers": {"answers.json": answers["answers_sha256"]},
        },
    )

    result = audit([queue, review], tmp_path / "census.json")

    expected = int(kind == "human")
    assert result["human_review_question_counts"] == {
        "known": expected,
        "unknown": expected,
        "conflict": 0,
    }
    assert result["independence_confirmed_case_ids"] == ([case["id"]] if kind == "human" else [])
    assert result["holdout_evaluation_use"] == "unknown"


def test_rejects_wrong_seal_parent_and_output_inside_sealed_run(tmp_path: Path) -> None:
    queue = seal(tmp_path, "holdout", "holdout", {"cases": [holdout_case()]})
    form = seal(
        tmp_path,
        "form",
        "review-form",
        {"queue": {"suite": "holdout", "manifest_sha256": "b" * 64}, "cases": [holdout_case()]},
    )
    output = tmp_path / "census.json"

    with pytest.raises(ValueError, match="manifest SHA"):
        audit([(queue[0], "c" * 64)], output)
    with pytest.raises(ValueError, match="exact pinned parent"):
        audit([queue, form], output)
    with pytest.raises(ValueError, match="outside the sealed"):
        audit([queue], queue[0] / "census.json")
    assert not output.exists()


def test_rejects_started_bytes_changed_after_seal_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = seal(tmp_path, "holdout", "holdout", {"cases": [holdout_case()]})

    def mutate_started(directory: Path) -> dict[str, Any]:
        manifest = verify(directory)
        started = directory / "started.json"
        started.write_bytes(started.read_bytes() + b" ")
        return manifest

    monkeypatch.setattr(corpus_split_census, "verify", mutate_started)
    output = tmp_path / "census.json"

    with pytest.raises(ValueError, match="Sealed census input changed"):
        audit([queue], output)

    assert not output.exists()


def test_overlap_follows_alternating_raw_aliases_and_recorded_groups(tmp_path: Path) -> None:
    holdout = seal(
        tmp_path,
        "holdout",
        "holdout",
        {
            "cases": [
                holdout_case(),
                {**holdout_case(), "id": "rpw:bridge", "group": "diagnostic-group"},
                {
                    **holdout_case(),
                    "id": "rpw:separate",
                    "group": "separate",
                    "originals": [{"sha256": "c" * 64}],
                },
            ]
        },
    )
    annotations = seal(
        tmp_path,
        "annotations",
        "annotations",
        {
            "cases": [
                {"id": "doc:known", "group": "diagnostic-group", "questions": {"role": "unknown"}}
            ]
        },
    )

    result = audit([holdout, annotations], tmp_path / "census.json")

    assert result["reserved_overlap_case_ids"] == ["rpw:bridge", "rpw:unseen"]
    assert result["exact_case_ids"] == 4
    assert result["case_components_after_raw_aliases"] == 3


@pytest.mark.parametrize("repeat_pin", [True, False])
def test_census_deduplicates_exact_reviews_and_rejects_distinct_answers_by_same_reviewer(
    tmp_path: Path, repeat_pin: bool
) -> None:
    case = holdout_case()
    queue = seal(tmp_path, "holdout", "holdout", {"cases": [case]})
    target = {
        "title": case["title"],
        "group": case["group"],
        "questions": list(case["questions"]),
        "holdout_eligible": True,
    }
    reviews = []
    for index, pin in enumerate(("b" * 64, ("b" if repeat_pin else "c") * 64)):
        submission = {
            "reviewer": "Fixture",
            "reviewer_kind": "human",
            "answers_sha256": pin,
            "answers": {"independent_initiative": "yes", "role": "unknown"},
        }
        reviews.append(
            seal(
                tmp_path,
                f"review-{index}",
                "review",
                {
                    "queue": {"suite": "holdout", "manifest_sha256": queue[1]},
                    "cases": [settle(case["id"], target, [submission])],
                    "answers": {f"answers-{index}.json": pin},
                },
            )
        )
    output = tmp_path / "census.json"
    if repeat_pin:
        report = audit([queue, *reviews], output)
        assert report["independence_confirmed_case_ids"] == [case["id"]]
        assert report["human_review_question_counts"] == {"known": 1, "unknown": 1, "conflict": 0}
    else:
        with pytest.raises(ValueError, match="Multiple pinned answers by one reviewer"):
            audit([queue, *reviews], output)
        assert not output.exists()
