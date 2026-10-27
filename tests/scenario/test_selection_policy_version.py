import pytest

from lexinform.document_selection import DOCUMENT_SELECTION_POLICY_VERSION
from lexinform.models import AnalysisRecord
from tests.fakes import FakeTextExtractor
from tests.harness import World


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("scanned", [False, True])
def test_policy_version_survives_batch_collection_projection_and_restore(
    workers: int, scanned: bool
) -> None:
    world = World(
        batch=True,
        batch_only=True,
        workers=workers,
        extractor=FakeTextExtractor("", page_count=12) if scanned else FakeTextExtractor(),
    )
    world.add_bill("4200", "Projekt ustawy o cudzoziemcach")
    world.run(track=False)
    world.batch.resolve()

    world.run(track=False)
    world.repo.restore(world.repo.dump())

    record = world.bill("4200").analysis
    assert record is not None, "the resolved fake batch supplies the bill's first analysis"
    assert record.selection.policy_version == DOCUMENT_SELECTION_POLICY_VERSION
    results = world.repo.list_computation_results(10, "4200", purpose="analysis")
    assert len(results) == 1
    paid = results[0].record
    assert isinstance(paid, AnalysisRecord), "validated analysis batches retain an AnalysisRecord"
    assert paid.selection.policy_version == DOCUMENT_SELECTION_POLICY_VERSION
    requests = len(world.batch.submitted)
    world.run(track=False)
    assert len(world.batch.submitted) == requests
