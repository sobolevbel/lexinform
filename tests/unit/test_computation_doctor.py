import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Literal

import pytest
from tools.computation_doctor import audit, run

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    AnalysisRecord,
    ComputationInputBinding,
    DocumentSelection,
    ResultApplication,
    SupplementRecord,
)
from lexinform.models.batch import (
    BatchItemMeta,
    DigestItemMeta,
    FrozenRequestManifest,
    LlmBatch,
    LlmBatchItem,
)
from lexinform.models.computations import ProjectionReason
from tests.computations import NOW, SHA, retained_result
from tests.unit.test_scan_input import loaded_scan


def test_projection_census_classifies_saved_receipts_without_changing_default(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    batch = retained_result("analysis")
    bound = batch.model_copy(
        update={"input_binding": ComputationInputBinding(term=10, number="3039", reuse_key="key")}
    )
    legacy = retained_result("reanalysis")
    reasons: tuple[ProjectionReason, ...] = ("batch_result", "memo_reuse", "retained_result")
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        repo.save_computation_result(bound)
        repo.save_computation_result(legacy)
        collection = ResultApplication(
            result_id=bound.result_id,
            term=10,
            number="3039",
            generation=0,
            outcome="ready",
            reason="current_generation",
            observed_generation=0,
            observed_status=None,
            created_at=bound.collected_at,
        )
        repo.save_result_application(collection)
        for generation, reason in enumerate(reasons):
            repo.save_result_application(
                ResultApplication(
                    result_id=legacy.result_id if reason == "memo_reuse" else bound.result_id,
                    term=10,
                    number="3039",
                    generation=generation,
                    phase="projection",
                    outcome="applied",
                    reason=reason,
                    observed_generation=generation,
                    observed_status=None,
                    created_at=bound.collected_at,
                )
            )
        repo.save_result_application(collection.model_copy(update={"generation": 1}))
        repo.save_result_application(collection.model_copy(update={"generation": 2}))
    with closing(sqlite3.connect(path)) as database, database:
        database.execute(
            "UPDATE result_applications SET application_json='{}'"
            " WHERE phase='collection' AND generation=1"
        )
        database.execute(
            "UPDATE result_applications SET number='wrong'"
            " WHERE phase='collection' AND generation=2"
        )
    before = path.read_bytes()

    default = audit(path, include_supplement_deliveries=True)
    output = tmp_path / "report.json"
    code = run(path, output, include_supplement_deliveries=True, include_projection_receipts=True)
    report = json.loads(output.read_text())
    census = report.pop("projection_receipt_census")

    assert code == 1
    assert report == default
    assert census["status"] == "recorded"
    assert census["counts"] == {
        "application_rows": 6,
        "collection_rows": 1,
        "invalid_rows": 2,
        "projections_by_reason": {
            "batch_result": {"input_binding_known": 1, "input_binding_unknown": 0},
            "memo_reuse": {"input_binding_known": 0, "input_binding_unknown": 1},
            "retained_result": {"input_binding_known": 1, "input_binding_unknown": 0},
        },
    }
    assert path.read_bytes() == before


@pytest.mark.parametrize("missing", [False, True])
def test_projection_census_distinguishes_missing_schema_from_empty_population(
    tmp_path: Path, missing: bool
) -> None:
    path = tmp_path / "state.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
    if missing:
        with closing(sqlite3.connect(path)) as database, database:
            database.execute("DROP TABLE result_applications")

    report = audit(path, include_projection_receipts=True)

    assert report["status"] == "not_run"
    census = report["projection_receipt_census"]
    assert census["status"] == ("schema_unavailable" if missing else "recorded")
    if missing:
        assert census["counts"] is None
    else:
        assert census["counts"]["application_rows"] == 0


@pytest.mark.parametrize("kind", ["analysis", "supplement"])
@pytest.mark.parametrize(
    "variant",
    ["bound", "missing_receipt", "missing_result_scan", "different_scan", "different_request"],
)
def test_retained_scan_requires_the_exact_consumed_request(
    tmp_path: Path,
    kind: Literal["analysis", "supplement"],
    variant: str,
) -> None:
    path = tmp_path / "state.db"
    _, document = loaded_scan(cover_letter=False)
    scan = document.scan_input
    assert scan is not None, "the scan loader supplies an input manifest"
    submitted = (
        scan.model_copy(update={"input_sha256": "f" * 64}) if variant == "different_scan" else scan
    )
    request_sha = "b" * 64 if variant == "different_request" else SHA
    manifest = FrozenRequestManifest(
        provider="openai",
        purpose=kind,
        request_sha256=request_sha,
        model="gpt-5.1",
        prompt_version="test",
        payload_bytes=1,
        messages_sha256=SHA,
        output_format_sha256=SHA,
        options_sha256=SHA,
    )
    result = retained_result(kind)
    record = result.record
    retained_scan = None if variant == "missing_result_scan" else scan
    meta: BatchItemMeta | DigestItemMeta
    if isinstance(record, AnalysisRecord):
        record = record.model_copy(
            update={"selection": DocumentSelection(scan_input=retained_scan)}
        )
        meta = BatchItemMeta(
            input_chars=0,
            truncated=False,
            text_source="pdf",
            source_kind="print",
            source_url=None,
            revision=0,
            text_sha256=None,
            selection=DocumentSelection(scan_input=submitted),
            request_sha256=request_sha,
            frozen_request_manifest=manifest,
        )
    else:
        assert isinstance(record, SupplementRecord), "the other fixture is a supplement"
        record = record.model_copy(update={"scan_input": retained_scan})
        meta = DigestItemMeta(
            memo_key="scan",
            scan_input=submitted,
            request_sha256=request_sha,
            frozen_request_manifest=manifest,
        )
    batch = LlmBatch(
        batch_id=result.batch_id,
        provider=result.provider,
        call_kind=kind,
        submitted_at=NOW,
        status="ended",
        request_count=1,
        estimated_cost_usd=0,
    )
    item = LlmBatchItem(
        batch_id=batch.batch_id,
        custom_id=result.custom_id,
        call_kind=kind,
        term=result.term,
        number=result.number,
        meta=meta,
    )
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        if variant != "missing_receipt":
            repo.save_llm_batch(batch, [item])
            repo.mark_llm_batch_item_consumed(batch.batch_id, item.custom_id, consumed_at=NOW)
        repo.save_computation_result(result.model_copy(update={"record": record}))

    report = audit(path)

    if variant == "bound":
        assert report["summary"]["retained_scan_bound"] == 1
    elif variant in ("missing_receipt", "missing_result_scan"):
        assert report["summary"]["retained_scan_unverified"] == 1
        if variant == "missing_result_scan":
            assert report["summary"]["retained_scan_result_manifest_unavailable"] == 1
            assert {
                "code": "retained_scan_result_manifest_unavailable",
                "level": "warning",
                "ref": f"computation_results/{result.result_id}",
            } in report["findings"]
    else:
        code = f"retained_scan_{'manifest' if variant == 'different_scan' else 'request'}_mismatch"
        assert {
            "code": code,
            "level": "error",
            "ref": f"computation_results/{result.result_id}",
        } in report["findings"]
