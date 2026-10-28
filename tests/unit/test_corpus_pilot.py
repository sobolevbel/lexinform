import fcntl
import json
import logging
import os
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
from tools import corpus_pilot
from tools.corpus_artifacts import digest
from tools.corpus_http import fetch
from tools.corpus_pilot import (
    BODY_BYTES,
    CHUNK_BYTES,
    METADATA_BYTES,
    TOTAL_BYTES,
    UNKNOWN_BYTES,
    PilotPlan,
    finalize_pilot,
    metadata_reservation,
    pilot,
    pilot_child,
    pilot_inputs,
    receipt_charge,
)
from tools.corpus_runs import verify

from tests.harness import rcl_document, rcl_folder, rcl_project, rcl_stage
from tests.unit.test_physical_extraction import docx


@dataclass(frozen=True)
class PilotFixture:
    corpus: Path
    plan: Path
    root: Path

    @property
    def pin(self) -> str:
        return digest(self.plan.read_bytes())


def json_file(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def artifact_pin(path: Path) -> dict[str, Any]:
    return {"path": str(path), "sha256": digest(path.read_bytes()), "bytes": path.stat().st_size}


def pilot_fixture(tmp_path: Path, count: int = 2, extension: str = "docx") -> PilotFixture:
    corpus = tmp_path / "corpus"
    for relative in ("corpus", "documents/files", "documents/text"):
        (corpus / relative).mkdir(parents=True, exist_ok=True)
    json_file(corpus / "INDEX.json", {})
    json_file(corpus / "documents/index.json", {})
    documents = tuple(
        rcl_document(key, f"Projekt.{extension}").model_copy(
            update={"url": f"https://legislacja.rcl.gov.pl/dokument{key}.{extension}"}
        )
        for key in range(100, 100 + count)
    )
    project = rcl_project(
        stages=(rcl_stage(1, "Projekt", "reached", rcl_folder(1, "Projekt", *documents)),)
    )
    catalog = corpus / f"corpus/{project.id}.json"
    catalog.write_text(project.model_dump_json())
    candidates = tmp_path / "candidates.json"
    caches = [f"documents/text/{document.id}.txt.gz" for document in documents]
    json_file(
        candidates,
        {
            "status": "recorded",
            "missing_inputs": caches,
            "read_inputs": dict.fromkeys(caches),
        },
    )
    inputs = {
        "index": artifact_pin(corpus / "INDEX.json"),
        "document_index": artifact_pin(corpus / "documents/index.json"),
        "candidates": artifact_pin(candidates),
    }
    readiness = tmp_path / "readiness.json"
    json_file(readiness, {"coverage": {"inputs": {row["path"]: row for row in inputs.values()}}})
    inputs["readiness"] = artifact_pin(readiness)
    plan = tmp_path / "plan.json"
    json_file(
        plan,
        {
            "version": 1,
            "kind": "rcl-gap-originals-pilot-v1",
            "status": "planned; not executable by the uncapped legacy collector",
            "input_pins": inputs,
            "sources": [
                {
                    "id": str(document.id),
                    "url": document.url,
                    "declared_extension": extension,
                    "project": project.id,
                    "stage": 1,
                    "folder_kind": None,
                    "catalog_path": f"corpus/{project.id}.json",
                    "catalog_sha256": digest(catalog.read_bytes()),
                    "historical_created": None,
                    "expected_raw_sha256": None,
                    "fetched_at": None,
                }
                for document in documents
            ],
            "limits": {
                "max_source_ids": 20,
                "max_source_attempts": 20,
                "workers": 1,
                "automatic_retries": 0,
                "max_body_bytes_per_attempt": BODY_BYTES,
                "max_total_application_bytes": TOTAL_BYTES,
                "reserved_body_bytes": 20 * BODY_BYTES,
                "max_stream_chunk_bytes": CHUNK_BYTES,
                "max_budget_for_source_snapshots_receipts_and_logs": METADATA_BYTES,
                "redirect_hops_per_attempt_max": 6,
                "http_operation_timeout_seconds": 15,
                "elapsed_fetch_guard_seconds": 60,
                "network_allowed_now": False,
                "interpretation": "Application body bytes plus retained originals and metadata",
            },
            "selection": {},
            "provenance_limits": ["Container validity is not a legal role"],
        },
    )
    return PilotFixture(corpus, plan, tmp_path / "runs")


def corpus_bytes(corpus: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(corpus)): path.read_bytes()
        for path in corpus.rglob("*")
        if path.is_file()
    }


