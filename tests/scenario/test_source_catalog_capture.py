import pytest

from lexinform.models import Stage
from lexinform.ports import TextExtractor
from tests.fakes import FakeTextExtractor
from tests.harness import RCL, RPW, World, print_url, rcl_project, submission, submission_url
from tests.scenario.test_explain import commands_only
from tests.unit.test_source_catalog_capture import Cancelled


class MainFileCancellation(FakeTextExtractor):
    def __init__(self, failure: BaseException) -> None:
        super().__init__()
        self.failure = failure

    def extract(self, data: bytes) -> str:
        if data == b"%PDF":
            raise self.failure
        return super().extract(data)


@pytest.mark.parametrize("source", ["3039", RPW, RCL])
def test_private_failed_source_keeps_catalog_without_enrolling_owner(source: str) -> None:
    failure: BaseException
    extractor: TextExtractor
    if source == RCL:
        failure = Cancelled("PRIVATE cancellation")
        extractor = MainFileCancellation(failure)
    else:
        parser_failure = ValueError("PRIVATE parser")
        failure = parser_failure
        extractor = FakeTextExtractor(error=parser_failure)
    world = World(extractor=extractor)
    if source == "3039":
        stage = Stage(
            stage_name="III czytanie", stage_type="ThirdReading", text_after3=print_url(source)
        )
        world.add_bill(source, "Projekt ustawy o cudzoziemcach", stages=(stage,))
    elif source == RPW:
        world.gateway.submissions.append(submission())
        world.gateway.files[submission_url()] = b"%PDF"
    else:
        world.add_rcl_project(rcl_project(consultation=None))
    world.command(f"/explain {source}")
    if source == RCL:
        with pytest.raises(Cancelled) as caught:
            commands_only(world)
        assert caught.value is failure
    else:
        assert commands_only(world).commands_failed == 1
    assert (
        world.repo.get(10, source) is None
        and not world.batch.submitted
        and not world.publisher.new_bills
    )
    (attempt,) = world.repo.list_source_catalog_attempts()
    assert attempt.owner.kind == "source_reference" and attempt.owner.number == source
    assert attempt.error_class == type(failure).__name__ and attempt.parent_count > 0
    assert attempt.selection == "unknown" and "PRIVATE" not in attempt.model_dump_json()
    catalog = world.repo.get_source_catalog_snapshot(attempt.catalog_id)
    assert catalog is not None, "caller persists catalog before its dependent attempt"
    assert catalog.descriptors and not world.repo.list_source_observations(10, source)


def test_enrolled_prefilter_failed_single_source_is_positive_catalog_evidence() -> None:
    world = World(workers=4, extractor=FakeTextExtractor(error=ValueError("PRIVATE parser")))
    stage = Stage(
        stage_name="III czytanie", stage_type="ThirdReading", text_after3=print_url("4100")
    )
    world.add_bill("4100", "Projekt ustawy o podatku", stages=(stage,))
    world.run(track=False)
    attempts = world.repo.list_source_catalog_attempts()
    assert attempts and all(a.owner.kind == "bill" and a.owner.number == "4100" for a in attempts)
    assert any(a.owner.purpose == "prefilter" for a in attempts)
    assert all(a.selection == "unknown" and a.error_class == "ValueError" for a in attempts)
    assert not world.llm.contexts and not world.batch.submitted
