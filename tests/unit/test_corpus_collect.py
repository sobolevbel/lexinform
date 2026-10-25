import json
from pathlib import Path

import httpx2 as httpx
import pytest
from tools.corpus_artifacts import digest
from tools.corpus_collect import collect, create_only, install
from tools.corpus_http import attachment_extension, fetch
from tools.corpus_runs import execute, verify

from tests.harness import rcl_document, rcl_folder, rcl_project, rcl_stage
from tests.unit.test_physical_extraction import docx


def test_public_fetch_records_redirects_and_limits_bytes_before_rejecting_html() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"location": "/file"})
        return httpx.Response(200, content=b"<html>refused</html>", headers={"etag": "source-etag"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = fetch(client, "https://legislacja.rcl.gov.pl/redirect")
        assert result.provenance["final_url"] == "https://legislacja.rcl.gov.pl/file"
        assert result.provenance["etag"] == "source-etag"
        with pytest.raises(ValueError, match="HTML"):
            attachment_extension(result.data, "pdf")
        with pytest.raises(ValueError, match="byte limit"):
            fetch(client, "https://legislacja.rcl.gov.pl/file", max_bytes=2)
        with pytest.raises(ValueError, match="allowlisted"):
            fetch(client, "https://private.test/file")


def test_failed_source_does_not_hide_an_independent_download_and_installation(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "corpus"
    for relative in ("corpus", "documents/files", "documents/text"):
        (corpus / relative).mkdir(parents=True, exist_ok=True)
    index = corpus / "documents/index.json"
    index.write_text('{"1":{"extension":"pdf","chars":5}}')
    legacy = corpus / "documents/files/1.pdf"
    legacy.write_bytes(b"old original")
    project = rcl_project(
        stages=(
            rcl_stage(
                1,
                "Projekt",
                "reached",
                rcl_folder(
                    1,
                    "Projekt",
                    rcl_document(999, "Projekt ustawy.docx"),
                    rcl_document(1000, "Autopoprawka.pdf"),
                ),
            ),
        )
    )
    (corpus / f"corpus/{project.id}.json").write_text(project.model_dump_json())
    health = tmp_path / "health.json"
    health.write_text(
        json.dumps(
            {
                "status": "recorded",
                "read_inputs": {"documents/index.json": digest(index.read_bytes())},
                "actions": [
                    {
                        "id": str(i),
                        "action": "fetch_source",
                        "urls": [f"https://legislacja.rcl.gov.pl/dokument{i}.{ext}"],
                    }
                    for i, ext in ((999, "docx"), (1000, "pdf"))
                ],
            }
        )
    )
    source = docx("USTAWA Art. 1. Termin 30 dni.")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=source if "999" in request.url.path else b"<html>refused</html>"
        )

    runs = tmp_path / "runs"
    before = {p: p.read_bytes() for p in corpus.rglob("*") if p.is_file()}

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        collected = collect(corpus, health, digest(health.read_bytes()), runs, "download", client)

    assert collected["status"] == "recorded"
    assert {p: p.read_bytes() for p in corpus.rglob("*") if p.is_file()} == before
    assert verify(runs / "download") == collected
    result = install(corpus, runs / "download", runs, "install")
    assert result["status"] == "recorded"
    rows = json.loads(index.read_bytes())
    assert rows["1"] == {"extension": "pdf", "chars": 5}
    assert rows["999"]["file_sha256"] == digest(source)
    assert legacy.read_bytes() == b"old original"
    assert (runs / "install/index-before.json").read_bytes() == before[index]
    assert not (corpus / "documents/files/1000.pdf").exists()
    assert not (corpus / "documents/text/1000.txt.gz").exists()
    again = install(corpus, runs / "download", runs, "install-again")
    assert again["status"] == "recorded"
    assert (
        json.loads((runs / "install-again/result.json").read_bytes())["observations"]["999"][
            "state"
        ]
        == "already_installed"
    )
    with pytest.raises(ValueError, match="explicit collector"):
        execute(runs, "not-offline", "sources", corpus, [])


def test_existing_originals_are_never_replaced(tmp_path: Path) -> None:
    path = tmp_path / "source.pdf"
    path.write_bytes(b"historical")
    with pytest.raises(ValueError, match="Refusing to replace"):
        create_only(path, b"new bytes")
    assert path.read_bytes() == b"historical"