def report(path: Path) -> dict[str, Any]:
    value: dict[str, Any] = json.loads((path / "result.json").read_bytes())
    return value


def final_id(staged: Path) -> str:
    return f"final-{digest((staged / 'manifest.json').read_bytes())}"


def test_pilot_seals_dispositions_before_offline_finalization_without_publishing(
    tmp_path: Path,
) -> None:
    fixture = pilot_fixture(tmp_path)
    before = corpus_bytes(fixture.corpus)
    calls: list[str] = []
    original = docx("USTAWA Art. 1. Termin 30 dni.")
    refused = b"<html>SECRET rejection body</html>"

    def handler(request: httpx.Request) -> httpx.Response:
        key = "100" if "100" in request.url.path else "101"
        intent = fixture.root / "pilot/children" / f"slot-{key}" / "started.json"
        assert json.loads(intent.read_bytes())["phase"] == "request_started"
        assert request.headers["accept-encoding"] == "identity"
        calls.append(key)
        return httpx.Response(200, content=original if key == "100" else refused)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        manifest = pilot(fixture.corpus, fixture.plan, fixture.pin, fixture.root, "pilot", client)
        assert (
            pilot(
                fixture.corpus,
                fixture.plan,
                fixture.pin,
                fixture.root,
                "pilot",
                client,
                resume=True,
            )
            == manifest
        )
        with pytest.raises(FileExistsError):
            pilot(fixture.corpus, fixture.plan, fixture.pin, fixture.root, "pilot", client)
    staged = fixture.root / "pilot"
    assert calls == ["100", "101"]
    assert corpus_bytes(fixture.corpus) == before
    assert verify(staged) == manifest
    summary = report(staged)
    assert summary["observations"]["100"]["state"] == "downloaded"
    assert summary["observations"]["101"]["state"] == "unavailable"
    assert summary["application_bytes_observed"] == len(original) + len(refused)
    assert summary["retained_bytes_observed"] == len(original)
    assert summary["charged_application_and_retained_bytes"] == len(original) * 2 + len(refused)
    assert all(b"SECRET" not in path.read_bytes() for path in staged.rglob("*.json"))
    final = finalize_pilot(
        fixture.corpus,
        fixture.plan,
        fixture.pin,
        staged,
        digest((staged / "manifest.json").read_bytes()),
        fixture.root,
        final_id(staged),
    )
    finalized = fixture.root / final_id(staged)
    assert final["status"] == "recorded"
    assert verify(finalized / "bundle/pilot") == manifest
    saved = report(finalized)
    assert saved["observations"]["100"]["sha256"] == digest(original)
    assert saved["observations"]["101"]["state"] == "unavailable"
    assert (finalized / "downloads/100.docx").read_bytes() == original
    assert corpus_bytes(fixture.corpus) == before
    before_repeat = corpus_bytes(fixture.root)
    assert (
        finalize_pilot(
            fixture.corpus,
            fixture.plan,
            fixture.pin,
            staged,
            digest((staged / "manifest.json").read_bytes()),
            fixture.root,
            final_id(staged),
        )
        == final
    )
    assert corpus_bytes(fixture.root) == before_repeat
    snapshot = saved["pilot_dependency_verified"]["shared_snapshot_sha256"]
    assert saved["retained_accounting"]["unique_retained_bytes"] == len(original)
    assert saved["retained_accounting"]["additional_retained_body_bytes"] == 0
    shutil.rmtree(staged)
    assert verify(finalized)["status"] == "recorded"
    child, _ = pilot_child(
        finalized / "bundle/pilot/children/slot-100", "100", fixture.pin, snapshot
    )
    assert child["sha256"] == digest(original)


