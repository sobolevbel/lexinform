from collections.abc import Sequence
from datetime import date, timedelta

import pytest

from lexinform.grouped_update_preview import GroupedUpdateRender
from lexinform.models import UpdateNews
from lexinform.services.grouped_update_preview import preview_retained_grouped_update
from tests.checkpoint_state import saved_state
from tests.scenario.test_group_remote_checkpoints import flush, queued_world, rows


def test_offline_preview_uses_saved_render_after_delivery_payload_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, identities = queued_world("updates")
    render = world.publisher.render_updates
    candidates: list[GroupedUpdateRender] = []

    def capture(news: Sequence[UpdateNews], *, today: date) -> str:
        first, second = news
        stages = first.change.new_stages
        when = stages[0].date
        assert when is not None, "the first stage provides a date to correct"
        corrected = stages[0].model_copy(update={"date": when + timedelta(days=1)})
        change = first.change.model_copy(update={"new_stages": (corrected, *stages[1:])})
        candidate = world.formatter.updates_with_witness(
            (first.model_copy(update={"change": change}), second), today=today
        )
        assert candidate is not None, "the explicit candidate remains a two-member render"
        candidates.append(candidate)
        return render(news, today=today)

    monkeypatch.setattr(world.publisher, "render_updates", capture)
    flush(world, "updates", identities)
    before = world.clock.now() + timedelta(days=1)
    world.repo.prune_history(before=before, memo_before=before)
    dump = world.repo.dump()
    (candidate,) = candidates

    with saved_state(dump) as restored:
        root, other = rows(restored, identities)
        assert root.delivery is None and other.delivery is None
        version = restored.repo.get_publication_content(identities[0])
        assert version is not None and version.grouped_stage_evidence is not None, (
            "the original shared message has retained editorial content"
        )
        witness = version.grouped_stage_evidence.render_witness
        assert witness is not None, "the first freeze captured the same-render witness"

        preview = preview_retained_grouped_update(
            restored.repo,
            identities[0],
            candidate,
            owner=witness.members[0],
            expected_content_id=version.content_id,
            expected_sha256=witness.text_sha256,
        )

        assert preview is not None and preview.html == candidate.text
        assert restored.repo.dump() == dump
