import datetime as dt
from pathlib import Path

import pytest

from lexinform.models import Bill, PrintInfo, Publication, PublicationKind, PublicationStatus
from lexinform.ports import PublishResult
from tests.harness import TERM, act
from tests.scenario.test_card_remote_checkpoints import Mode, connect
from tests.scenario.test_single_card_revision_producer import card, operation, ready


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
def test_command_refresh_and_normal_tracking_share_one_single_edit_attempt_per_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: Mode
) -> None:
    world = ready(mode)
    connect(world, tmp_path)
    original = world.publisher.publish_prepared
    attempted: list[int] = []

    def inspect(saved: Publication) -> PublishResult:
        if saved.kind is PublicationKind.MESSAGE_PART:
            assert saved.id is not None, "physical card edits have their saved identity"
            attempted.append(saved.id)
        return original(saved)

    monkeypatch.setattr(world.publisher, "publish_prepared", inspect)
    baseline = card(world)
    world.publisher.fail_on.add("3039")
    world.command("/refresh 3039")

    first = world.run(discover=False, analyze=False)

    saved = operation(world)
    assert attempted == [saved.id]
    assert saved.status is PublicationStatus.FAILED and saved.attempts == 1
    assert first.cards_refreshed == 0
    assert card(world) == baseline
    world.publisher.fail_on.clear()

    second = world.run(discover=False, analyze=False)
    third = world.run(discover=False, analyze=False)

    assert attempted == [saved.id, saved.id]
    assert operation(world).status is PublicationStatus.SENT
    assert second.cards_refreshed == 1 and third.cards_refreshed == 0
    assert card(world).rendered_sha256 == saved.rendered_sha256


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
def test_print_fetch_crossing_warsaw_midnight_keeps_card_digest_and_html_on_one_saved_day(
    monkeypatch: pytest.MonkeyPatch, mode: Mode
) -> None:
    world = ready(mode)
    world.clock.current = dt.datetime(2026, 9, 19, 21, 59, 30, tzinfo=dt.UTC)
    before, after = dt.date(2026, 9, 19), dt.date(2026, 9, 20)
    world.repo.save_act(TERM, "3039", act(entry_into_force=after))
    original_digest = world.publisher.card_digest
    original_print = world.gateway.get_print
    digested: list[dt.date | None] = []
    shifted: list[bool] = []

    def digest(bill: Bill, *, today: dt.date | None = None) -> str:
        digested.append(today)
        return original_digest(bill, today=today)

    def print_info(term: int, number: str) -> PrintInfo:
        if digested and not shifted:
            world.clock.advance(minutes=2)
            shifted.append(True)
        return original_print(term, number)

    monkeypatch.setattr(world.publisher, "card_digest", digest)
    monkeypatch.setattr(world.gateway, "get_print", print_info)

    result = world.container.tracking_service(dry_run=False).check_bill(world.bill("3039"))

    saved = operation(world)
    assert result.cards_refreshed == 1
    assert digested == [before]
    assert shifted == [True]
    assert saved.delivery is not None, "the refresh saves the day used for its immutable card"
    assert saved.delivery.today == before
    bill = world.bill("3039")
    assert saved.rendered_sha256 == original_digest(bill, today=before)
    assert original_digest(bill, today=before) != original_digest(bill, today=after)
    assert (
        saved.delivery.rendered_text
        == world.publisher.render_card_parts(
            bill, original_print(TERM, "3039"), minimum=1, today=before
        )[0]
    )