@pytest.mark.parametrize(
    "mutation",
    ["population", "duplicate", "limit", "extra", "host", "credentials", "port", "edition"],
)
def test_pilot_rejects_unpinned_or_unsafe_plan_before_request(
    tmp_path: Path, mutation: str
) -> None:
    fixture = pilot_fixture(tmp_path)
    data = json.loads(fixture.plan.read_bytes())
    match mutation:
        case "population":
            data["sources"] = data["sources"] * 11
        case "duplicate":
            data["sources"][1] = data["sources"][0]
        case "limit":
            data["limits"]["workers"] = 2
        case "extra":
            data["install"] = True
        case "host":
            data["sources"][0]["url"] = "https://private.test/dokument100.docx"
        case "credentials":
            data["sources"][0]["url"] = "https://user:SECRET@legislacja.rcl.gov.pl/dokument100.docx"
        case "port":
            data["sources"][0]["url"] = "https://legislacja.rcl.gov.pl:8443/dokument100.docx"
        case "edition":
            data["sources"][0]["expected_raw_sha256"] = "0" * 64
    json_file(fixture.plan, data)

    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("Invalid plan reached HTTP")

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(ValueError, match="plan is invalid"),
    ):
        pilot(fixture.corpus, fixture.plan, fixture.pin, fixture.root, "pilot", client)


@pytest.mark.parametrize(
    "changed",
    ["plan", "index", "document_index", "candidates", "readiness", "catalog", "raw", "cache"],
)
def test_pilot_preflight_binds_metadata_and_absent_originals(tmp_path: Path, changed: str) -> None:
    fixture = pilot_fixture(tmp_path)
    pin = fixture.pin
    data = json.loads(fixture.plan.read_bytes())
    if changed == "catalog":
        path = fixture.corpus / data["sources"][0]["catalog_path"]
    elif changed == "raw":
        path = fixture.corpus / "documents/files/100.pdf"
    elif changed == "cache":
        path = fixture.corpus / "documents/text/100.txt.gz"
    else:
        path = fixture.plan if changed == "plan" else Path(data["input_pins"][changed]["path"])
    path.write_bytes(path.read_bytes() + b" " if path.exists() else b"new")
    with pytest.raises(ValueError):
        pilot_inputs(fixture.corpus, fixture.plan, pin)


def test_official_plan_preserves_uppercase_original_extension(tmp_path: Path) -> None:
    fixture = pilot_fixture(tmp_path)
    data = json.loads(fixture.plan.read_bytes())
    data["sources"][0]["url"] = data["sources"][0]["url"].replace(".docx", ".DOCX")
    plan = PilotPlan.model_validate_json(json.dumps(data))
    assert plan.sources[0].url.endswith(".DOCX")


@pytest.mark.parametrize(
    ("status", "headers", "body", "category"),
    [
        (403, {}, b"SECRET", "http_status"),
        (206, {}, b"partial", "http_status"),
        (200, {"Content-Range": "bytes 0-2/20"}, b"abc", "response_headers"),
        (200, {"Content-Encoding": "gzip"}, b"abc", "response_headers"),
        (200, {"Content-Length": "no"}, b"abc", "response_headers"),
        (200, {"Content-Length": "100"}, b"abc", "response_headers"),
        (200, {"Content-Length": "7"}, b"abc", "incomplete_body"),
        (200, {}, b"abcdefghijk", "byte_limit"),
    ],
)
def test_strict_fetch_never_assigns_complete_identity_to_partial_or_refused_body(
    status: int,
    headers: dict[str, str],
    body: bytes,
    category: str,
) -> None:
    receipt: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers=headers, stream=httpx.ByteStream(body))

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises((ValueError, httpx.HTTPStatusError)),
    ):
        fetch(
            client,
            "https://legislacja.rcl.gov.pl/dokument100.pdf",
            max_bytes=8,
            chunk_size=4,
            strict=True,
            receipt=receipt,
        )
    assert receipt["failure_category"] == category
    assert receipt["complete"] is False
    assert "sha256" not in receipt
    assert "SECRET" not in json.dumps(receipt)


class BrokenBody(httpx.SyncByteStream):
    def __iter__(self) -> Iterator[bytes]:
        yield b"partial"
        raise httpx.ReadError("SECRET transport body")


