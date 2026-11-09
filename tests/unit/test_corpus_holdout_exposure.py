import json
from pathlib import Path

import pytest
from tools.corpus_artifacts import digest, write_json
from tools.corpus_holdout_exposure import create, load_exposures
from tools.corpus_review import apply_exposures, form, record
from tools.corpus_split_census import audit

from tests.unit.test_corpus_review import HOLDOUT, answers, holdout_queue, page_review
from tests.unit.test_corpus_split_census import holdout_case, seal


def test_review_and_form_demote_only_with_the_pinned_exposure(tmp_path: Path) -> None:
    corpus, queue, pin, raw = holdout_queue(tmp_path)
    proof = tmp_path / "proof.json"
    receipt = tmp_path / "receipt.json"
    write_json(proof, {"source_sha256": raw})
    create(queue, pin, "qualification", proof, digest(proof.read_bytes()), [HOLDOUT], receipt)
    exposure = [(receipt, digest(receipt.read_bytes()))]
    form(corpus, queue, pin, tmp_path / "form.json", exposures=exposure)
    assert (
        json.loads((tmp_path / "form.json").read_bytes())["cases"][0]["holdout_eligible"] is False
    )
    offered = answers(
        tmp_path / "answers.json", pin, {HOLDOUT: page_review(raw, independent_initiative="yes")}
    )
    record(corpus, queue, pin, [offered], tmp_path / "review.json", exposures=exposure)
    reviewed = json.loads((tmp_path / "review.json").read_bytes())
    assert reviewed["cases"][0]["split"] == "regression"
    assert reviewed["cases"][0]["oracle"] == "human_review"
    assert reviewed["exposures"] == {str(receipt): exposure[0][1]}
    record(corpus, queue, pin, [offered], tmp_path / "legacy-review.json")
    assert (
        json.loads((tmp_path / "legacy-review.json").read_bytes())["cases"][0]["split"] == "holdout"
    )


def test_census_demotes_exact_raw_and_group_components(tmp_path: Path) -> None:
    one = holdout_case()
    two = {**one, "id": "rpw:two", "originals": [{"sha256": "b" * 64}]}
    three = {**two, "id": "rpw:three", "group": "new-group"}
    fresh = {**three, "id": "rpw:fresh", "group": "fresh", "originals": [{"sha256": "c" * 64}]}
    first = seal(tmp_path, "first", "holdout", {"cases": [one, two]})
    second = seal(tmp_path, "second", "holdout", {"cases": [three, fresh]})
    proof, receipt = (tmp_path / name for name in ("proof.json", "receipt.json"))
    write_json(proof, {"source_sha256": "a" * 64})
    create(*first, "qualification", proof, digest(proof.read_bytes()), [one["id"]], receipt)
    pins = [(receipt, digest(receipt.read_bytes()))]
    report = audit([first, second], tmp_path / "census.json", exposures=pins)
    assert report["exposed_holdout_case_ids"] == [three["id"], two["id"], one["id"]]
    assert report["remaining_reserved_holdout_case_ids"] == [fresh["id"]]
    assert report["independence_confirmed_case_ids"] == []
    targets = {
        row["id"]: {
            "group": row["group"],
            "originals": {item["sha256"]: {} for item in row["originals"]},
            "holdout_eligible": True,
        }
        for row in (three, fresh)
    }
    apply_exposures("holdout", targets, pins)
    assert targets[three["id"]]["holdout_eligible"] is False
    assert targets[fresh["id"]]["holdout_eligible"] is True
    with pytest.raises(ValueError, match="parent queue"):
        audit([second], tmp_path / "missing.json", exposures=pins)


def test_exposure_requires_exact_proof_and_rejects_resealed_foreign_case(tmp_path: Path) -> None:
    case = holdout_case()
    queue = seal(tmp_path, "holdout", "holdout", {"cases": [case]})
    proof = tmp_path / "proof.json"
    write_json(proof, {"source_sha256": "b" * 64})
    with pytest.raises(ValueError, match="does not bind"):
        create(
            *queue,
            "qualification",
            proof,
            digest(proof.read_bytes()),
            [case["id"]],
            tmp_path / "rejected.json",
        )
    assert not (tmp_path / "rejected.json").exists()
    assisted = tmp_path / "assisted.json"
    write_json(
        assisted,
        {
            "review_type": "human_verification_of_agent_draft",
            "queue_manifest_sha256": queue[1],
            "cases": {case["id"]: {"original_sha256": "a" * 64}},
        },
    )
    receipt = tmp_path / "receipt.json"
    create(
        *queue, "assisted_review", assisted, digest(assisted.read_bytes()), [case["id"]], receipt
    )
    assert load_exposures([(receipt, digest(receipt.read_bytes()))])
    altered = json.loads(receipt.read_bytes())
    altered["cases"][0]["group"] = "foreign"
    forged = tmp_path / "forged.json"
    write_json(forged, altered)
    with pytest.raises(ValueError, match="cases differ"):
        load_exposures([(forged, digest(forged.read_bytes()))])

    raw = receipt.read_bytes()
    with pytest.raises(FileExistsError):
        create(
            *queue,
            "assisted_review",
            assisted,
            digest(assisted.read_bytes()),
            [case["id"]],
            receipt,
        )
    assert receipt.read_bytes() == raw
    assisted.write_text("{}")
    with pytest.raises(ValueError, match="Exposure SHA mismatch"):
        load_exposures([(receipt, digest(raw))])
