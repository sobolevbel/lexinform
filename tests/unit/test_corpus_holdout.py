import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
from tools.corpus_artifacts import digest, write_json
from tools.corpus_holdout import (
    candidates,
    grouped_candidates,
    regression_download_hashes,
    run,
    viewer,
)
from tools.corpus_runs import finish


def source_row(identity: str, data: bytes, path: str = "downloads/new.pdf") -> dict[str, Any]:
    return {
        "metadata": [
            {
                "number": identity.removeprefix("rpw:"),
                "applicantType": "DEPUTIES",
                "submissionType": "BILL",
                "title": "New bill",
            }
        ],
        "reserved_holdout": True,
        "downloads": [
            {
                "state": "downloaded",
                "path": path,
                "name": "new.pdf",
                "url": "https://api.sejm.gov.pl/new.pdf",
                "sha256": digest(data),
                "bytes": len(data),
            }
        ],
    }


def preparation(tmp_path: Path) -> tuple[Path, Path, str, Path, str, Path, str]:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    original = corpus / "old.zip"
    with zipfile.ZipFile(original, "w") as archive:
        archive.writestr("misleading.dat", b"%PDF-consumed-member")
    baseline = tmp_path / "baseline.json"
    write_json(
        baseline,
        {
            "entities": {
                "rcl:1": {"druk": "druk:2", "documents": [3]},
                "druk:2": {"rpw": "rpw:RPW/1/2026"},
                "rpw:RPW/1/2026": {},
            }
        },
    )
    baseline_pin = digest(baseline.read_bytes())
    inventory = tmp_path / "inventory.json"
    write_json(
        inventory,
        {
            "index_sha256": baseline_pin,
            "files": [{"path": "old.zip", "sha256": digest(original.read_bytes())}],
        },
    )
    source = tmp_path / "source-run"
    downloads = source / "downloads"
    downloads.mkdir(parents=True)
    consumed = b"%PDF-consumed-member"
    fresh = b"%PDF-unread-fresh-body"
    (downloads / "alias.pdf").write_bytes(consumed)
    (downloads / "new.pdf").write_bytes(fresh)
    write_json(
        source / "result.json",
        {
            "status": "recorded",
            "observations": {
                "rpw:RPW/2/2026": source_row("rpw:RPW/2/2026", consumed, "downloads/alias.pdf"),
                "rpw:RPW/3/2026": source_row("rpw:RPW/3/2026", fresh),
            },
        },
    )
    write_json(source / "started.json", {})
    finish(source, "sejm-sources", returncode=0, started={})
    return (
        corpus,
        baseline,
        baseline_pin,
        inventory,
        digest(inventory.read_bytes()),
        source,
        digest((source / "manifest.json").read_bytes()),
    )


def test_consumed_links_and_raw_aliases_cannot_become_a_new_holdout() -> None:
    data = b"%PDF-unread"
    rows = {
        "rpw:RPW/1/2026": source_row("rpw:RPW/1/2026", data),
        "rpw:RPW/2/2026": source_row("rpw:RPW/2/2026", b"consumed"),
        "rpw:RPW/3/2026": source_row("rpw:RPW/3/2026", data),
    }
    accepted, rejected = candidates(rows, {"rpw:RPW/1/2026": "druk:7"}, {digest(b"consumed")})

    assert list(accepted) == ["rpw:RPW/3/2026"]
    assert rejected["rpw:RPW/1/2026"] == ["previously_consumed_initiative"]
    assert rejected["rpw:RPW/2/2026"] == ["raw_bytes_already_consumed"]


@pytest.mark.parametrize("field,value", [("applicantType", "GOVERNMENT"), ("print", "77")])
def test_unresolved_or_existing_initiative_links_require_human_relation_review(
    field: str, value: str
) -> None:
    row = source_row("rpw:RPW/4/2026", b"new")
    row["metadata"][0][field] = value

    accepted, rejected = candidates({"rpw:RPW/4/2026": row}, {}, set())

    assert not accepted and rejected


def test_source_processed_before_reservation_is_not_promoted_to_holdout() -> None:
    row = source_row("rpw:RPW/4/2026", b"new")
    row["reserved_holdout"] = False

    accepted, rejected = candidates({"rpw:RPW/4/2026": row}, {}, set())

    assert not accepted
    assert rejected["rpw:RPW/4/2026"] == ["not_reserved_before_body_processing"]


def test_reserved_bytes_shared_with_a_new_regression_download_are_consumed() -> None:
    data = b"new bytes used by a new regression source"
    first = source_row("rpw:RPW/4/2026", data)
    second = source_row("rpw:RPW/5/2026", data)
    second["reserved_holdout"] = False
    rows = {"rpw:RPW/4/2026": first, "rpw:RPW/5/2026": second}

    accepted, rejected = candidates(rows, {}, regression_download_hashes(rows))

    assert not accepted
    assert rejected["rpw:RPW/4/2026"] == ["raw_bytes_already_consumed"]


def test_raw_aliases_connect_candidate_groups_transitively() -> None:
    first = source_row("rpw:RPW/1/2026", b"first")
    second = source_row("rpw:RPW/2/2026", b"second")
    bridge = source_row("rpw:RPW/3/2026", b"first")
    bridge["downloads"] += second["downloads"]

    groups = grouped_candidates(
        {"rpw:RPW/1/2026": first, "rpw:RPW/2/2026": second, "rpw:RPW/3/2026": bridge}
    )

    assert len({groups[f"rpw:RPW/{number}/2026"] for number in range(1, 4)}) == 1


def test_pending_human_package_excludes_archive_aliases_and_never_exposes_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tools.corpus_holdout.source_fingerprints", lambda: {})
    arguments = preparation(tmp_path)
    target = tmp_path / "holdout.json"

    run(*arguments, target)

    result = json.loads(target.read_bytes())
    assert result["group_count"] == 1
    assert [row["id"] for row in result["cases"]] == ["rpw:RPW/3/2026"]
    assert result["rejected"]["rpw:RPW/2/2026"] == ["raw_bytes_already_consumed"]
    case = result["cases"][0]
    assert case["reviewer"] is None and case["oracle"] is None
    assert case["split"] == "holdout_pending" and case["read_scope"] == "not_read"
    assert set(case["questions"].values()) == {"unknown"}
    assert "%PDF-unread-fresh-body" not in target.read_text()
    assert "%PDF-unread-fresh-body" not in target.with_suffix(".html").read_text()
    with pytest.raises(ValueError, match="new"):
        run(*arguments, target)


def test_changed_consumed_source_or_unsealed_new_source_is_rejected(tmp_path: Path) -> None:
    arguments = preparation(tmp_path)
    (arguments[0] / "old.zip").write_bytes(b"changed")
    with pytest.raises(ValueError, match="Consumed raw source changed"):
        run(*arguments, tmp_path / "holdout.json")
    (arguments[5] / "downloads/new.pdf").write_bytes(b"changed")
    with pytest.raises(ValueError, match="artifact SHA/size mismatch"):
        run(*arguments, tmp_path / "holdout-second.json")


def test_holdout_viewer_escapes_names_and_source_metadata() -> None:
    result = viewer(
        [
            {
                "id": "rpw:1",
                "title": "<script>source</script>",
                "group": "rpw:1",
                "originals": [
                    {
                        "name": "<img src=x>",
                        "sha256": "f" * 64,
                        "local_uri": "file:///safe.pdf",
                    }
                ],
            }
        ]
    )
    assert "<script>source</script>" not in result
    assert "&lt;img src=x&gt;" in result
