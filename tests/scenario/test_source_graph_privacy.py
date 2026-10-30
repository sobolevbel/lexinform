import hashlib

import pytest

from lexinform.models import ReviewSource, TextDocument
from tests.fakes import FakeTextExtractor
from tests.harness import World, print_url


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("prefilter", [False, True])
def test_unsafe_primary_and_prefilter_graphs_do_not_change_paid_work_or_publication(
    workers: int, prefilter: bool
) -> None:
    world = World(
        workers=workers,
        extractor=FakeTextExtractor(
            "USTAWA\nArt. 1. Cudzoziemiec uzyskuje zezwolenie na pobyt czasowy. " * 30
        ),
    )
    world.add_bill("4100", "Projekt ustawy o podatku" if prefilter else "Projekt o cudzoziemcach")
    original = world.gateway.prints["4100"]
    url = print_url("4100") + "?token=PRIVATE"
    world.gateway.prints["4100"] = original.model_copy(
        update={"attachments": (original.attachments[0].model_copy(update={"url": url}),)}
    )
    world.gateway.files[url] = b"%PDF"

    report = world.run(track=False)

    assert report.analyzed == report.published == 1 and len(world.llm.contexts) == 1
    assert report.text_prefilter_checked == int(prefilter)
    assert world.gateway.calls.count(f"download:{url}") == 1
    assert not world.repo.list_source_observations(10, "4100")
    assert not world.repo.list_artifact_reads()
    assert world.repo.get_artifact_metadata(hashlib.sha256(b"%PDF").hexdigest()) is not None
    assert report.metadata_url_omissions is not None and not report.errors


@pytest.mark.parametrize("compare_versions", [False, True])
def test_unsafe_review_graph_is_omitted_for_direct_read_and_candidate_selection(
    compare_versions: bool,
) -> None:
    world = World(
        consultation_reviews_enabled=True,
        batch_kinds=frozenset({"analysis", "reanalysis", "consultation_review"}),
    )
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    world.run(track=False)
    before = tuple(world.repo.list_source_observations(10, "4100"))
    url = print_url("4100") + "?token=PRIVATE"
    world.gateway.files[url] = b"%PDF-review"
    source = ReviewSource(
        identity="review/1",
        role="response",
        document=TextDocument(url=url, kind="print", compare_versions=compare_versions),
    )

    context = world.analysis.review_context(world.bill("4100"), "consultation_review", (source,))

    assert context is not None and len(context.documents) == 1
    assert tuple(world.repo.list_source_observations(10, "4100")) == before
    assert not world.repo.list_artifact_reads(requested_url=url)
    assert world.repo.get_artifact_metadata(hashlib.sha256(b"%PDF-review").hexdigest()) is not None
    assert world.container.text_loader().read_metadata_url_omissions() is not None
