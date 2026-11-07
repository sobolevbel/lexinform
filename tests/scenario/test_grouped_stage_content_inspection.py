from datetime import timedelta
from unittest.mock import patch

import pytest

from lexinform.errors import DeliveryUncertainError
from lexinform.services import posting
from lexinform.services.content_inspection import inspect_content, inspect_content_page
from tests.checkpoint_state import saved_state
from tests.scenario.test_group_remote_checkpoints import flush, queued_world, rows


@pytest.mark.parametrize("outcome", ["sent", "unknown", "legacy"])
def test_group_member_inspection_resolves_only_verified_history(
    outcome: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, identities = queued_world("updates")
    if outcome == "unknown":

        def uncertain(text: str) -> int:
            raise DeliveryUncertainError("response lost")

        monkeypatch.setattr(world.publisher, "publish_updates", uncertain)
        with pytest.raises(DeliveryUncertainError):
            flush(world, "updates", identities)
    elif outcome == "legacy":
        with patch.object(posting, "record_grouped_stage_content", return_value=None):
            flush(world, "updates", identities)
    else:
        flush(world, "updates", identities)
        before = world.clock.now() + timedelta(days=1)
        world.repo.prune_history(before=before, memo_before=before)
    baseline = world.repo.dump()

    with saved_state(baseline) as restored:
        root, second = rows(restored, identities)
        assert restored.repo.get_publication_content(identities[1]) is None
        history = inspect_content(restored.repo, root)
        assert inspect_content(restored.repo, second) == history
        if outcome == "legacy":
            assert history is None
        else:
            assert history is not None and history.integrity == "valid"
            assert (history.current_id is not None) == (outcome == "sent")
            assert inspect_content_page(restored.repo, second, page=1) == inspect_content_page(
                restored.repo, root, page=1
            )
            foreign = second.model_copy(update={"id": 9999})
            assert inspect_content(restored.repo, foreign) is None
        assert restored.repo.dump() == baseline
