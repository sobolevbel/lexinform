import hashlib

import pytest

from lexinform.models import BatchIntent, BatchRequest
from lexinform.services.analysis import Waiting
from tests.scenario.test_secondary_scan_input import ask, ready_world


@pytest.mark.parametrize("uncertain", [False, True])
def test_payload_checksum_survives_queued_and_uncertain_intent_restore(uncertain: bool) -> None:
    world = ready_world()
    assert isinstance(ask(world, "supplement"), Waiting)
    intent = world.repo.list_queued_batch_intents()[0]
    expected = intent.request
    assert expected.payload_json is not None, "the backend froze the request before saving it"
    assert expected.payload_sha256 == hashlib.sha256(expected.payload_json.encode()).hexdigest()
    if uncertain:
        world.repo.mark_batch_intents_submitting([expected.custom_id])
    before = len(world.batch.submitted)
    world.repo.restore(world.repo.dump())
    world.analysis.start_run()
    restored = (
        world.repo.list_submitting_batch_intents()
        if uncertain
        else world.repo.list_queued_batch_intents()
    )
    assert restored[0].request == expected

    world.analysis.submit_queued_batches()

    assert len(world.batch.submitted) == before + int(not uncertain)
    if uncertain:
        assert world.repo.list_submitting_batch_intents()[0].request == expected
    else:
        assert world.batch.submitted[-1] == expected
    assert not world.llm.supplement_contexts


def test_legacy_queued_payload_is_submitted_unchanged_after_restore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = ready_world()
    save = world.repo.save_batch_intent

    def legacy_intent(intent: BatchIntent) -> None:
        old = BatchRequest.model_validate(intent.request.model_dump(exclude={"payload_sha256"}))
        save(intent.model_copy(update={"request": old}))

    monkeypatch.setattr(world.repo, "save_batch_intent", legacy_intent)
    assert isinstance(ask(world, "supplement"), Waiting)
    expected = world.repo.list_queued_batch_intents()[0].request
    world.repo.restore(world.repo.dump())
    world.analysis.start_run()

    world.analysis.submit_queued_batches()

    assert world.batch.submitted[-1] == expected
    assert expected.payload_sha256 is None and "payload_sha256" not in expected.model_dump()
