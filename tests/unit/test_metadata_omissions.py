import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.concurrency import fan_out
from lexinform.metadata_omissions import MetadataUrlOmissionCollector
from lexinform.metadata_urls import MetadataUrlOmissionReason
from lexinform.models import MetadataUrlOmissions, RunMode, RunReport
from lexinform.models.metadata_omissions import MetadataUrlOmissionExample


def sha(value: int) -> str:
    return hashlib.sha256(str(value).encode()).hexdigest()


def captured(order: list[int], *, workers: int = 1) -> MetadataUrlOmissions:
    collector = MetadataUrlOmissionCollector()

    def observe(index: int) -> None:
        collector.observe(
            "userinfo", requested_url_sha256=sha(index), artifact_sha256=sha(index + 1)
        )

    for outcome in fan_out(order, observe, workers=workers):
        outcome.result()
    snapshot = collector.snapshot()
    assert snapshot is not None, "the collector received omission observations"
    return snapshot


def test_bounded_partial_examples_are_order_worker_and_repeat_independent() -> None:
    order = list(range(10000)) * 2
    first = captured(order)
    assert captured(list(reversed(order)), workers=4) == first
    assert first.total == 20000 and first.reasons[0].count == 20000
    assert 0 < len(first.examples) <= 8
    assert [item.key for item in first.examples] == sorted(
        ("userinfo", sha(index), sha(index + 1)) for index in range(10000)
    )[: len(first.examples)]
    assert all(item.count == 2 for item in first.examples)
    assert first.omitted_observations == 20000 - sum(item.count for item in first.examples)
    assert len(first.model_dump_json().encode()) <= 2048
    assert MetadataUrlOmissions.model_validate_json(first.model_dump_json()) == first


def test_evicted_observations_never_reappear_and_snapshots_are_detached() -> None:
    collector = MetadataUrlOmissionCollector()
    assert collector.snapshot() is None
    for index in range(8):
        collector.observe("userinfo", requested_url_sha256=f"{index + 16:064x}")
    previous = collector.snapshot()
    collector.observe("userinfo", requested_url_sha256="0" * 64)
    collector.observe("userinfo", requested_url_sha256=f"{23:064x}", count=9)
    current = collector.snapshot()
    assert previous is not None and current is not None, "both snapshots follow observations"
    assert previous.total == 8 and current.total == 18
    assert f"{23:064x}" not in {item.requested_url_sha256 for item in current.examples}
    assert current.omitted_observations == 10
    collector.reset()
    assert collector.snapshot() is None and previous.total == 8
    with pytest.raises(ValidationError):
        current.__setattr__("total", 1)


def test_unavailable_hash_and_opaque_prose_are_explicit_without_url_or_message_fields() -> None:
    collector = MetadataUrlOmissionCollector()
    collector.observe("invalid_unicode", requested_url_sha256=None, artifact_sha256=sha(1))
    collector.observe("opaque_failure_prose", requested_url_sha256=None, count=2)
    value = collector.snapshot()
    assert value is not None, "the collector received two omission reasons"
    assert value.total == 3 and len(value.reasons) == 2
    assert all(item.requested_url_sha256 is None for item in value.examples)
    assert "requested_url_sha256" not in value.model_dump_json()
    assert "partial" in value.model_dump_json()


@pytest.mark.parametrize(
    "mutation",
    [
        "version",
        "policy",
        "total",
        "reason",
        "duplicate",
        "order",
        "excess",
        "unknown",
        "hash",
        "null",
        "unicode",
        "count",
        "float",
        "overflow",
        "cap",
    ],
)
def test_reader_rejects_incoherent_or_unbounded_diagnostics(mutation: str) -> None:
    value = captured([0, 1]).model_dump(mode="json")
    if mutation == "version":
        value["schema_version"] = True
    elif mutation == "policy":
        value["policy_version"] = "future"
    elif mutation == "total":
        value["total"] += 1
    elif mutation == "reason":
        value["reasons"][0]["reason"] = "fragment"
    elif mutation == "duplicate":
        value["examples"][1] = value["examples"][0]
    elif mutation == "order":
        value["examples"].reverse()
    elif mutation == "excess":
        value["omitted_observations"] = 1
    elif mutation == "unknown":
        value["examples"][0]["message"] = "secret"
    elif mutation == "hash":
        value["examples"][0]["requested_url_sha256"] = "secret"
    elif mutation == "null":
        value["examples"][0]["requested_url_sha256"] = None
    elif mutation == "unicode":
        value["examples"][0]["reason"] = "invalid_unicode"
    elif mutation == "count":
        value["reasons"][0]["count"] = True
    elif mutation == "float":
        value["total"] = 2.0
    elif mutation == "overflow":
        value["total"] = 2**63
    else:
        value = captured(list(range(8))).model_dump(mode="json")
        while len(value["examples"]) < 8:
            index = len(value["examples"])
            value["examples"].append(
                {
                    "reason": "userinfo",
                    "count": 1,
                    "requested_url_sha256": f"{index:064x}",
                    "artifact_sha256": sha(index),
                }
            )
        value["examples"].sort(
            key=lambda item: (item["reason"], item["requested_url_sha256"], item["artifact_sha256"])
        )
        value["total"] = 8
        value["reasons"][0]["count"] = 8
        value["omitted_observations"] = 0
        for item in value["examples"]:
            item["count"] = 10**17
            item["reason"] = "unrecognized_query"
        value["total"] = 8 * 10**17
        value["reasons"][0] = {"reason": "unrecognized_query", "count": 8 * 10**17}
    with pytest.raises(ValidationError):
        MetadataUrlOmissions.model_validate(value)