def test_strict_fetch_records_completion_headers_and_safe_redirect_provenance() -> None:
    receipt: dict[str, Any] = {}
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(302, headers={"Location": "/file?SECRET=token"})
        return httpx.Response(200, headers={"ETag": "a" * 1000}, content=b"abc")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = fetch(
            client,
            "https://legislacja.rcl.gov.pl/start",
            strict=True,
            chunk_size=4,
            receipt=receipt,
        )
    assert len(calls) == 2
    assert result.data == b"abc"
    assert receipt["complete"] is True
    assert receipt["sha256"] == digest(b"abc")
    assert receipt["final_url"] == "https://legislacja.rcl.gov.pl/file"
    assert receipt["final_query_sha256"] == digest(b"SECRET=token")
    assert len(receipt["headers"]["etag"]) == 256
    assert "SECRET" not in json.dumps(receipt)


def test_strict_fetch_clears_intermediate_query_hash_on_queryless_final_url() -> None:
    receipt: dict[str, Any] = {}
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/start":
            return httpx.Response(302, headers={"Location": "/intermediate?nonce=one"})
        if request.url.path == "/intermediate":
            return httpx.Response(302, headers={"Location": "/final"})
        return httpx.Response(200, content=b"abc")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        fetch(client, "https://legislacja.rcl.gov.pl/start", strict=True, receipt=receipt)
    assert len(calls) == 3
    assert receipt["complete"] is True
    assert receipt["final_url"] == "https://legislacja.rcl.gov.pl/final"
    assert "final_query_sha256" not in receipt


@pytest.mark.parametrize("valid", [False, True])
def test_strict_fetch_clears_reused_query_receipt_before_request(valid: bool) -> None:
    receipt: dict[str, Any] = {}
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"abc"))
    ) as client:
        fetch(client, "https://legislacja.rcl.gov.pl/first?nonce=one", strict=True, receipt=receipt)
        assert receipt["final_query_sha256"] == digest(b"nonce=one")
        if valid:
            fetch(client, "https://legislacja.rcl.gov.pl/final", strict=True, receipt=receipt)
        else:
            with pytest.raises(ValueError, match="allowlisted public HTTPS"):
                fetch(client, "https://private.test/final", strict=True, receipt=receipt)
    assert "final_query_sha256" not in receipt


@pytest.mark.parametrize("kind", ["external", "http", "credentials", "port", "loop", "broken"])
def test_strict_fetch_redirects_and_stream_failures_are_terminal(kind: str) -> None:
    receipt: dict[str, Any] = {}
    calls = 0
    locations = {
        "external": "https://private.test/x",
        "http": "http://legislacja.rcl.gov.pl/x",
        "credentials": "https://user:SECRET@legislacja.rcl.gov.pl/x",
        "port": "https://legislacja.rcl.gov.pl:8443/x",
        "loop": "/again",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if kind == "broken":
            return httpx.Response(200, stream=BrokenBody())
        return httpx.Response(302, headers={"location": locations[kind]})

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises((ValueError, httpx.ReadError)),
    ):
        fetch(
            client,
            "https://legislacja.rcl.gov.pl/start",
            strict=True,
            chunk_size=4,
            receipt=receipt,
        )
    assert calls == (6 if kind == "loop" else 1)
    assert not receipt["complete"]
    assert "sha256" not in receipt
    assert "SECRET" not in json.dumps(receipt)
    assert receipt_charge(receipt, 0) == (BODY_BYTES + CHUNK_BYTES if kind == "broken" else 0)


