from collections.abc import Sequence
from datetime import date, timedelta
from typing import Literal

import pytest

from lexinform.grouped_update_preview import GroupedUpdateRender, preview_grouped_update
from lexinform.models import Bill, UpdateNews
from tests.scenario.test_group_remote_checkpoints import flush, queued_world, rows


@pytest.mark.parametrize("mode", ["off", "on"])
def test_shared_update_preview_preserves_exact_sibling_and_shared_bytes(
    mode: Literal["off", "on"], monkeypatch: pytest.MonkeyPatch
) -> None:
    world, identities = queued_world("updates", mode)
    original_render = world.publisher.render_updates
    captured: list[tuple[tuple[UpdateNews, ...], GroupedUpdateRender]] = []

    def observe(news: Sequence[UpdateNews], *, today: date) -> str:
        baseline = world.formatter.updates_with_witness(news, today=today)
        assert baseline is not None, "the fixture contains two exact stage update members"
        assert baseline.text == original_render(news, today=today)
        captured.append((tuple(news), baseline))
        return baseline.text

    monkeypatch.setattr(world.publisher, "render_updates", observe)
    flush(world, "updates", identities)

    (witnessed,) = captured
    news, baseline = witnessed
    assert world.publisher.sent[-1].text == baseline.text
    assert all(
        member.delivery is not None and member.delivery.group_text == baseline.text
        for member in rows(world, identities)
    )
    first, second = news
    stages = first.change.new_stages
    old_date = stages[0].date
    assert old_date is not None, "the observed stage has a date to correct"
    updated_stage = stages[0].model_copy(update={"date": old_date + timedelta(days=1)})
    corrected = first.model_copy(
        update={
            "change": first.change.model_copy(update={"new_stages": (updated_stage, *stages[1:])})
        }
    )
    candidate = world.formatter.updates_with_witness((corrected, second), today=baseline.today)
    assert candidate is not None and candidate.text != baseline.text, (
        "the date correction changes the original renderer's owned entry"
    )

    preview = preview_grouped_update(
        baseline, candidate, owner=baseline.members[0], expected_sha256=baseline.sha256
    )

    assert preview == candidate.text
    before, after = baseline.text.encode(), candidate.text.encode()
    for old, new in zip(baseline.ranges, candidate.ranges, strict=True):
        assert (old.kind, old.owners) == (new.kind, new.owners)
        if (old.kind, old.owners) != ("member", (baseline.members[0],)):
            assert before[old.start : old.end] == after[new.start : new.end]
    assert (
        preview_grouped_update(
            baseline, candidate, owner=baseline.members[1], expected_sha256=baseline.sha256
        )
        is None
    )
    assert (
        preview_grouped_update(
            baseline, candidate, owner=baseline.members[0], expected_sha256="0" * 64
        )
        is None
    )


def test_preview_refuses_shared_regrouping_and_unsupported_members(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, identities = queued_world("updates")
    captured: list[tuple[UpdateNews, ...]] = []
    render = world.publisher.render_updates

    def observe(news: Sequence[UpdateNews], *, today: date) -> str:
        captured.append(tuple(news))
        return render(news, today=today)

    monkeypatch.setattr(world.publisher, "render_updates", observe)
    flush(world, "updates", identities)
    ((first, second),) = captured
    today = world.clock.now().date()
    baseline = world.formatter.updates_with_witness((first, second), today=today)
    assert baseline is not None, "the original renderer provides the shared ownership map"

    def divergent_step(bill: Bill, day: date) -> str:
        return "A" if bill.number == first.bill.number else "B"

    monkeypatch.setattr(world.formatter, "_next_step_line", divergent_step)
    regrouped = world.formatter.updates_with_witness((first, second), today=today)
    assert regrouped is not None, "the candidate is independently renderable"
    assert tuple((span.kind, span.owners) for span in baseline.ranges) != tuple(
        (span.kind, span.owners) for span in regrouped.ranges
    )
    assert (
        preview_grouped_update(
            baseline, regrouped, owner=baseline.members[0], expected_sha256=baseline.sha256
        )
        is None
    )
    assert world.formatter.updates_with_witness((first, first), today=today) is None
    assert world.formatter.updates_with_witness((first, second, second), today=today) is None
