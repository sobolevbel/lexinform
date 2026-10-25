import json
from pathlib import Path

import pytest
from tools.corpus_annotations import initiative_groups, run, validate_annotation, viewer
from tools.corpus_artifacts import digest
from tools.corpus_derivatives import run as reextract

from tests.unit.test_corpus_derivatives import cohort


def test_transitive_source_relations_and_document_aliases_share_one_group() -> None:
    result = initiative_groups(
        {
            "entities": {
                "druk:1": {"rcl": "rcl:2"},
                "rcl:2": {"rpw": "rpw:3", "documents": [7]},
                "rpw:3": {},
                "rcl:4": {"documents": [7]},
                "wpl:UD5": {"rcl": "rcl:2"},
                "rcl:other": {"wykaz": "UD5"},
            }
        }
    )
    assert (
        len({result[key] for key in ("druk:1", "rcl:2", "rpw:3", "doc:7", "rcl:4", "wpl:UD5")}) == 1
    )
    assert result["rcl:other"] != result["rcl:2"]


def test_review_evidence_is_literal_and_consumed_or_agent_cases_cannot_be_holdout() -> None:
    text = "USTAWA Art. 1. Termin 30 dni."
    case = {"text_sha256": digest(text.encode()), "used_for_tuning": True}
    label = {
        "text_sha256": case["text_sha256"],
        "reviewer": "reviewer",
        "reviewer_kind": "human",
        "read_scope": "excerpt",
        "split": "regression",
        "evidence": [{"start": 0, "end": 6, "quote": "USTAWA"}],
    }
    validate_annotation(case, label, text)
    with pytest.raises(ValueError, match="holdout"):
        validate_annotation(case, {**label, "split": "holdout"}, text)
    with pytest.raises(ValueError, match="source span"):
        validate_annotation(
            case, {**label, "evidence": [{"start": 0, "end": 6, "quote": "OTHER"}]}, text
        )
    with pytest.raises(ValueError, match="identity"):
        validate_annotation(case, label, text + "Modified")


def test_source_text_cannot_inject_html_or_terminate_the_viewer_script() -> None:
    result = viewer(
        {
            "doc:1": {
                "name": "</script><script>alert(1)</script>",
                "group": "rcl:1",
                "url": "https://source.test/",
                "read_ranges": [{"text": "<script>source</script>"}],
            }
        }
    )
    assert "<script>alert(1)</script>" not in result
    assert "&lt;script&gt;source&lt;/script&gt;" in result


def test_pending_queue_can_show_a_pinned_new_derivative_without_replacing_the_old_cache(
    tmp_path: Path,
) -> None:
    root, health, pin = cohort(tmp_path)
    (root / "INDEX.json").write_text('{"entities":{"rcl:1":{"documents":[1,2]}}}')
    derived = tmp_path / "derived.json"
    assert reextract(root, health, pin, derived) == 0
    target = tmp_path / "review.json"

    run(
        root,
        health,
        pin,
        target,
        derivatives=derived,
        derivatives_sha256=digest(derived.read_bytes()),
    )

    result = json.loads(target.read_bytes())
    assert result["group_count"] == 1 and len(result["cases"]) == 2
    assert result["cases"][0]["status"] == "pending"
    assert result["cases"][0]["read_ranges"][0]["text"].startswith("USTAWA")
    assert result["cases"][0]["used_for_tuning"] is True
    assert target.with_suffix(".html").exists()
    assert str(derived) in result["derivative_inputs"]
    (root / "documents/files/1.zip").write_bytes(b"changed raw source")
    with pytest.raises(ValueError, match="raw source changed"):
        run(
            root,
            health,
            pin,
            tmp_path / "stale-review.json",
            derivatives=derived,
            derivatives_sha256=digest(derived.read_bytes()),
        )