def test_resume_never_retries_unknown_request_and_keeps_conservative_charge(tmp_path: Path) -> None:
    fixture = pilot_fixture(tmp_path)
    calls: list[str] = []

    def interrupted(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        raise KeyboardInterrupt

    previous = logging.root.manager.disable
    with (
        httpx.Client(transport=httpx.MockTransport(interrupted)) as client,
        pytest.raises(KeyboardInterrupt),
    ):
        pilot(fixture.corpus, fixture.plan, fixture.pin, fixture.root, "pilot", client)
    assert logging.root.manager.disable == previous
    assert (fixture.root / "pilot/children/slot-100/started.json").exists()

    def resumed(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, content=docx("Art. 2. Termin 7 dni."))

    with httpx.Client(transport=httpx.MockTransport(resumed)) as client:
        pilot(fixture.corpus, fixture.plan, fixture.pin, fixture.root, "pilot", client, resume=True)
    summary = report(fixture.root / "pilot")
    assert calls == ["/dokument100.docx", "/dokument101.docx"]
    assert summary["observations"]["100"]["state"] == "unknown"
    assert summary["observations"]["100"]["charged_bytes"] == UNKNOWN_BYTES
    assert summary["unknown_attempts"] == 1
    assert summary["application_bytes_charged"] > summary["application_bytes_observed"]
    assert summary["retained_bytes_charged"] > summary["retained_bytes_observed"]
    staged = fixture.root / "pilot"
    finalize_pilot(
        fixture.corpus,
        fixture.plan,
        fixture.pin,
        staged,
        digest((staged / "manifest.json").read_bytes()),
        fixture.root,
        final_id(staged),
    )
    assert report(fixture.root / final_id(staged))["observations"]["100"]["state"] == "unknown"


def test_pilot_reserves_combined_body_storage_and_stops_before_next_request(tmp_path: Path) -> None:
    fixture = pilot_fixture(tmp_path, count=20, extension="xml")
    raw = b"<?xml version='1.0'?><x>" + b"a" * (BODY_BYTES - 28) + b"</x>"
    assert len(raw) == BODY_BYTES
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=raw)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        pilot(fixture.corpus, fixture.plan, fixture.pin, fixture.root, "pilot", client)
    summary = report(fixture.root / "pilot")
    assert calls == 10
    assert (
        sum(row["state"] == "unattempted_budget" for row in summary["observations"].values()) == 10
    )
    assert summary["application_bytes_charged"] == 10 * BODY_BYTES
    assert summary["retained_bytes_charged"] == 10 * BODY_BYTES
    assert summary["charged_application_and_retained_bytes"] + METADATA_BYTES <= TOTAL_BYTES
    staged = fixture.root / "pilot"
    extra = staged / "downloads/119.xml"
    extra.parent.mkdir()
    extra.write_bytes(raw)
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda request: pytest.fail("Extra body reached HTTP"))
        ) as client,
        pytest.raises(ValueError, match="source reservation"),
    ):
        pilot(fixture.corpus, fixture.plan, fixture.pin, fixture.root, "pilot", client, resume=True)


def test_resume_charges_later_unknown_slots_before_earlier_unattempted_sources(
    tmp_path: Path,
) -> None:
    fixture = pilot_fixture(tmp_path, count=20)

    def interrupted(request: httpx.Request) -> httpx.Response:
        raise KeyboardInterrupt

    with (
        httpx.Client(transport=httpx.MockTransport(interrupted)) as client,
        pytest.raises(KeyboardInterrupt),
    ):
        pilot(fixture.corpus, fixture.plan, fixture.pin, fixture.root, "pilot", client)
    for key in range(110, 119):
        (fixture.root / f"pilot/children/slot-{key}").mkdir()
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: pytest.fail("Unknown reservations reached HTTP")
        )
    ) as client:
        pilot(fixture.corpus, fixture.plan, fixture.pin, fixture.root, "pilot", client, resume=True)
    saved = report(fixture.root / "pilot")
    assert saved["unknown_attempts"] == 10
    assert sum(row["state"] == "unattempted_budget" for row in saved["observations"].values()) == 10
    assert saved["charged_application_and_retained_bytes"] == 10 * UNKNOWN_BYTES


@pytest.mark.parametrize("kind", ["metadata", "raw", "symlink", "unowned_slot"])
def test_resume_refuses_unbounded_or_aliased_staging_before_http(tmp_path: Path, kind: str) -> None:
    fixture = pilot_fixture(tmp_path)

    def interrupted(request: httpx.Request) -> httpx.Response:
        raise KeyboardInterrupt

    with (
        httpx.Client(transport=httpx.MockTransport(interrupted)) as client,
        pytest.raises(KeyboardInterrupt),
    ):
        pilot(fixture.corpus, fixture.plan, fixture.pin, fixture.root, "pilot", client)
    staged = fixture.root / "pilot"
    if kind == "metadata":
        (staged / "extra.json").write_bytes(b"x" * METADATA_BYTES)
    elif kind == "raw":
        raw = staged / "children/slot-100/downloads"
        raw.mkdir()
        (raw / "100.docx").write_bytes(b"x" * (BODY_BYTES + 1))
    elif kind == "symlink":
        (staged / "alias").symlink_to(fixture.plan)
    else:
        (staged / "children/slot-999").mkdir()
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda request: pytest.fail("Unsafe resume reached HTTP"))
        ) as client,
        pytest.raises(ValueError),
    ):
        pilot(fixture.corpus, fixture.plan, fixture.pin, fixture.root, "pilot", client, resume=True)


