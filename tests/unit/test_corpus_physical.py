import gzip
import hashlib
import io
import json
from pathlib import Path

import pytest
from pypdf import PdfWriter
from tools.corpus_physical import SENATE_ROOT, container_state, inspect, run

from tests.unit.test_physical_extraction import archive, docx


def physical_corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    for part in (
        "documents/files",
        "documents/text",
        "sejm/term10/files",
        "sejm/term10/prints",
        "sejm/term10/text",
    ):
        (root / part).mkdir(parents=True)
    (root / "INDEX.json").write_text("{}")
    (root / "documents/index.json").write_text(
        json.dumps({"1": {"extension": "docx"}, "2": {"extension": "pdf"}})
    )
    (root / "documents/files/1.docx").write_bytes(docx("USTAWA"))
    (root / "documents/text/1.txt.gz").write_bytes(gzip.compress(b"USTAWA"))
    (root / "documents/text/2.txt.gz").write_bytes(b"broken gzip")
    (root / "sejm/term10/files/3__3.pdf").write_bytes(b"<html>Request Rejected</html>")
    (root / "sejm/term10/prints/3.json").write_text(
        json.dumps({"number": "3", "attachments": [{"name": "3.pdf"}]})
    )
    return root


def test_inventory_records_bad_payloads_missing_originals_and_missing_texts_without_repair(
    tmp_path: Path,
) -> None:
    root = physical_corpus(tmp_path)
    old = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}

    result = inspect(root)

    assert result["counts"] == {"valid_container": 1, "html_response": 1, "missing": 1}
    assert result["text_counts"] == {"readable": 1, "unreadable": 1, "missing": 1}
    assert result["by_family"]["rcl"] == {"valid_container": 1, "missing": 1}
    assert result["metadata"]["rpw"]["state"] == "missing"
    assert result["observations"]["documents/files/2.pdf"]["state"] == "missing"
    assert result["read_inputs"]["documents/files/2.pdf"] is None
    assert result["raw_totals"]["files"] == 2
    assert not result["changed_inputs"] and not result["changed_code"]
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == old


def test_container_checks_detect_crc_corruption_encryption_and_unpacking_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid = archive({"one.txt": b"ordinary bytes"})
    corrupted = valid.replace(b"ordinary bytes", b"modified bytes")
    assert container_state(valid)["state"] == "valid_container"
    assert container_state(corrupted)["state"] == "invalid_container"
    assert container_state(b"PK\x03\x04broken")["state"] == "invalid_container"
    monkeypatch.setattr("tools.corpus_physical.MAX_EXPANDED", 2)
    assert container_state(valid)["state"] == "limit_exceeded"
    stream = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(100, 100)
    writer.encrypt("password")
    writer.write(stream)
    assert container_state(stream.getvalue()) == {"state": "encrypted", "format": "pdf"}


def test_inventory_refuses_symlinks_and_historical_report_overwrite(tmp_path: Path) -> None:
    root = physical_corpus(tmp_path)
    output = tmp_path / "inventory.json"
    run(root, output)
    saved = output.read_bytes()
    with pytest.raises(ValueError, match="new and outside"):
        run(root, output)
    assert output.read_bytes() == saved
    with pytest.raises(ValueError, match="outside"):
        run(root, root / "report.json")
    (root / "documents/text/alias.txt.gz").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="symlink"):
        inspect(root)


def test_senate_sources_follow_catalog_identity_and_keep_missing_and_changed_sources_explicit(
    tmp_path: Path,
) -> None:
    root = physical_corpus(tmp_path)
    senate = root / SENATE_ROOT
    (senate / "files").mkdir(parents=True)
    (senate / "status").mkdir()
    (senate / "texts").mkdir()
    (senate / "catalog.json").write_text(
        json.dumps(
            {"records": [{"term": 10, "number": "3", "attachments": ["3.pdf", "3-uchwała.docx"]}]}
        )
    )
    identity = hashlib.sha256(b"https://api.sejm.gov.pl/sejm/term10/prints/3/3.pdf").hexdigest()
    raw = senate / f"files/{identity}.pdf"
    raw.write_bytes(b"%PDF-broken")
    (senate / f"status/{identity}.json").write_text(json.dumps({"sha256": "0" * 64}))
    (senate / f"texts/{identity}.txt.gz").write_bytes(gzip.compress(b""))
    (senate / "files/unlisted.docx").write_bytes(docx("Not a catalog source"))

    result = inspect(root)

    assert result["by_family"]["senate_resolution"] == {"recorded_hash_mismatch": 1, "missing": 1}
    assert result["observations"][str(raw.relative_to(root))]["container_state"] == "parse_failed"
    assert result["raw_totals"]["files"] == 3
    assert not any("unlisted" in path for path in result["observations"])
    assert result["text_counts"]["empty"] == 1
