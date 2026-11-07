from collections.abc import Sequence
from datetime import date, timedelta
from typing import Literal
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from lexinform.errors import DeliveryUncertainError
from lexinform.grouped_update_preview import GroupedUpdateRender
from lexinform.models import ContentSnapshot, ContentVersion, UpdateNews
from lexinform.models.grouped_update_witness import (
    GroupedRenderOwner,
    GroupedRenderRange,
    GroupedRenderWitness,
)
from lexinform.ports import PublishResult
from lexinform.services import posting
from lexinform.services.content_inspection import inspect_content
from tests.checkpoint_state import saved_state
from tests.harness import World
from tests.scenario.test_group_remote_checkpoints import flush, queued_world, rows
from tests.scenario.test_grouped_stage_content_reader import attach, version_fixture


@pytest.fixture(autouse=True)
def isolate_manual_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(posting, "record_grouped_stage_content", lambda *args, **kwargs: None)


def witnessed_fixture(
    monkeypatch: pytest.MonkeyPatch, *, unknown: bool = False
) -> tuple[
    World, tuple[int, ...], ContentVersion, tuple[ContentSnapshot, ...], GroupedRenderWitness
]:
    world, identities = queued_world("updates")
    monkeypatch.setattr(posting, "record_grouped_stage_content", Mock())
    original_render = world.publisher.render_updates
    captures: list[GroupedUpdateRender] = []

    def observe(news: Sequence[UpdateNews], *, today: date) -> str:
        render = world.formatter.updates_with_witness(news, today=today)
        assert render is not None, "the fixed two-member fixture has one eligible render"
        assert render.text == original_render(news, today=today)
        captures.append(render)
        return render.text

    monkeypatch.setattr(world.publisher, "render_updates", observe)
    if unknown:

        def uncertain(text: str) -> PublishResult:
            raise DeliveryUncertainError("lost response")

        monkeypatch.setattr(world.publisher, "publish_updates", uncertain)
        with pytest.raises(DeliveryUncertainError):
            flush(world, "updates", identities)
    else:
        flush(world, "updates", identities)
    (render,) = captures
    first, second = render.members
    members = []
    for term, number, card, event in (first, second):
        assert card is not None and event is not None, "witness eligibility requires exact IDs"
        members.append(GroupedRenderOwner(term, number, card, event))
    first_owner, second_owner = members
    witness = GroupedRenderWitness(
        text_sha256=render.sha256,
        members=(first_owner, second_owner),
        today=render.today,
        attention=render.attention,
        ranges=tuple(
            GroupedRenderRange(
                fragment.kind,
                tuple(render.members.index(owner) for owner in fragment.owners),
                fragment.start,
                fragment.end,
            )
            for fragment in render.ranges
        ),
    )
    assert witness.bind(render.text, render.language) == render
    old, snapshots = version_fixture(world, identities)
    return world, identities, old, snapshots, witness


def with_witness(old: ContentVersion, witness: GroupedRenderWitness) -> ContentVersion:
    evidence = old.grouped_stage_evidence
    assert evidence is not None, "only the grouped profile can retain its fragment witness"
    return ContentVersion.model_validate(
        old.model_copy(
            update={
                "grouped_stage_evidence": evidence.model_copy(update={"render_witness": witness})
            }
        ).model_dump()
    )


@pytest.mark.parametrize("profile", ["old", "new", "unknown"])
def test_grouped_fragment_reader_preserves_old_and_new_history_after_two_restores(
    profile: Literal["old", "new", "unknown"], monkeypatch: pytest.MonkeyPatch
) -> None:
    world, identities, old, snapshots, witness = witnessed_fixture(
        monkeypatch, unknown=profile == "unknown"
    )
    assert '"render_witness"' not in old.model_dump_json()
    assert old == ContentVersion.model_validate_json(old.model_dump_json())
    version = old if profile == "old" else with_witness(old, witness)
    assert (version.content_id == old.content_id) == (profile == "old")
    attach(world, version, snapshots)
    if profile != "unknown":
        before = world.clock.now() + timedelta(days=1)
        world.repo.prune_history(before=before, memo_before=before)
        assert all(member.delivery is None for member in rows(world, identities))
    original = world.repo.dump()
    dump = original
    for _ in range(2):
        with saved_state(dump) as restored:
            root, _ = rows(restored, identities)
            assert restored.repo.get_publication_content(identities[0]) == version
            history = inspect_content(restored.repo, root)
            assert history is not None and history.integrity == "valid"
            assert history.current_id == (None if profile == "unknown" else version.content_id)
            dump = restored.repo.dump()
            assert dump == original


@pytest.mark.parametrize("change", ["sha", "order", "card", "road", "offset", "header"])
def test_grouped_fragment_reader_refuses_invalid_render_witness(
    change: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, version, _, witness = witnessed_fixture(monkeypatch)
    update: dict[str, object]
    if change == "sha":
        update = {"text_sha256": "0" * 64}
    elif change == "order":
        update = {"members": witness.members[::-1]}
    elif change == "card":
        first, second = witness.members
        update = {"members": (first._replace(card_message_id=999), second)}
    else:
        ranges = list(witness.ranges)
        if change == "offset":
            ranges[0] = ranges[0]._replace(end=1)
        else:
            index = next(index for index, span in enumerate(ranges) if span.kind == "road")
            ranges[index] = (
                ranges[index]._replace(owners=(0,))
                if change == "road"
                else ranges[index]._replace(kind="header", owners=())
            )
        update = {"ranges": tuple(ranges)}

    with pytest.raises(ValidationError, match="Grouped render witness"):
        with_witness(version, witness.model_copy(update=update))


@pytest.mark.parametrize("change", ["date", "bill_owner"])
def test_grouped_fragment_reader_checks_witness_against_frozen_database_parents(
    change: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, identities, version, snapshots, witness = witnessed_fixture(monkeypatch)
    first, second = witness.members
    update = (
        {"today": witness.today + timedelta(days=1)}
        if change == "date"
        else {"members": (first, second._replace(number="99999"))}
    )
    candidate = with_witness(version, witness.model_copy(update=update))

    with pytest.raises(ValueError, match="grouped_stage_render_witness_scope"):
        attach(world, candidate, snapshots)
    assert world.repo.get_publication_content(identities[0]) is None


def test_malformed_retained_witness_fails_restore_without_replacing_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, _, old, snapshots, witness = witnessed_fixture(monkeypatch)
    valid = with_witness(old, witness)
    attach(world, valid, snapshots)
    before = world.repo.dump()
    stale = with_witness(
        old, witness.model_copy(update={"today": witness.today + timedelta(days=1)})
    )
    broken = before.replace(valid.content_id, stale.content_id).replace(
        valid.model_dump_json().replace("'", "''"), stale.model_dump_json().replace("'", "''")
    )
    assert stale.content_id in broken

    with pytest.raises(ValueError, match="invalid_content_history"):
        world.repo.restore(broken)

    assert world.repo.dump() == before