def test_pilot_large_archive_reservation_stops_before_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = pilot_fixture(tmp_path)
    monkeypatch.setattr(corpus_pilot, "metadata_reservation", lambda *args: METADATA_BYTES + 1)
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda request: pytest.fail("Budget reached HTTP"))
        ) as client,
        pytest.raises(ValueError, match="reservation"),
    ):
        pilot(fixture.corpus, fixture.plan, fixture.pin, fixture.root, "pilot", client)
    assert not (fixture.root / "pilot").exists()
    assert metadata_reservation(METADATA_BYTES, 1, 20) > METADATA_BYTES


@pytest.mark.parametrize("sealed", [False, True])
def test_resume_rejects_undeclared_retained_inode_before_http(tmp_path: Path, sealed: bool) -> None:
    fixture = pilot_fixture(tmp_path)
    raw = docx("Art. 1.")

    def initial(request: httpx.Request) -> httpx.Response:
        if not sealed and "101" in request.url.path:
            raise KeyboardInterrupt
        return httpx.Response(200, content=raw)

    with httpx.Client(transport=httpx.MockTransport(initial)) as client:
        if sealed:
            pilot(fixture.corpus, fixture.plan, fixture.pin, fixture.root, "pilot", client)
        else:
            with pytest.raises(KeyboardInterrupt):
                pilot(fixture.corpus, fixture.plan, fixture.pin, fixture.root, "pilot", client)
    staged = fixture.root / "pilot"
    extra = staged / "children/slot-100/downloads/100.docx.tmp"
    extra.write_bytes(raw)
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda request: pytest.fail("Inventory reached HTTP"))
        ) as client,
        pytest.raises(ValueError, match="undeclared artifacts"),
    ):
        pilot(fixture.corpus, fixture.plan, fixture.pin, fixture.root, "pilot", client, resume=True)
    assert extra.read_bytes() == raw


def test_resume_preserves_owned_unknown_partial_body_in_final_bundle(tmp_path: Path) -> None:
    fixture = pilot_fixture(tmp_path)

    def interrupted(request: httpx.Request) -> httpx.Response:
        raise KeyboardInterrupt

    with (
        httpx.Client(transport=httpx.MockTransport(interrupted)) as client,
        pytest.raises(KeyboardInterrupt),
    ):
        pilot(fixture.corpus, fixture.plan, fixture.pin, fixture.root, "pilot", client)
    staged = fixture.root / "pilot"
    partial = staged / "children/slot-100/downloads/100.docx.tmp"
    partial.parent.mkdir()
    partial.write_bytes(b"incomplete original")
    (partial.parent.parent / "manifest.json").write_bytes(b'{"unfinished":')
    calls: list[str] = []

    def remaining(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, content=docx("Art. 2."))

    with httpx.Client(transport=httpx.MockTransport(remaining)) as client:
        pilot(fixture.corpus, fixture.plan, fixture.pin, fixture.root, "pilot", client, resume=True)
    assert calls == ["/dokument101.docx"]
    assert report(staged)["observations"]["100"]["state"] == "unknown"
    finalized = fixture.root / final_id(staged)
    finalize_pilot(
        fixture.corpus,
        fixture.plan,
        fixture.pin,
        staged,
        digest((staged / "manifest.json").read_bytes()),
        fixture.root,
        finalized.name,
    )
    copied = finalized / "bundle/pilot" / partial.relative_to(staged)
    assert copied.read_bytes() == b"incomplete original"
    assert copied.samefile(partial)
    assert report(finalized)["observations"]["100"]["state"] == "unknown"


