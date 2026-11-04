from pathlib import Path

import pytest
from tools.content_history_doctor import audit as content_audit
from tools.work_doctor import audit as work_audit

from lexinform.models import (
    AmendmentRevisionApplicationWork,
    Publication,
    PublicationStatus,
)
from lexinform.services.content_history import restore_content_delivery
from tests.harness import World
from tests.scenario.test_request_doctor import snapshot
from tests.scenario.test_revision_application_runtime import collected_work


def completed_revision() -> tuple[World, Publication, AmendmentRevisionApplicationWork]:
    with pytest.MonkeyPatch.context() as patch:
        world, pending = collected_work(patch)
    world.analysis.collect_batches()
    world.run()
    work = world.repo.get_revision_application_work(pending.key)
    assert work is not None and work.revision_publication_id is not None, (
        "the real batch collection completes its saved application work"
    )
    revision = world.repo.publication_by_id(work.revision_publication_id)
    assert revision is not None and revision.status is PublicationStatus.SENT, (
        "the real delivery service confirms the revision through the fake publisher"
    )
    return world, revision, work


def test_completed_revision_prunes_payload_and_preserves_supported_history_and_next_command(
    tmp_path: Path,
) -> None:
    world, revision, work = completed_revision()
    assert revision.id is not None and revision.delivery is not None, (
        "the real fake lifecycle confirms the complete physical payload"
    )
    version = world.repo.get_publication_content(revision.id)
    assert version is not None, "the delivery service binds its rendered editorial version"
    versions = world.repo.list_content_versions(work.original_publication_id)
    results = world.repo.list_computation_results(10, "1929")
    receipts = world.repo.list_result_applications(10, "1929")
    world.clock.advance(days=365)
    counts = world.repo.prune_history(before=world.clock.now(), memo_before=world.clock.now())
    expected = revision.model_copy(update={"delivery": None})
    assert counts["delivery_payloads"] > 0
    assert world.repo.publication_by_id(revision.id) == expected
    assert world.repo.list_amendment_revisions(work.original_publication_id) == [expected]
    assert world.repo.get_revision_application_work(work.key) == work
    assert (
        world.repo.list_revision_application_work(publication_id=work.original_publication_id) == []
    )
    assert world.repo.get_publication_content(revision.id) == version
    assert world.repo.list_content_versions(work.original_publication_id) == versions
    assert restore_content_delivery(world.repo, version) == revision.delivery
    assert world.repo.list_computation_results(10, "1929") == results
    assert world.repo.list_result_applications(10, "1929") == receipts
    pruned = world.repo.dump()
    world.repo.restore(pruned)
    assert world.repo.dump() == pruned
    path = snapshot(world, tmp_path)
    assert content_audit(path)["summary"]["errors"] == 0
    assert work_audit(path)["summary"]["errors"] == 0
    calls = list(world.analysis.calls)
    world.analysis.collect_batches()
    assert world.repo.dump() == pruned and world.analysis.calls == calls
    world.command("/amendments 1929 force")
    world.run(track=False)
    assert len(world.batch.submitted) == 2
    world.batch.resolve()
    world.run()
    assert len(world.repo.list_amendment_revisions(work.original_publication_id)) == 2
    assert world.repo.publication_by_id(revision.id) == expected
