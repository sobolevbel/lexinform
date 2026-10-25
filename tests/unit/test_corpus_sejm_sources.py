import io
import json
from pathlib import Path

import httpx2 as httpx
import pytest
from pypdf import PdfWriter
from tools.corpus_runs import verify
from tools.corpus_sejm_sources import catalogue, collect, install, publish_catalogue, requirement
from tools.corpus_snapshot import supplementary


def source_corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "corpus"
    for relative in (
        "sejm/snapshots/old/term10",
        "sejm/snapshots/new/term10",
        "sejm/term10/supplements/records",
        "sejm/term10/supplements/files",
        "sejm/term10/supplements/text",
        "rpw/term10/files",
        "rpw/term10/text",
    ):
        (corpus / relative).mkdir(parents=True, exist_ok=True)
    for name in ("old", "new"):
        filings = (
            []
            if name == "old"
            else [{"number": "1-s", "title": "Stanowisko Rządu", "attachments": ["1-s.pdf"]}]
        )
        (corpus / f"sejm/snapshots/{name}/term10/prints.json").write_text(
            json.dumps([{"number": "1", "title": "Bill", "additionalPrints": filings}])
        )
    (corpus / "sejm/snapshots/new/term10/bills.json").write_text(
        json.dumps(
            [
                {"number": "RPW/2/2026", "title": "New bill", "submissionType": "BILL"},
                {
                    "number": "RPW/3/2026",
                    "title": "Reserved bill",
                    "submissionType": "BILL",
                    "applicantType": "DEPUTIES",
                },
            ]
        )
    )
    (corpus / "rpw/term10/index.json").write_text('{"old":{"number":"RPW/1/2026"}}')
    return corpus


def source_plan(corpus: Path, runs: Path) -> Path:
    result = catalogue(
        corpus,
        Path("sejm/snapshots/new/term10"),
        Path("sejm/snapshots/old/term10"),
        runs,
        "plan",
        10,
        {"RPW/2/2026": "submission source regression"},
        {"1-s": "filed source regression"},
        {"RPW/3/2026": "pending human holdout"},
    )
    assert result["status"] == "recorded"
    return runs / "plan"


def pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_catalogue_keeps_parent_occurrences_and_requires_explicit_snapshot_membership(
    tmp_path: Path,
) -> None:
    corpus = source_corpus(tmp_path)
    runs = tmp_path / "runs"
    plan = source_plan(corpus, runs)
    data = json.loads((plan / "result.json").read_bytes())
    assert data["catalogue"] == {"rows": 1, "unique_ids": 1}
    assert data["diff"] == {"added": ["1-s"], "changed": [], "removed": []}
    assert data["observations"]["rpw:RPW/3/2026"]["reserved_holdout"]
    assert verify(plan)["status"] == "recorded"
    (corpus / "sejm/snapshots/2026-10-25/term10").mkdir(parents=True)
    target = publish_catalogue(corpus, plan, "2026-10-25")
    assert json.loads(target.read_bytes())[0]["parent"] == "1"
    assert publish_catalogue(corpus, plan, "2026-10-25") == target
    target.write_text("[]")
    with pytest.raises(ValueError, match="replace"):
        publish_catalogue(corpus, plan, "2026-10-25")
    rows = [
        {"number": "1", "additionalPrints": [{"number": "1-s"}]},
        {"number": "2", "additionalPrints": [{"number": "1-s"}]},
    ]
    assert [row["parent"] for row in supplementary(rows)] == ["1", "2"]
    with pytest.raises(ValueError, match="snapshot"):
        catalogue(
            corpus,
            Path("sejm/snapshots/new/term10"),
            Path("sejm/snapshots/old/term10"),
            runs,
            "bad",
            10,
            {"RPW/999/2026": "absent"},
            {},
        )
    with pytest.raises(ValueError, match="purpose"):
        requirement("RPW/2/2026")


def test_sejm_downloads_are_bounded_and_reserved_holdout_is_never_extracted(tmp_path: Path) -> None:
    corpus = source_corpus(tmp_path)
    runs = tmp_path / "runs"
    plan = source_plan(corpus, runs)
    raw = pdf()
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=raw)

    before = {path: path.read_bytes() for path in corpus.rglob("*") if path.is_file()}
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = collect(corpus, plan, runs, "download", client)
    assert result["status"] == "recorded" and len(seen) == 3
    assert {path: path.read_bytes() for path in corpus.rglob("*") if path.is_file()} == before
    result = install(corpus, runs / "download", runs, "install")
    assert result["status"] == "recorded"
    index = json.loads((corpus / "rpw/term10/index.json").read_bytes())
    assert index["old"] == {"number": "RPW/1/2026"}
    assert index["RPW-2-2026"]["pages"] == 1
    assert "RPW-3-2026" not in index
    assert not (corpus / "rpw/term10/files/RPW-3-2026.pdf").exists()
    assert not (corpus / "rpw/term10/text/RPW-3-2026.txt.gz").exists()
    assert (corpus / "sejm/term10/supplements/files/1-s__1-s.pdf").read_bytes() == raw
    installed = json.loads((runs / "install/result.json").read_bytes())
    assert "rpw:RPW/3/2026" not in installed["observations"]
    assert (runs / "install/rpw-index-before.json").read_bytes() == before[
        corpus / "rpw/term10/index.json"
    ]
    result = install(corpus, runs / "download", runs, "repeat")
    assert result["status"] == "recorded"
    repeated = json.loads((runs / "repeat/result.json").read_bytes())
    assert all(row["state"] == "already_installed" for row in repeated["observations"].values())


def test_html_refusal_preserves_other_downloads_and_changed_inputs_block_fetch(
    tmp_path: Path,
) -> None:
    corpus = source_corpus(tmp_path)
    runs = tmp_path / "runs"
    plan = source_plan(corpus, runs)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"<html>WAF</html>" if "RPW-2" in request.url.path else pdf()
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = collect(corpus, plan, runs, "download", client)
        assert result["status"] == "recorded"
        report = json.loads((runs / "download/result.json").read_bytes())
        assert report["observations"]["rpw:RPW/2/2026"]["state"] == "unavailable"
        assert "HTML" in report["observations"]["rpw:RPW/2/2026"]["downloads"][0]["error"]
        assert report["observations"]["supplement:1-s"]["state"] == "downloaded"
        (corpus / "rpw/term10/index.json").write_text("{}")
        with pytest.raises(ValueError, match="changed"):
            collect(corpus, plan, runs, "stale", client)


def test_download_refuses_a_redirect_outside_public_sources(tmp_path: Path) -> None:
    corpus = source_corpus(tmp_path)
    runs = tmp_path / "runs"
    plan = source_plan(corpus, runs)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://private.example/file.pdf"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        collect(corpus, plan, runs, "redirect", client)
    report = json.loads((runs / "redirect/result.json").read_bytes())
    assert len(seen) == 3 and not any("private.example" in url for url in seen)
    assert all(row["state"] == "unavailable" for row in report["observations"].values())
