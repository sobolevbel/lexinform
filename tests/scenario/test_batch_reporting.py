from collections.abc import Sequence

import pytest

from lexinform.models import BatchRequest
from tests.fakes import FakeBatchBackend
from tests.harness import World


def test_report_distinguishes_submitted_waiting_and_completed_analysis() -> None:
    w = World(batch=True)
    for number in ("1039", "1040"):
        w.add_bill(number, "Projekt ustawy o cudzoziemcach")
    submitted = w.run()
    text = w.formatter.run_report(submitted, []).text
    assert submitted.analyzed == 0
    assert submitted.batch_requests_submitted == 2
    assert submitted.batch_requests_pending == 2
    assert not submitted.is_empty
    assert "отправлено запросов в batch: 2" in text
    assert "nothing analyzed" not in text

    waiting = w.run()
    assert waiting.batch_requests_submitted == 0
    assert waiting.batch_requests_pending == 2
    assert "nothing analyzed" not in w.formatter.run_report(waiting, []).text

    w.batch.resolve()
    completed = w.run()
    assert completed.analyzed == 2
    assert completed.batch_requests_pending == 0
    assert completed.batch_requests_submitted == 0


def test_dry_run_does_not_claim_submission() -> None:
    w = World(batch=True)
    w.add_bill("1039", "Projekt ustawy o cudzoziemcach")
    report = w.run(dry_run=True)
    assert report.batch_requests_submitted == 0
    assert report.batch_requests_pending == 0
    assert not w.batch.submitted


def test_uncertain_submission_is_not_reported_as_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(self: FakeBatchBackend, requests: Sequence[BatchRequest]) -> str:
        raise TimeoutError("response lost")

    monkeypatch.setattr(FakeBatchBackend, "submit", fail)
    w = World(batch=True)
    w.add_bill("1039", "Projekt ustawy o cudzoziemcach")
    report = w.run(expect_bugs=True)
    assert report.batch_requests_submitted == 0
    assert report.batch_requests_pending == 0
    assert report.batch_requests_uncertain == 1
    assert "отправка batch не подтверждена: 1" in w.formatter.run_report(report, []).text
