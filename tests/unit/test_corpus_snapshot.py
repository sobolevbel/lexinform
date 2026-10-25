import json
from pathlib import Path

import httpx2 as httpx
import pytest
from tools.corpus_runs import verify
from tools.corpus_snapshot import LISTINGS, listing, publish, snapshot


def test_snapshot_preserves_old_metadata_and_reports_added_and_changed_rows(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    reference = corpus / "sejm/snapshots/2026-10-03/term10"
    reference.mkdir(parents=True)
    for family in LISTINGS:
        (reference / f"{family}.json").write_text('[{"number":"1","title":"Old"}]')
    before = {p: p.read_bytes() for p in reference.iterdir()}

    def handler(request: httpx.Request) -> httpx.Response:
        rows = (
            []
            if request.url.params.get("offset") == "2"
            else [{"number": "1", "title": "New"}, {"number": "2", "title": "Added"}]
        )
        return httpx.Response(200, json=rows)

    runs = tmp_path / "runs"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = snapshot(reference, runs, "fresh", 10, client)
    assert result["status"] == "recorded" and verify(runs / "fresh") == result
    report = json.loads((runs / "fresh/result.json").read_bytes())
    assert report["observations"]["prints"]["changed"] == ["1"]
    assert report["observations"]["prints"]["added"] == ["2"]
    target = publish(runs / "fresh", corpus, "2026-10-25")
    assert json.loads((target / "term10/bills.json").read_bytes())[1]["number"] == "2"
    assert {p: p.read_bytes() for p in reference.iterdir()} == before
    with pytest.raises(FileExistsError):
        publish(runs / "fresh", corpus, "2026-10-25")


def test_repeated_page_is_rejected_without_guessing_the_end() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"number": "1"}])

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(ValueError, match="repeated"),
    ):
        listing(client, 10, "bills")
