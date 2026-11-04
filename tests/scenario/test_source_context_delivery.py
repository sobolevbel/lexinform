from typing import Never

from lexinform.models import (
    OutcomeStatus,
    ProcessDetail,
    RunMode,
)
from tests.harness import CHANNEL, World
from tests.source_context_cases import queued


def forbidden() -> Never:
    raise AssertionError("a reserved block cannot reach a checkpoint or publisher callback")


def test_operator_retry_cannot_resolve_or_clear_a_reserved_block(
    process_3039: ProcessDetail,
) -> None:
    current = World()
    publication = queued(current.repo, process_3039, current.clock.now(), channel=CHANNEL)
    assert publication.id is not None, "queued stores the command's exact target"
    current.command(f"/delivery {publication.id} retry")
    current.run(mode=RunMode.COMMANDS, discover=False, track=False, max_analyze=0, max_publish=0)
    outcome = current.replier.replies[-1][1]
    assert outcome.status is OutcomeStatus.ERROR and "source context" in outcome.note
    assert current.repo.publication_by_id(publication.id) == publication
    assert not current.repo.delivery_resolutions(publication.id)
    assert not current.publisher.sent and not current.publisher.edited
