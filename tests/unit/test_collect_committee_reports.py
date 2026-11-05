import gzip
import json
from pathlib import Path

import httpx2 as httpx
import pytest
import tools.collect_committee_reports as collector
from tools.collect_committee_reports import collect, freeze_plan
from tools.corpus_artifacts import digest
from tools.corpus_sejm_sources import install


def corpus(root: Path) -> Path:
    root.mkdir()
    (root / "INDEX.json").write_text("{}")
    target = root / "sejm/term10"
    target.mkdir(parents=True)
    records = {
        str(i): {
            "number": str(i),
            "term": 10,
            "title": "Official project",
            "changeDate": "2026-11-01T00:00:00",
            "stages": [
                {
                    "stageType": "CommitteeReport",
                    "printNumber": f"{i}-A",
                    "proposal": "przyjąć poprawki",
                    "reportFile": f"https://api.sejm.gov.pl/sejm/term10/prints/{i}-A/{i}-A.pdf",
                }
            ],
        }
        for i in range(1, 15)
    }
    (target / "process_details.json.gz").write_bytes(gzip.compress(json.dumps(records).encode()))
    return root


def test_plan_is_frozen_deterministic_unique_and_cannot_overwrite(tmp_path: Path) -> None:
    root = corpus(tmp_path / "corpus")
    pin = digest((root / "INDEX.json").read_bytes())
    plan = tmp_path / "plan.json"
    value = freeze_plan(root, plan, index_sha256=pin)
    assert len(value["reports"]) == 12 and value["eligible_unique_urls"] == 14
    assert [row["id"] for row in value["reports"]] == sorted(row["id"] for row in value["reports"])
    with pytest.raises(FileExistsError):
        freeze_plan(root, plan, index_sha256=pin)
    with pytest.raises(ValueError):
        freeze_plan(root, root / "plan.json", index_sha256=pin)


def test_redirect_receipts_and_create_only_generation(tmp_path: Path) -> None:
    root = corpus(tmp_path / "corpus")
    plan = tmp_path / "plan.json"
    freeze_plan(root, plan, index_sha256=digest((root / "INDEX.json").read_bytes()))
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        if len(seen) == 1:
            return httpx.Response(302, headers={"Location": "https://evil.test/file.pdf"})
        return httpx.Response(200, content=b"%PDF-1.4\ncontrolled")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = collect(root, plan, digest(plan.read_bytes()), tmp_path / "runs", "one", client)
        assert len(seen) == 12 and next(iter(result["observations"].values()))["state"] == "error"
        assert (
            next(iter(result["observations"].values()))["http_receipt"]["failure_category"] == "url"
        )
        assert all(
            row["state"] == "downloaded" for row in list(result["observations"].values())[1:]
        )
        assert (tmp_path / "runs/one/manifest.json").is_file()
        assert all("evil.test" not in u for u in seen)
        with pytest.raises(FileExistsError):
            collect(root, plan, digest(plan.read_bytes()), tmp_path / "runs", "one", client)


def test_manifest_tamper_or_source_change_prevents_network(tmp_path: Path) -> None:
    root = corpus(tmp_path / "corpus")
    plan = tmp_path / "plan.json"
    freeze_plan(root, plan, index_sha256=digest((root / "INDEX.json").read_bytes()))
    pin = digest(plan.read_bytes())

    def handler(req: httpx.Request) -> httpx.Response:
        raise AssertionError("Invalid plan must not fetch")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="SHA"):
            collect(root, plan, "a" * 64, tmp_path / "runs", "bad", client)
        forged = json.loads(plan.read_text())
        forged["reports"][0]["url"] = "https://api.sejm.gov.pl/not-an-official-report.pdf"
        changed = tmp_path / "forged.json"
        changed.write_text(json.dumps(forged))
        with pytest.raises(ValueError, match="differs"):
            collect(
                root, changed, digest(changed.read_bytes()), tmp_path / "runs", "forged", client
            )
        (root / "INDEX.json").write_text('{"changed":true}')
        with pytest.raises(ValueError):
            collect(root, plan, pin, tmp_path / "runs", "changed", client)


def test_size_limits_and_html_are_errors_without_replacing_planned_urls(tmp_path: Path) -> None:
    root = corpus(tmp_path / "corpus")
    plan = tmp_path / "plan.json"
    freeze_plan(root, plan, index_sha256=digest((root / "INDEX.json").read_bytes()))
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200, headers={"Content-Length": str(9 * 1024 * 1024)}, content=b""
            )
        return httpx.Response(200, content=b"<html>Unavailable</html>")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = collect(root, plan, digest(plan.read_bytes()), tmp_path / "runs", "limits", client)
    assert calls == 12 and result["downloaded_bytes"] == 0
    assert all(row["state"] == "error" for row in result["observations"].values())
    assert not list((tmp_path / "runs/limits/downloads").iterdir())


def test_total_budget_and_time_guard_preserve_unattempted_planned_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(collector, "TOTAL_BYTES", 23)
    monkeypatch.setattr(collector, "FILE_BYTES", 10)
    monkeypatch.setattr(collector, "CHUNK_BYTES", 8)
    root = corpus(tmp_path / "corpus")
    plan = tmp_path / "plan.json"
    freeze_plan(root, plan, index_sha256=digest((root / "INDEX.json").read_bytes()))
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"%PDF-")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = collect(root, plan, digest(plan.read_bytes()), tmp_path / "runs", "bytes", client)
        assert calls == 3 and result["downloaded_bytes"] == result["observed_body_bytes"] == 15
        assert sum(r["state"] == "not_attempted" for r in result["observations"].values()) == 9
        monkeypatch.setattr(collector, "TOTAL_SECONDS", 0)
        timed = tmp_path / "timed.json"
        freeze_plan(root, timed, index_sha256=digest((root / "INDEX.json").read_bytes()))
        result = collect(root, timed, digest(timed.read_bytes()), tmp_path / "runs", "time", client)
    assert calls == 3
    assert all(r["reason"] == "collection_time_limit" for r in result["observations"].values())


def test_filing_installer_rejects_a_committee_generation_before_corpus_writes(
    tmp_path: Path,
) -> None:
    root = corpus(tmp_path / "corpus")
    plan = tmp_path / "plan.json"
    freeze_plan(root, plan, index_sha256=digest((root / "INDEX.json").read_bytes()))

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"%PDF-1.4\ncontrolled")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        collect(root, plan, digest(plan.read_bytes()), tmp_path / "runs", "committee", client)
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="Committee-report generations"):
        install(root, tmp_path / "runs/committee", tmp_path / "installs", "rejected")
    assert {
        str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()
    } == before
    assert not (tmp_path / "installs").exists()