@pytest.mark.parametrize("fail_parent_sync", [False, True])
def test_request_intent_and_ancestor_entries_are_durable_before_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_parent_sync: bool
) -> None:
    fixture = pilot_fixture(tmp_path, count=1)
    synced: set[tuple[int, int]] = set()
    original_sync = os.fsync
    calls = 0

    def sync(descriptor: int) -> None:
        stat = os.fstat(descriptor)
        children = fixture.root / "pilot/children"
        if fail_parent_sync and children.exists():
            parent = children.stat()
            if (stat.st_dev, stat.st_ino) == (parent.st_dev, parent.st_ino):
                raise OSError("Injected parent directory fsync failure")
        original_sync(descriptor)
        synced.add((stat.st_dev, stat.st_ino))

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        child = fixture.root / "pilot/children/slot-100"
        for path in (child / "started.json", child, *child.parents):
            stat = path.stat()
            assert (stat.st_dev, stat.st_ino) in synced
        return httpx.Response(200, content=docx("Art. 1."))

    monkeypatch.setattr(os, "fsync", sync)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        if fail_parent_sync:
            with pytest.raises(OSError, match="parent directory fsync"):
                pilot(fixture.corpus, fixture.plan, fixture.pin, fixture.root, "pilot", client)
        else:
            pilot(fixture.corpus, fixture.plan, fixture.pin, fixture.root, "pilot", client)
    assert calls == (0 if fail_parent_sync else 1)


def test_pilot_holds_single_writer_lock_and_stops_after_input_drift(tmp_path: Path) -> None:
    fixture = pilot_fixture(tmp_path)
    fixture.root.mkdir()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        (fixture.corpus / "INDEX.json").write_text('{"changed":true}')
        return httpx.Response(200, content=docx("Art. 1."))

    with (fixture.root / ".pilot.pilot.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with (
            httpx.Client(transport=httpx.MockTransport(handler)) as client,
            pytest.raises(ValueError, match="active writer"),
        ):
            pilot(fixture.corpus, fixture.plan, fixture.pin, fixture.root, "pilot", client)
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(ValueError, match="pinned input changed"),
    ):
        pilot(fixture.corpus, fixture.plan, fixture.pin, fixture.root, "pilot", client)
    assert calls == 1
    assert not (fixture.root / "pilot/children/slot-101").exists()


@pytest.mark.parametrize("changed", ["shared", "child", "population", "parent_pin"])
def test_finalizer_rejects_changed_dependencies(tmp_path: Path, changed: str) -> None:
    fixture = pilot_fixture(tmp_path, count=1)
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=docx("Art. 1.")))
    ) as client:
        pilot(fixture.corpus, fixture.plan, fixture.pin, fixture.root, "pilot", client)
    staged = fixture.root / "pilot"
    pin = digest((staged / "manifest.json").read_bytes())
    if changed == "shared":
        (staged / "code.tar.gz").write_bytes(b"changed")
    elif changed == "child":
        (staged / "children/slot-100/manifest.json").write_bytes(b"changed")
    elif changed == "population":
        data = report(staged)
        data["observations"] = {}
        json_file(staged / "result.json", data)
    else:
        pin = "0" * 64
    with pytest.raises(ValueError):
        finalize_pilot(
            fixture.corpus, fixture.plan, fixture.pin, staged, pin, fixture.root, f"final-{pin}"
        )


def test_child_explicitly_verifies_shared_snapshot_dependency(tmp_path: Path) -> None:
    fixture = pilot_fixture(tmp_path, count=1)
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=docx("Art. 1.")))
    ) as client:
        pilot(fixture.corpus, fixture.plan, fixture.pin, fixture.root, "pilot", client)
    staged = fixture.root / "pilot"
    child = staged / "children/slot-100"
    snapshot = digest((staged / "code.tar.gz").read_bytes())
    (staged / "code.tar.gz").write_bytes(b"changed")
    assert verify(child)["status"] == "recorded"
    with pytest.raises(ValueError, match="shared snapshot"):
        pilot_child(child, "100", fixture.pin, snapshot)


