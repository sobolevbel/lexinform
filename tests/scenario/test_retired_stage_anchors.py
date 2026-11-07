from datetime import timedelta

import pytest

from lexinform.services.content_inspection import inspect_content
from tests.checkpoint_state import saved_state
from tests.scenario.test_commands import _commands_only
from tests.scenario.test_group_remote_checkpoints import flush, queued_world


@pytest.mark.parametrize("command", ["republish", "forget"])
def test_group_history_retains_only_the_confirmed_retired_anchor(command: str) -> None:
    world, identities = queued_world("updates")
    flush(world, "updates", identities)
    original = world.repo.get_publication_content(identities[0])
    assert original is not None and original.grouped_stage_evidence is not None
    member = original.grouped_stage_evidence.members[0]
    card = world.repo.publication_by_id(member.anchor_publication_id)
    assert card is not None and card.id is not None and card.message_id is not None
    live = world.repo.dump()
    bare_delete = live.replace("COMMIT;", f"DELETE FROM publications WHERE id={card.id};\nCOMMIT;")
    with pytest.raises(ValueError, match="invalid_content_history"):
        world.repo.restore(bare_delete)
    assert world.repo.dump() == live

    world.command(f"/{command} {card.number}")
    assert not _commands_only(world).commands_failed
    current = world.publication(card.number)
    if command == "republish":
        assert (
            current is not None and current.id != card.id and current.message_id != card.message_id
        )
    else:
        assert current is None
    assert world.repo.publication_by_id(card.id) is None
    assert not world.repo.card_aliases(card.channel_id, card.message_id)
    cutoff = world.clock.now() + timedelta(days=1)
    world.repo.prune_history(before=cutoff, memo_before=cutoff)
    sql = world.repo.dump()
    tampered = sql.replace(
        "COMMIT;",
        f"UPDATE retired_stage_anchors SET message_id={card.message_id + 10}"
        f" WHERE id={card.id};\nCOMMIT;",
    )
    for _ in range(2):
        with saved_state(sql) as restored:
            assert restored.repo.dump() == sql
            root = restored.repo.publication_by_id(identities[0])
            assert root is not None
            history = inspect_content(restored.repo, root)
            assert history is not None and history.current_id == original.content_id
            with pytest.raises(ValueError, match="invalid_content_history"):
                restored.repo.restore(tampered)
            assert restored.repo.dump() == sql