def test_bad_observation_and_overflow_leave_all_prior_counters_unchanged() -> None:
    collector = MetadataUrlOmissionCollector()
    collector.observe("userinfo", requested_url_sha256=sha(1), count=2**63 - 1)
    before = collector.snapshot()
    with pytest.raises(ValueError):
        collector.observe("userinfo", requested_url_sha256=sha(2))
    with pytest.raises(ValidationError):
        collector.observe(cast(MetadataUrlOmissionReason, "secret"), requested_url_sha256=sha(2))
    with pytest.raises(ValidationError):
        collector.observe("userinfo", requested_url_sha256=sha(2), count=0)
    assert collector.snapshot() == before


def test_example_hash_unavailability_is_not_a_claim_of_utf8_identity() -> None:
    with pytest.raises(ValidationError):
        MetadataUrlOmissionExample(reason="invalid_unicode", requested_url_sha256=sha(1), count=1)


def test_snapshot_trims_examples_at_cap_without_changing_retained_counters() -> None:
    collector = MetadataUrlOmissionCollector()
    for index in range(8):
        collector.observe(
            "unrecognized_query",
            requested_url_sha256=sha(index),
            artifact_sha256=sha(index + 1),
            count=10**17,
        )
    value = collector.snapshot()
    assert value is not None, "the collector received omission observations"
    assert value.total == 8 * 10**17 and 0 < len(value.examples) < 8
    assert value.omitted_observations == value.total - sum(item.count for item in value.examples)
    assert collector.snapshot() == value


def test_optional_diagnostic_survives_repository_dump_restore_and_quiet_repeat(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 10, 29, tzinfo=UTC)
    report = RunReport(
        started_at=now, since=now, mode=RunMode.RUN, metadata_url_omissions=captured([1, 2, 3])
    )
    repo = SqliteBillRepository(tmp_path / "source.db")
    restored = SqliteBillRepository(tmp_path / "restored.db")
    try:
        repo.migrate()
        run_id = repo.start_run(report)
        repo.finish_run(run_id, report)
        before = repo.dump()
        restored.restore(before)
        assert restored.list_runs(since=now) == [report]
        assert restored.dump() == before
        restored.finish_run(run_id, report)
        assert restored.dump() == before
    finally:
        restored.close()
        repo.close()


def test_optional_reader_keeps_legacy_report_bytes_and_roundtrips_new_metadata() -> None:
    now = datetime(2026, 10, 29, tzinfo=UTC)
    report = RunReport(started_at=now, since=now, mode=RunMode.RUN)
    raw = report.model_dump_json()
    assert "metadata_url_omissions" not in raw
    assert RunReport.model_validate_json(raw).model_dump_json() == raw
    report.metadata_url_omissions = captured([1, 2, 3])
    assert report.is_empty
    assert (
        RunReport.model_validate_json(report.model_dump_json()).model_dump_json()
        == report.model_dump_json()
    )
    report.metadata_url_omissions = None
    assert report.model_dump_json() == raw


def test_omission_diagnostics_preserve_execution_and_cli_read_result_semantics() -> None:
    now = datetime(2026, 10, 29, tzinfo=UTC)
    diagnostic = captured([1, 2, 3])
    report = RunReport(started_at=now, since=now, mode=RunMode.RUN)
    assert report.is_empty
    report.metadata_url_omissions = diagnostic
    assert RunReport.model_validate_json(report.model_dump_json()).is_empty
    assert not RunReport(
        started_at=now,
        since=now,
        mode=RunMode.RUN,
        metadata_url_omissions=diagnostic,
        errors=["actual execution failure"],
    ).is_empty
    reads = RunReport(
        started_at=now,
        since=now,
        mode=RunMode.CLI_READS,
        cli_read_operation="scan",
        metadata_url_omissions=diagnostic,
    )
    assert not reads.is_empty
