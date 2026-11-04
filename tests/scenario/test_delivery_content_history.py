import datetime as dt

from lexinform.models import OutcomeStatus, PublicationStatus
from tests.scenario.test_amendments_command import published_world


def test_history_command_inspects_unknown_revision_after_cleanup_without_delivery_or_model_work() -> (
    None
):
    world, original = published_world()
    assert original.id is not None, "the fixture persists the original publication"
    world.command("/amendments 1929")
    world.run(track=False)
    world.batch.resolve()
    world.publisher.unknown_on_edit.add("1929")
    world.run()
    revision = world.repo.list_amendment_revisions(original.id)[0]
    assert revision.id is not None and revision.status is PublicationStatus.UNKNOWN, (
        "the simulated edit is uncertain and requires an audited resolution"
    )
    content = world.repo.get_publication_content(revision.id)
    assert content is not None, "the revision owns a frozen content version"
    future = world.clock.now() + dt.timedelta(days=181)
    world.repo.prune_history(before=future, memo_before=future)
    publications = world.repo.list_content_publications(original.id)
    versions = world.repo.list_content_versions(original.id)
    decisions = world.repo.delivery_resolutions(revision.id)
    effects = (len(world.batch.submitted), len(world.publisher.sent), len(world.publisher.edited))

    world.command(f"/delivery {revision.id} history 2")
    world.run(track=False, publish=False)

    outcome = world.replier.replies[-1][1]
    assert outcome.status is OutcomeStatus.DELIVERY
    page = outcome.content_history_page
    assert page is not None and page.status == "available" and page.version is not None, (
        "read-only history remains available with publishing disabled"
    )
    assert page.version.content_id == content.content_id
    assert page.history is not None and page.history.current_id != content.content_id, (
        "an uncertain physical edit never becomes confirmed current through inspection"
    )
    assert world.repo.list_content_publications(original.id) == publications
    assert world.repo.list_content_versions(original.id) == versions
    assert world.repo.delivery_resolutions(revision.id) == decisions
    assert effects == (
        len(world.batch.submitted),
        len(world.publisher.sent),
        len(world.publisher.edited),
    )