def test_child_rejects_unmanifested_directory_alias_before_hashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = pilot_fixture(tmp_path, count=1)
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=docx("Art. 1.")))
    ) as client:
        pilot(fixture.corpus, fixture.plan, fixture.pin, fixture.root, "pilot", client)
    staged = fixture.root / "pilot"
    child = staged / "children/slot-100"
    snapshot = digest((staged / "code.tar.gz").read_bytes())
    (child / "alias").symlink_to(tmp_path, target_is_directory=True)

    def forbidden(directory: Path) -> dict[str, Any]:
        pytest.fail("Aliased child reached artifact hashing")

    monkeypatch.setattr(corpus_pilot, "verify", forbidden)
    with pytest.raises(ValueError, match="aliased artifacts"):
        pilot_child(child, "100", fixture.pin, snapshot)


def test_finalizer_rebinds_parent_pin_after_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = pilot_fixture(tmp_path, count=1)
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=docx("Art. 1.")))
    ) as client:
        pilot(fixture.corpus, fixture.plan, fixture.pin, fixture.root, "pilot", client)
    staged = fixture.root / "pilot"
    pin = digest((staged / "manifest.json").read_bytes())
    original_link = os.link

    def mutate(source: Path, target: Path) -> None:
        original_link(source, target)
        if target.parent == fixture.root / f"final-{pin}/downloads":
            (staged / "manifest.json").write_bytes((staged / "manifest.json").read_bytes() + b" ")

    monkeypatch.setattr(os, "link", mutate)
    with pytest.raises(ValueError):
        finalize_pilot(
            fixture.corpus, fixture.plan, fixture.pin, staged, pin, fixture.root, f"final-{pin}"
        )


def test_finalizer_refuses_uncharged_body_copy_instead_of_hardlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = pilot_fixture(tmp_path, count=1)
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=docx("Art. 1.")))
    ) as client:
        pilot(fixture.corpus, fixture.plan, fixture.pin, fixture.root, "pilot", client)
    staged = fixture.root / "pilot"
    pin = digest((staged / "manifest.json").read_bytes())

    def copy(source: Path, target: Path) -> None:
        shutil.copyfile(source, target)

    monkeypatch.setattr(os, "link", copy)
    with pytest.raises(ValueError, match="deduplication"):
        finalize_pilot(
            fixture.corpus, fixture.plan, fixture.pin, staged, pin, fixture.root, f"final-{pin}"
        )


def test_finalizer_rejects_alternate_destinations_and_preserves_partial_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = pilot_fixture(tmp_path, count=1)
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=docx("Art. 1.")))
    ) as client:
        pilot(fixture.corpus, fixture.plan, fixture.pin, fixture.root, "pilot", client)
    staged = fixture.root / "pilot"
    pin = digest((staged / "manifest.json").read_bytes())
    before = corpus_bytes(fixture.root)
    other_root = tmp_path / "alternate"
    for root, identity in ((other_root, f"final-{pin}"), (fixture.root, "another-final")):
        with pytest.raises(ValueError, match="deterministic sibling"):
            finalize_pilot(fixture.corpus, fixture.plan, fixture.pin, staged, pin, root, identity)
    assert not other_root.exists()
    assert corpus_bytes(fixture.root) == before
    original_link = os.link
    linked = False

    def interrupt(source: Path, target: Path) -> None:
        nonlocal linked
        original_link(source, target)
        linked = True
        raise OSError("Injected partial finalization")

    monkeypatch.setattr(os, "link", interrupt)
    with pytest.raises(OSError, match="partial finalization"):
        finalize_pilot(
            fixture.corpus, fixture.plan, fixture.pin, staged, pin, fixture.root, f"final-{pin}"
        )
    assert linked
    partial = corpus_bytes(fixture.root)
    monkeypatch.setattr(os, "link", original_link)
    with pytest.raises(ValueError, match="incomplete"):
        finalize_pilot(
            fixture.corpus, fixture.plan, fixture.pin, staged, pin, fixture.root, f"final-{pin}"
        )
    assert corpus_bytes(fixture.root) == partial


@pytest.mark.parametrize(
    "receipt",
    [
        {"bytes_read": True},
        {"bytes_read": BODY_BYTES + CHUNK_BYTES + 1},
        {"bytes_read": 1, "sha256": "0" * 64},
        {"bytes_read": 1, "complete": True},
    ],
)
def test_receipt_budget_rejects_invalid_completion_or_counts(receipt: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        receipt_charge(receipt, 0)
