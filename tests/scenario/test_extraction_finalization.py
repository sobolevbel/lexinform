import datetime as dt
import threading

import pytest

from lexinform.models import Attachment, ExtractionResult
from tests.harness import World, print_url
from tests.unit.test_physical_extraction import archive, docx, extractor


@pytest.mark.parametrize("workers", [1, 4])
def test_pipeline_finalizer_keeps_both_extractions_after_required_part_blocks_the_graph(
    monkeypatch: pytest.MonkeyPatch, workers: int
) -> None:
    world = World(workers=workers, extractor=extractor())
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    main = print_url("4100")
    required = main + "?token=PRIVATE"
    original = world.gateway.prints["4100"]
    world.gateway.prints["4100"] = original.model_copy(
        update={
            "attachments": (
                Attachment(print_number="4100", name="4100-cz1.pdf", url=main),
                Attachment(print_number="4100", name="4100-cz2.pdf", url=required),
            )
        }
    )
    world.gateway.files[main] = docx("USTAWA\nArt. 1. Cudzoziemiec sklada wniosek. " * 30)
    world.gateway.files[required] = archive({})
    caller = threading.get_ident()
    writes: list[int] = []
    save = world.repo.save_extraction_result

    def record(result: ExtractionResult, *, recorded_at: dt.datetime) -> None:
        writes.append(threading.get_ident())
        save(result, recorded_at=recorded_at)

    monkeypatch.setattr(world.repo, "save_extraction_result", record)

    report = world.run(track=False)

    loader = world.container.text_loader()
    results = loader.read_extraction_results()
    assert len(results) == len(loader.read_artifacts()) == 2
    assert len(writes) >= 2 and set(writes) == {caller}
    assert all(world.repo.get_extraction_result(result.result_id) == result for result in results)
    assert not world.repo.list_source_observations(10, "4100")
    assert not world.llm.contexts and not report.errors
    assert report.analyzed == report.published == 0
