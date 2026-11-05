import json
import os
from pathlib import Path

from tools.replay_content_applications import replay

from lexinform.models import AmendmentsRecord
from tests.computations import retained_result
from tests.unit.test_content_sources import Case
from tests.unit.test_content_sources import case as case


def test_public_original_revision_and_audited_recovery_baseline(case: Case) -> None:
    result = retained_result("amendments")
    assert isinstance(result.record, AmendmentsRecord), "the fixture requests amendments"
    result = result.model_copy(
        update={
            "record": result.record.model_copy(
                update={"source_url": "https://offline.invalid/technical-correction"}
            )
        }
    )
    report = replay(case.repo, case.bill, case.change, result, recovery=True)
    assert report["provider_calls"] == 0 and report["network_calls"] == 0
    assert report["original"]["sent"] == 1 and report["revision"]["edited"] == 1
    assert report["repeat_exact"] and report["revision"]["double_restore_exact"]
    destination = os.environ.get("LEXINFORM_APPLICATION_REPLAY_OUTPUT")
    if destination:
        Path(destination).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
